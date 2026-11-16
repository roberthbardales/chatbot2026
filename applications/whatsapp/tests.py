from unittest.mock import patch
import uuid
from datetime import timedelta
import io
import json
import base64
from urllib.error import HTTPError, URLError

from django.test import Client, SimpleTestCase, TestCase, override_settings
from django.utils import timezone
from django.urls import reverse

from applications.users.models import User
from applications.chat.providers import ProviderError
from applications.chat.models import Product
from applications.chat.services import InventoryAnswer
from applications.chat.limits import LimitExceeded, identity, principal_slot
from applications.chat.models import UsageBucket
from PIL import Image

from .models import Outbox, WhatsAppContact, WhatsAppMessage
from .meta_api import WhatsAppAPIError, send_text_message
from .services import (PROCESSING_ERROR, _finish_outbox, _finish_processing, _recover_abandoned_messages,
                       enqueue_message, enqueue_outbox, process_outbox, process_pending_messages,
                       split_whatsapp_text)


@override_settings(WHATSAPP_DEFAULT_SOURCE='google_sheets', WHATSAPP_RATE_PER_CONTACT=20,
                   CHAT_RATE_WINDOW_SECONDS=60)
class WhatsAppQuotaTests(TestCase):
    def setUp(self):
        self.contact = WhatsAppContact.objects.create(phone_number='51922221111')
        self.answer = patch('applications.whatsapp.services.answer_sheet_message', return_value='Respuesta').start()
        self.send = patch('applications.whatsapp.services.send_text_message').start()
        self.addCleanup(patch.stopall)

    def incoming(self, message_id):
        enqueue_message(self.contact.phone_number, '', message_id, 'Consulta')
        return WhatsAppMessage.objects.get(message_id=message_id)

    def test_budget_limit_defers_without_error_message_or_spent_processing_attempt(self):
        message = self.incoming('deferred')
        self.answer.side_effect = LimitExceeded('Cupo diario agotado', 3600, 'daily_budget')
        self.assertEqual(process_pending_messages(force=True), 0)
        message.refresh_from_db()
        self.assertEqual(message.status, WhatsAppMessage.PENDING)
        self.assertIsNotNone(message.retry_after_at)
        self.assertIsNone(message.processing_token)
        self.assertEqual(message.processing_attempts, 0)
        self.assertFalse(Outbox.objects.exists())
        process_pending_messages(force=True)
        self.answer.assert_called_once()
        self.send.assert_not_called()

    def test_deferred_message_resumes_when_wait_expires(self):
        message = self.incoming('resume')
        self.answer.side_effect = LimitExceeded('Ocupado', 60, 'ai_concurrency_limit')
        process_pending_messages(force=True)
        message.refresh_from_db()
        self.answer.side_effect = None
        with patch('applications.whatsapp.services.timezone.now', return_value=message.retry_after_at):
            self.assertEqual(process_pending_messages(force=True), 1)
        message.refresh_from_db()
        self.assertEqual(message.status, WhatsAppMessage.PROCESSED)
        self.assertIsNone(message.retry_after_at)
        self.assertEqual(Outbox.objects.get().content, 'Respuesta')

    @override_settings(WHATSAPP_RATE_PER_CONTACT=1)
    def test_contact_rate_limit_is_applied_before_ai(self):
        self.incoming('first-rate')
        process_pending_messages(force=True)
        second = self.incoming('second-rate')
        process_pending_messages(force=True)
        second.refresh_from_db()
        self.assertEqual(second.status, WhatsAppMessage.PENDING)
        self.assertIsNotNone(second.retry_after_at)
        self.answer.assert_called_once()

    def test_contact_concurrency_defers_without_calling_ai(self):
        message = self.incoming('busy-contact')
        with principal_slot(identity('whatsapp', self.contact.phone_number)):
            process_pending_messages(force=True)
        message.refresh_from_db()
        self.assertEqual(message.status, WhatsAppMessage.PENDING)
        self.assertIsNotNone(message.retry_after_at)
        self.answer.assert_not_called()

    def test_human_mode_does_not_consume_bot_rate(self):
        self.contact.mode = WhatsAppContact.HUMAN
        self.contact.save()
        self.incoming('human-quota')
        process_pending_messages(force=True)
        self.assertFalse(UsageBucket.objects.exists())
        self.answer.assert_not_called()

    def test_oversized_ai_context_is_failed_not_retried_forever(self):
        message = self.incoming('large-context')
        self.answer.side_effect = LimitExceeded('Contexto demasiado grande', 60, 'context_limit')
        process_pending_messages(force=True)
        message.refresh_from_db()
        self.assertEqual(message.status, WhatsAppMessage.FAILED)
        self.assertIsNone(message.retry_after_at)
        self.assertEqual(Outbox.objects.count(), 1)
        self.send.assert_not_called()


@override_settings(WHATSAPP_DEFAULT_SOURCE='google_sheets')
class WhatsAppInventoryContextTests(TestCase):
    def setUp(self):
        self.contact = WhatsAppContact.objects.create(phone_number='51922220000')
        self.product = Product.objects.create(sku='VAL-01', name='Válvula bronce 1/2 pulgada', price='25', stock=8)
        self.generate = patch('applications.chat.services.generate_response', return_value='{}').start()
        self.send = patch('applications.whatsapp.services.send_text_message').start()
        self.addCleanup(patch.stopall)

    def ask(self, text, contact=None):
        contact = contact or self.contact
        enqueue_message(contact.phone_number, '', str(uuid.uuid4()), text)
        process_pending_messages(force=True)
        return contact.messages.filter(is_from_customer=False).order_by('-created_at', '-pk').first()

    def test_follow_up_reuses_only_committed_sku_and_fresh_inventory(self):
        first = self.ask('SKU VAL-01')
        self.assertEqual(first.inventory_context, {'selected_sku': 'VAL-01'})
        Product.objects.filter(pk=self.product.pk).update(stock=4)
        follow_up = self.ask('¿Y cuántas unidades hay?')
        self.assertIn('hay 4', follow_up.text)
        self.assertEqual(follow_up.inventory_context, {'selected_sku': 'VAL-01'})
        self.send.assert_not_called()

    def test_contacts_do_not_share_context(self):
        self.ask('SKU VAL-01')
        other = WhatsAppContact.objects.create(phone_number='51922220001')
        result = self.ask('¿Y el stock?', contact=other)
        self.assertIn('No encontré', result.text)
        self.assertEqual(result.inventory_context, {})

    def test_ambiguity_and_option_selection_work_on_whatsapp(self):
        Product.objects.create(sku='VAL-02', name='Válvula bronce 3/4 pulgada', stock=15)
        first = self.ask('válvula bronce')
        self.assertIn('candidates', first.inventory_context)
        self.assertNotIn('hay 15', first.text)
        self.assertIn('hay 15', self.ask('la segunda').text)

    def test_manual_message_resets_automatic_product_context(self):
        self.ask('SKU VAL-01')
        WhatsAppMessage.objects.create(contact=self.contact, message_id='manual', text='Voy a revisar', is_from_customer=False)
        self.assertIn('No encontré', self.ask('¿Y el precio?').text)

    def test_old_attempt_cannot_persist_speculative_context(self):
        message = WhatsAppMessage.objects.create(
            contact=self.contact, message_id='stale-context', status=WhatsAppMessage.PROCESSING,
            processing_token=uuid.uuid4(),
        )
        answer = InventoryAnswer('Respuesta antigua', {'selected_sku': 'VAL-01'})
        self.assertFalse(_finish_processing([message], uuid.uuid4(), answer))
        self.assertFalse(self.contact.messages.filter(is_from_customer=False).exists())


@override_settings(WHATSAPP_DEFAULT_SOURCE='google_sheets')
class WhatsAppHumanModeTests(TestCase):
    def setUp(self):
        self.contact = WhatsAppContact.objects.create(phone_number='51933333333', mode=WhatsAppContact.HUMAN)
        self.download = patch('applications.whatsapp.services.download_media_data').start()
        self.answer = patch('applications.whatsapp.services.answer_sheet_message', return_value='Respuesta IA').start()
        self.send = patch('applications.whatsapp.services.send_text_message').start()
        self.addCleanup(patch.stopall)

    def incoming(self, image=False):
        enqueue_message(self.contact.phone_number, '', 'human-test', 'Esta es la pieza',
                        'media-123' if image else '', 'image/jpeg' if image else '')
        return WhatsAppMessage.objects.get(message_id='human-test')

    def take_over(self):
        WhatsAppContact.objects.filter(pk=self.contact.pk).update(mode=WhatsAppContact.HUMAN)

    def assert_silent_processed(self, message):
        message.refresh_from_db()
        self.assertEqual(message.status, WhatsAppMessage.PROCESSED)
        self.assertEqual(message.response, '')
        self.assertIsNone(message.processing_token)
        self.assertIsNone(message.processing_started_at)
        self.assertIsNotNone(message.processed_at)
        self.assertFalse(Outbox.objects.exists())
        self.assertFalse(WhatsAppMessage.objects.filter(is_from_customer=False).exists())
        self.send.assert_not_called()

    def test_human_text_is_saved_without_ai(self):
        message = self.incoming()
        self.assertEqual(process_pending_messages(force=True), 1)
        self.assert_silent_processed(message)
        self.answer.assert_not_called()
        self.download.assert_not_called()

    def test_human_image_is_not_downloaded_even_when_download_would_fail(self):
        self.download.side_effect = WhatsAppAPIError('download failed')
        message = self.incoming(image=True)
        process_pending_messages(force=True)
        self.assert_silent_processed(message)
        self.download.assert_not_called()
        self.answer.assert_not_called()
        self.assertEqual(message.media_id, 'media-123')
        self.assertEqual(message.media_type, 'image/jpeg')
        self.assertEqual(message.text, 'Esta es la pieza')

    def test_mode_change_during_ai_discards_response(self):
        self.contact.mode = WhatsAppContact.AI
        self.contact.save()
        message = self.incoming()

        def answer(*args, **kwargs):
            self.take_over()
            return 'Respuesta que no debe enviarse'

        self.answer.side_effect = answer
        process_pending_messages(force=True)
        self.answer.assert_called_once()
        self.assert_silent_processed(message)

    def test_mode_change_during_ai_suppresses_provider_and_unexpected_errors(self):
        message = self.incoming()
        for error in [ProviderError('offline'), RuntimeError('unexpected')]:
            with self.subTest(error=type(error).__name__):
                WhatsAppContact.objects.filter(pk=self.contact.pk).update(mode=WhatsAppContact.AI)
                WhatsAppMessage.objects.filter(pk=message.pk).update(status=WhatsAppMessage.PENDING)

                def answer(*args, **kwargs):
                    self.take_over()
                    raise error

                self.answer.side_effect = answer
                process_pending_messages(force=True)
                self.assert_silent_processed(message)

    def test_mode_change_during_failed_download_suppresses_error(self):
        self.contact.mode = WhatsAppContact.AI
        self.contact.save()
        message = self.incoming(image=True)

        def download(*args, **kwargs):
            self.take_over()
            raise WhatsAppAPIError('download failed')

        self.download.side_effect = download
        process_pending_messages(force=True)
        self.assert_silent_processed(message)
        self.answer.assert_not_called()

    def test_mode_change_during_successful_download_skips_ai(self):
        self.contact.mode = WhatsAppContact.AI
        self.contact.save()
        message = self.incoming(image=True)

        def download(*args, **kwargs):
            self.take_over()
            return 'data:image/jpeg;base64,abc'

        self.download.side_effect = download
        process_pending_messages(force=True)
        self.assert_silent_processed(message)
        self.answer.assert_not_called()

    def test_manual_message_in_human_mode_still_queues_and_sends(self):
        manager = User.objects.create_user('human-manager@test.com', occupation=User.EMPLEADO)
        self.client.force_login(manager)
        response = self.client.post(
            reverse('send-manual-message', args=[self.contact.pk]),
            data=json.dumps({'content': 'Voy a revisar la pieza.'}), content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        self.send.assert_not_called()
        self.assertEqual(process_outbox(), 1)
        self.send.assert_called_once_with(self.contact.phone_number, 'Voy a revisar la pieza.')

    def test_ai_image_processing_still_works(self):
        self.contact.mode = WhatsAppContact.AI
        self.contact.save()
        message = self.incoming(image=True)
        self.download.return_value = 'data:image/jpeg;base64,abc'
        process_pending_messages(force=True)
        self.download.assert_called_once_with('media-123')
        self.assertEqual(self.answer.call_args.kwargs['image_data_url'], 'data:image/jpeg;base64,abc')
        self.assertEqual(Outbox.objects.get().content, 'Respuesta IA')
        message.refresh_from_db()
        self.assertEqual(message.response, 'Respuesta IA')


class WhatsAppDashboardTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.manager = User.objects.create_user('panel@test.com', occupation=User.EMPLEADO)
        cls.regular = User.objects.create_user('panel-user@test.com', occupation=User.USUARIO)

    def setUp(self):
        self.client.force_login(self.manager)
        self.contact = WhatsAppContact.objects.create(phone_number='51912345678', display_name='Ana Pérez')

    def test_dashboard_has_controls_and_static_script(self):
        response = self.client.get(reverse('dashboard'))
        for text in ['Nombre o teléfono', 'Cargar mensajes anteriores', 'Encolar respuesta',
                     'toggle-mode', 'whatsapp/dashboard.js']:
            self.assertContains(response, text)
        self.assertIn('no-store', response['Cache-Control'])

    def test_contact_search_by_name_or_phone(self):
        WhatsAppContact.objects.create(phone_number='51111111111', display_name='Otro')
        for query in ['Pérez', '12345678']:
            with self.subTest(query=query):
                data = self.client.get(reverse('conversations'), {'q': query}).json()
                self.assertEqual([item['id'] for item in data['conversations']], [self.contact.pk])
        self.assertEqual(self.client.get(reverse('conversations'), {'q': 'no-coincide'}).json()['conversations'], [])

    def test_contacts_can_be_paginated(self):
        WhatsAppContact.objects.bulk_create([
            WhatsAppContact(phone_number=f'520{index:08d}') for index in range(101)
        ])
        first = self.client.get(reverse('conversations')).json()
        second = self.client.get(reverse('conversations'), {'offset': first['next_offset']}).json()
        self.assertTrue(first['has_more'])
        self.assertFalse(second['has_more'])
        ids = [item['id'] for item in first['conversations'] + second['conversations']]
        self.assertEqual(len(ids), 102)
        self.assertEqual(len(set(ids)), 102)

    def test_history_cursor_handles_timestamp_ties_and_new_incoming_messages(self):
        messages = WhatsAppMessage.objects.bulk_create([
            WhatsAppMessage(contact=self.contact, message_id=f'page-{i}', text=str(i)) for i in range(5)
        ])
        WhatsAppMessage.objects.filter(contact=self.contact).update(created_at=timezone.now())
        url = reverse('conversation-messages', args=[self.contact.pk])
        first = self.client.get(url, {'limit': 2}).json()
        WhatsAppMessage.objects.create(contact=self.contact, message_id='new-arrival', text='Nuevo')
        second = self.client.get(url, {'limit': 2, 'before': first['next_before']}).json()
        third = self.client.get(url, {'limit': 2, 'before': second['next_before']}).json()
        self.assertEqual([m['id'] for m in first['messages']], [m.pk for m in messages[3:]])
        self.assertEqual([m['id'] for m in second['messages']], [m.pk for m in messages[1:3]])
        self.assertEqual([m['id'] for m in third['messages']], [messages[0].pk])
        self.assertFalse(third['has_more'])

    def test_invalid_pagination_is_rejected(self):
        url = reverse('conversation-messages', args=[self.contact.pk])
        for params in [{'limit': 'x'}, {'limit': '0'}, {'limit': '501'}, {'before': 'tampered'}]:
            with self.subTest(params=params):
                self.assertEqual(self.client.get(url, params).status_code, 400)
        for offset in ['-1', 'bad']:
            self.assertEqual(self.client.get(reverse('conversations'), {'offset': offset}).status_code, 400)

    def test_cursor_cannot_be_reused_for_another_contact(self):
        for i in range(2):
            WhatsAppMessage.objects.create(contact=self.contact, message_id=f'cursor-{i}')
        cursor = self.client.get(reverse('conversation-messages', args=[self.contact.pk]), {'limit': 1}).json()['next_before']
        other = WhatsAppContact.objects.create(phone_number='51999999999')
        self.assertEqual(self.client.get(reverse('conversation-messages', args=[other.pk]), {'before': cursor}).status_code, 400)

    @patch('applications.whatsapp.views.download_media_data')
    def test_all_dashboard_endpoints_require_manager_permissions(self, download):
        message = WhatsAppMessage.objects.create(contact=self.contact, message_id='private-image', media_id='media')
        urls = [reverse('dashboard'), reverse('conversations'), reverse('connection-status'),
                reverse('conversation-messages', args=[self.contact.pk]),
                reverse('message-media', args=[self.contact.pk, message.pk])]
        for user in [None, self.regular]:
            self.client.logout()
            if user:
                self.client.force_login(user)
            for url in urls:
                with self.subTest(user=user, url=url):
                    self.assertEqual(self.client.get(url).status_code, 302)
            self.assertEqual(self.client.post(reverse('change-mode', args=[self.contact.pk]),
                                              data='{"mode":"human"}', content_type='application/json').status_code, 302)
            self.assertEqual(self.client.post(reverse('send-manual-message', args=[self.contact.pk]),
                                              data='{"content":"Hola"}', content_type='application/json').status_code, 302)
        download.assert_not_called()

    def test_mode_change_and_manual_send_validate_json_types(self):
        for payload in ['[]', 'null', '{"mode":[]}', '{"mode":"invalid"}']:
            self.assertEqual(self.client.post(reverse('change-mode', args=[self.contact.pk]),
                                              data=payload, content_type='application/json').status_code, 400)
        for payload in ['[]', 'null', '{"content":45}', '{"content":"  "}', json.dumps({'content': 'x' * 50001})]:
            self.assertEqual(self.client.post(reverse('send-manual-message', args=[self.contact.pk]),
                                              data=payload, content_type='application/json').status_code, 400)
        response = self.client.post(reverse('change-mode', args=[self.contact.pk]),
                                    data='{"mode":"human"}', content_type='application/json')
        self.assertEqual(response.status_code, 200)
        self.contact.refresh_from_db()
        self.assertEqual(self.contact.mode, WhatsAppContact.HUMAN)
        self.assertFalse(Outbox.objects.exists())

    def test_manual_actions_require_csrf(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.manager)
        for name, payload in [('send-manual-message', {'content': 'Hola'}), ('change-mode', {'mode': 'human'})]:
            response = client.post(reverse(name, args=[self.contact.pk]), data=json.dumps(payload), content_type='application/json')
            self.assertEqual(response.status_code, 403)
        client.get(reverse('dashboard'))
        token = client.cookies['csrftoken'].value
        response = client.post(reverse('send-manual-message', args=[self.contact.pk]),
                               data='{"content":"Hola"}', content_type='application/json', HTTP_X_CSRFTOKEN=token)
        self.assertEqual(response.status_code, 200)

    def test_delivery_errors_and_retry_dates_are_available(self):
        message = WhatsAppMessage.objects.create(contact=self.contact, message_id='error-details', is_from_customer=False)
        retry = timezone.now() + timedelta(seconds=30)
        Outbox.objects.create(contact=self.contact, message=message, content='Hola', attempts=1,
                              last_error='temporary error', next_attempt_at=retry)
        data = self.client.get(reverse('conversation-messages', args=[self.contact.pk])).json()
        part = data['messages'][0]['delivery_parts'][0]
        self.assertEqual(part['status'], 'pending')
        self.assertEqual(part['last_error'], 'temporary error')
        self.assertEqual(part['next_attempt_at'], retry.isoformat())


class WhatsAppMediaTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.manager = User.objects.create_user('media@test.com', occupation=User.EMPLEADO)

    def setUp(self):
        self.client.force_login(self.manager)
        self.contact = WhatsAppContact.objects.create(phone_number='51900000001')
        self.message = WhatsAppMessage.objects.create(contact=self.contact, message_id='image', media_id='meta-image', media_type='image/png')
        self.url = reverse('message-media', args=[self.contact.pk, self.message.pk])

    @patch('applications.whatsapp.views.download_media_data')
    def test_image_is_reencoded_and_not_cached(self, download):
        output = io.BytesIO()
        Image.new('RGB', (10, 10), 'red').save(output, format='PNG')
        download.return_value = 'data:image/png;base64,' + base64.b64encode(output.getvalue()).decode()
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'image/jpeg')
        self.assertEqual(response['Cache-Control'], 'private, no-store')
        self.assertEqual(response['X-Content-Type-Options'], 'nosniff')
        self.assertEqual(Image.open(io.BytesIO(response.content)).format, 'JPEG')
        download.assert_called_once_with('meta-image')

    @patch('applications.whatsapp.views.download_media_data')
    def test_non_images_and_invalid_base64_are_rejected(self, download):
        for value in ['data:image/svg+xml;base64,PHN2Zz4=', 'data:image/png;base64,invalid!', 'data:image/jpeg;base64,aGVsbG8=']:
            download.return_value = value
            self.assertEqual(self.client.get(self.url).status_code, 502)

    @patch('applications.whatsapp.views.download_media_data', side_effect=WhatsAppAPIError('expired'))
    def test_expired_media_has_controlled_error(self, download):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 502)
        self.assertIn('caducado', response.json()['error'])

    @patch('applications.whatsapp.views.download_media_data')
    def test_media_is_scoped_to_its_contact(self, download):
        other = WhatsAppContact.objects.create(phone_number='51900000002')
        self.assertEqual(self.client.get(reverse('message-media', args=[other.pk, self.message.pk])).status_code, 404)
        self.message.media_id = ''
        self.message.save()
        self.assertEqual(self.client.get(self.url).status_code, 404)
        download.assert_not_called()


class WhatsAppTextSplitTests(SimpleTestCase):
    def test_short_and_exact_limit_are_unchanged(self):
        for text in ['Hola', 'x' * 4096]:
            self.assertEqual(split_whatsapp_text(text), [text])

    def test_splitting_is_lossless_and_headers_fit(self):
        for text in ['x' * 4097, ('Párrafo con palabras.\n\nOtra línea.\n' * 500),
                     '😀' * 9000, 'x' * 50000, ('a' * 2100 + ' ' * 2000) * 4]:
            with self.subTest(length=len(text)):
                parts = split_whatsapp_text(text)
                self.assertTrue(all(len(part) <= 4096 for part in parts))
                self.assertEqual(''.join(part.split('\n', 1)[1] for part in parts), text)
                for index, part in enumerate(parts, 1):
                    self.assertTrue(part.startswith(f'Parte {index}/{len(parts)}\n'))

    def test_paragraph_boundary_is_preferred(self):
        text = 'a' * 3000 + '\n\n' + 'b' * 3000
        self.assertTrue(split_whatsapp_text(text)[0].endswith('\n\n'))


@override_settings(WHATSAPP_OUTBOX_MAX_ATTEMPTS=5, WHATSAPP_OUTBOX_RETRY_BASE_SECONDS=30)
class WhatsAppMultipartTests(TestCase):
    def setUp(self):
        self.contact = WhatsAppContact.objects.create(phone_number='51944444444')
        self.message = WhatsAppMessage.objects.create(
            contact=self.contact, message_id='multipart', text='x' * 9000,
            is_from_customer=False, status=WhatsAppMessage.PROCESSED,
        )
        self.send = patch('applications.whatsapp.services.send_text_message').start()
        self.addCleanup(patch.stopall)

    def queue(self):
        return enqueue_outbox(self.contact, self.message, self.message.text)

    def test_parts_are_sent_in_order_in_one_invocation(self):
        parts = self.queue()
        self.assertEqual(len(parts), 3)
        self.assertEqual(process_outbox(), 3)
        self.assertEqual([call.args[1] for call in self.send.call_args_list], [part.content for part in parts])
        self.assertEqual(Outbox.objects.filter(sent=True).count(), 3)
        self.message.refresh_from_db()
        self.assertEqual(self.message.text, 'x' * 9000)

    def test_partial_enqueue_is_rolled_back(self):
        create = Outbox.objects.create
        calls = 0

        def fail_on_second_part(**kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError('database failure')
            return create(**kwargs)

        with patch('applications.whatsapp.services.Outbox.objects.create', side_effect=fail_on_second_part):
            with self.assertRaises(RuntimeError):
                self.queue()
        self.assertFalse(Outbox.objects.exists())

    def test_active_predecessor_blocks_following_parts(self):
        parts = self.queue()
        Outbox.objects.filter(pk=parts[0].pk).update(locked_at=timezone.now(), lock_token=uuid.uuid4())
        self.assertEqual(process_outbox(), 0)
        self.send.assert_not_called()

    @override_settings(WHATSAPP_DEFAULT_SOURCE='google_sheets')
    @patch('applications.whatsapp.services.answer_sheet_message', return_value='y' * 9000)
    def test_automated_long_response_uses_shared_splitter(self, answer):
        enqueue_message(self.contact.phone_number, '', 'long-question', 'Consulta')
        process_pending_messages(force=True)
        self.assertEqual(Outbox.objects.count(), 3)
        assistant = WhatsAppMessage.objects.filter(is_from_customer=False).exclude(pk=self.message.pk).get()
        self.assertEqual(assistant.text, 'y' * 9000)
        self.send.assert_not_called()

    def test_failed_second_part_waits_then_resumes_without_repeating_first(self):
        parts = self.queue()
        self.send.side_effect = [None, WhatsAppAPIError('temporary')]
        self.assertEqual(process_outbox(), 1)
        self.assertEqual(self.send.call_count, 2)
        parts[1].refresh_from_db()
        self.send.side_effect = None
        with patch('applications.whatsapp.services.timezone.now', return_value=parts[1].next_attempt_at):
            self.assertEqual(process_outbox(), 2)
        self.assertEqual([call.args[1] for call in self.send.call_args_list],
                         [parts[0].content, parts[1].content, parts[1].content, parts[2].content])

    def test_permanent_failure_marks_following_parts_failed(self):
        parts = self.queue()
        self.send.side_effect = [None, WhatsAppAPIError('invalid', code=131009)]
        process_outbox()
        parts[2].refresh_from_db()
        self.assertIsNotNone(parts[2].failed_at)
        self.assertIn('Dependency failed', parts[2].last_error)
        self.assertEqual(self.send.call_count, 2)

    def test_exhausted_part_marks_dependencies_failed(self):
        parts = self.queue()
        Outbox.objects.filter(pk=parts[0].pk).update(attempts=5)
        process_outbox()
        self.assertEqual(Outbox.objects.filter(failed_at__isnull=False).count(), 3)
        self.send.assert_not_called()

    def test_other_client_advances_while_part_is_waiting(self):
        parts = self.queue()
        Outbox.objects.filter(pk=parts[0].pk).update(next_attempt_at=timezone.now() + timedelta(minutes=5))
        other = WhatsAppContact.objects.create(phone_number='51955555555')
        other_message = WhatsAppMessage.objects.create(contact=other, message_id='other', text='Hola')
        enqueue_outbox(other, other_message, 'Hola')
        self.assertEqual(process_outbox(), 1)
        self.send.assert_called_once_with(other.phone_number, 'Hola')

    def test_legacy_long_row_is_split_before_send(self):
        Outbox.objects.create(contact=self.contact, message=self.message, content=self.message.text)
        self.assertEqual(process_outbox(), 3)
        self.assertTrue(all(len(call.args[1]) <= 4096 for call in self.send.call_args_list))
        self.assertEqual(''.join(call.args[1].split('\n', 1)[1] for call in self.send.call_args_list), self.message.text)

    def test_legacy_active_lock_and_sent_rows_are_not_split(self):
        for kwargs in [{'locked_at': timezone.now()}, {'sent': True}]:
            Outbox.objects.create(contact=self.contact, message=self.message, content=self.message.text, **kwargs)
        self.assertEqual(process_outbox(), 0)
        self.assertEqual(Outbox.objects.count(), 2)
        self.send.assert_not_called()

    def test_manual_long_message_is_queued_without_sending(self):
        manager = User.objects.create_user('multipart-manager@test.com', occupation=User.EMPLEADO)
        self.client.force_login(manager)
        response = self.client.post(reverse('send-manual-message', args=[self.contact.pk]),
                                    data=json.dumps({'content': self.message.text}), content_type='application/json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Outbox.objects.count(), 3)
        self.send.assert_not_called()

    def test_history_exposes_partial_progress(self):
        parts = self.queue()
        Outbox.objects.filter(pk=parts[0].pk).update(sent=True)
        Outbox.objects.filter(pk__in=[part.pk for part in parts[1:]]).update(failed_at=timezone.now())
        manager = User.objects.create_user('progress@test.com', occupation=User.EMPLEADO)
        self.client.force_login(manager)
        result = self.client.get(reverse('conversation-messages', args=[self.contact.pk])).json()
        self.assertEqual(result['messages'][0]['sending'], {'total_parts': 3, 'accepted_parts': 1, 'failed_parts': 2,
                                                           'sent_parts': 0, 'delivered_parts': 0, 'read_parts': 0})


class WhatsAppHistoryTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.manager = User.objects.create_user('history-manager@test.com', occupation=User.EMPLEADO)
        cls.regular_user = User.objects.create_user('history-user@test.com', occupation=User.USUARIO)

    def setUp(self):
        self.contact = WhatsAppContact.objects.create(phone_number='51911111111')
        self.url = reverse('conversation-messages', args=[self.contact.pk])
        self.client.force_login(self.manager)

    def create_messages(self, count, contact=None):
        contact = contact or self.contact
        return WhatsAppMessage.objects.bulk_create([
            WhatsAppMessage(
                contact=contact, message_id=f'history-{contact.pk}-{index}',
                text=f'Mensaje {index}', is_from_customer=index % 2 == 0,
                status=WhatsAppMessage.PROCESSED,
            ) for index in range(count)
        ])

    def test_empty_history(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['messages'], [])

    def test_less_than_limit_preserves_order_and_response_fields(self):
        messages = self.create_messages(3)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual([item['id'] for item in data['messages']], [item.pk for item in messages])
        self.assertEqual(data['conversation'], {
            'id': self.contact.pk, 'phone': self.contact.phone_number,
            'name': self.contact.display_name, 'mode': self.contact.mode,
        })
        self.assertEqual(data['messages'][1], {
            'id': messages[1].pk, 'role': 'assistant', 'content': 'Mensaje 1',
            'media_type': '', 'status': WhatsAppMessage.PROCESSED,
            'retry_after_at': None,
            'created_at': messages[1].created_at.isoformat(),
            'sending': None,
            'delivery_parts': [],
            'media_url': None,
        })

    def test_exactly_five_hundred_returns_every_message(self):
        messages = self.create_messages(500)
        result = self.client.get(self.url).json()['messages']
        self.assertEqual([item['id'] for item in result], [item.pk for item in messages])

    def test_large_history_returns_latest_five_hundred_without_deleting_old_messages(self):
        messages = self.create_messages(700)
        result = self.client.get(self.url).json()['messages']
        self.assertEqual([item['id'] for item in result], [item.pk for item in messages[200:]])
        self.assertEqual(self.contact.messages.count(), 700)

    def test_timestamp_controls_order_before_id(self):
        messages = self.create_messages(3)
        now = timezone.now()
        WhatsAppMessage.objects.filter(pk=messages[0].pk).update(created_at=now)
        WhatsAppMessage.objects.filter(pk=messages[1].pk).update(created_at=now - timedelta(minutes=2))
        WhatsAppMessage.objects.filter(pk=messages[2].pk).update(created_at=now - timedelta(minutes=1))
        result = self.client.get(self.url).json()['messages']
        self.assertEqual([item['id'] for item in result], [messages[1].pk, messages[2].pk, messages[0].pk])

    def test_identical_timestamps_use_id_for_selection_and_order(self):
        messages = self.create_messages(501)
        self.contact.messages.update(created_at=timezone.now())
        first = self.client.get(self.url).json()['messages']
        second = self.client.get(self.url).json()['messages']
        self.assertEqual([item['id'] for item in first], [item.pk for item in messages[1:]])
        self.assertEqual(first, second)

    def test_other_contact_messages_are_excluded(self):
        messages = self.create_messages(2)
        other = WhatsAppContact.objects.create(phone_number='51922222222')
        self.create_messages(3, contact=other)
        result = self.client.get(self.url).json()['messages']
        self.assertEqual([item['id'] for item in result], [item.pk for item in messages])

    def test_regular_user_cannot_read_history(self):
        self.client.force_login(self.regular_user)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)

    def test_anonymous_user_cannot_read_history(self):
        self.client.logout()
        self.assertEqual(self.client.get(self.url).status_code, 302)


@override_settings(
    WHATSAPP_OUTBOX_MAX_ATTEMPTS=5, WHATSAPP_OUTBOX_RETRY_BASE_SECONDS=30,
    WHATSAPP_OUTBOX_RETRY_MAX_SECONDS=3600, WHATSAPP_OUTBOX_LOCK_SECONDS=600,
)
class OutboxRetryTests(TestCase):
    def setUp(self):
        self.contact = WhatsAppContact.objects.create(phone_number='51912345678')
        self.send = patch('applications.whatsapp.services.send_text_message').start()
        self.addCleanup(patch.stopall)

    def queued(self, **kwargs):
        message = WhatsAppMessage.objects.create(
            contact=self.contact, message_id=str(uuid.uuid4()), text='Respuesta',
            is_from_customer=False, status=WhatsAppMessage.PROCESSED,
        )
        return Outbox.objects.create(contact=self.contact, message=message, content='Respuesta', **kwargs)

    def test_twenty_delayed_failures_do_not_block_new_messages(self):
        for _ in range(20):
            self.queued(attempts=1, next_attempt_at=timezone.now() + timedelta(minutes=1))
        fresh = [self.queued() for _ in range(5)]
        self.assertEqual(process_outbox(), 5)
        self.assertEqual(self.send.call_count, 5)
        self.assertEqual(Outbox.objects.filter(pk__in=[item.pk for item in fresh], sent=True).count(), 5)

    def test_retry_waits_until_due_and_then_succeeds(self):
        item = self.queued()
        now = timezone.now()
        self.send.side_effect = WhatsAppAPIError('rate limit', code=130429)
        with patch('applications.whatsapp.services.timezone.now', return_value=now):
            self.assertEqual(process_outbox(), 0)
        item.refresh_from_db()
        self.assertEqual(item.attempts, 1)
        self.assertEqual(item.next_attempt_at, now + timedelta(seconds=30))
        self.assertIsNone(item.locked_at)
        self.assertIsNone(item.lock_token)
        self.assertIsNone(item.failed_at)
        self.assertIn('130429', item.last_error)
        with patch('applications.whatsapp.services.timezone.now', return_value=now + timedelta(seconds=29)):
            process_outbox()
        self.assertEqual(self.send.call_count, 1)
        self.send.side_effect = None
        with patch('applications.whatsapp.services.timezone.now', return_value=now + timedelta(seconds=30)):
            self.assertEqual(process_outbox(), 1)
        item.refresh_from_db()
        self.assertTrue(item.sent)
        self.assertEqual(item.attempts, 2)
        self.assertEqual(item.last_error, '')
        self.assertIsNone(item.next_attempt_at)
        self.assertIsNotNone(item.sent_at)

    def test_progressive_backoff_and_attempt_limit(self):
        item = self.queued()
        self.send.side_effect = WhatsAppAPIError('offline')
        now = timezone.now()
        for attempt, delay in [(1, 30), (2, 60), (3, 120), (4, 240), (5, None)]:
            with patch('applications.whatsapp.services.timezone.now', return_value=now):
                process_outbox()
            item.refresh_from_db()
            self.assertEqual(item.attempts, attempt)
            if delay is None:
                self.assertEqual(item.failed_at, now)
                self.assertIsNone(item.next_attempt_at)
            else:
                self.assertEqual(item.next_attempt_at, now + timedelta(seconds=delay))
                now = item.next_attempt_at
        process_outbox()
        self.assertEqual(self.send.call_count, 5)

    @override_settings(WHATSAPP_OUTBOX_RETRY_MAX_SECONDS=45)
    def test_delay_is_capped(self):
        item = self.queued(attempts=2)
        self.send.side_effect = WhatsAppAPIError('offline')
        now = timezone.now()
        with patch('applications.whatsapp.services.timezone.now', return_value=now):
            process_outbox()
        item.refresh_from_db()
        self.assertEqual(item.next_attempt_at, now + timedelta(seconds=45))

    def test_permanent_error_is_not_retried(self):
        item = self.queued()
        self.send.side_effect = WhatsAppAPIError('invalid parameter', http_status=400, code=131009)
        process_outbox()
        process_outbox()
        item.refresh_from_db()
        self.assertIsNotNone(item.failed_at)
        self.assertIsNone(item.next_attempt_at)
        self.assertEqual(item.attempts, 1)
        self.assertIn('permanent', item.last_error)
        self.send.assert_called_once()

    def test_configuration_error_is_identifiable(self):
        item = self.queued()
        self.send.side_effect = WhatsAppAPIError('expired token', code=190)
        process_outbox()
        item.refresh_from_db()
        self.assertIsNotNone(item.failed_at)
        self.assertIn('configuration', item.last_error)

    def test_unknown_http_400_has_limited_retries(self):
        item = self.queued()
        self.send.side_effect = WhatsAppAPIError('unknown', http_status=400, code=999999)
        process_outbox()
        item.refresh_from_db()
        self.assertIsNone(item.failed_at)
        self.assertIsNotNone(item.next_attempt_at)

    def test_exhausted_legacy_rows_do_not_block_new_messages(self):
        old = [self.queued(attempts=20, last_error='Legacy failure') for _ in range(20)]
        self.queued()
        self.assertEqual(process_outbox(), 1)
        self.assertEqual(Outbox.objects.filter(pk__in=[item.pk for item in old], failed_at__isnull=False).count(), 20)
        old[0].refresh_from_db()
        self.assertEqual(old[0].last_error, 'Legacy failure')

    def test_active_lock_is_not_stolen_even_on_last_attempt(self):
        item = self.queued(attempts=5, locked_at=timezone.now(), lock_token=uuid.uuid4())
        process_outbox()
        item.refresh_from_db()
        self.assertIsNone(item.failed_at)
        self.assertIsNotNone(item.lock_token)
        self.send.assert_not_called()

    def test_stale_lock_can_be_recovered(self):
        item = self.queued(locked_at=timezone.now() - timedelta(seconds=601), lock_token=uuid.uuid4())
        self.assertEqual(process_outbox(), 1)
        item.refresh_from_db()
        self.assertTrue(item.sent)
        self.assertIsNone(item.lock_token)

    def test_stale_final_attempt_is_failed_without_resending(self):
        item = self.queued(attempts=5, locked_at=timezone.now() - timedelta(seconds=601))
        process_outbox()
        item.refresh_from_db()
        self.assertIsNotNone(item.failed_at)
        self.send.assert_not_called()

    def test_old_attempt_cannot_overwrite_new_owner(self):
        old_token, new_token = uuid.uuid4(), uuid.uuid4()
        item = self.queued(attempts=1, locked_at=timezone.now(), lock_token=old_token)
        Outbox.objects.filter(pk=item.pk).update(lock_token=new_token, attempts=2)
        self.assertFalse(_finish_outbox(item, old_token))
        self.assertFalse(_finish_outbox(item, old_token, WhatsAppAPIError('late failure')))
        item.refresh_from_db()
        self.assertEqual(item.lock_token, new_token)
        self.assertFalse(item.sent)
        self.assertEqual(item.last_error, '')
        self.assertTrue(_finish_outbox(item, new_token))

    def test_already_sent_and_failed_rows_are_skipped(self):
        self.queued(sent=True, sent_at=timezone.now())
        self.queued(failed_at=timezone.now())
        self.assertEqual(process_outbox(), 0)
        self.send.assert_not_called()


@override_settings(WHATSAPP_ACCESS_TOKEN='test-token', WHATSAPP_PHONE_NUMBER_ID='123')
class WhatsAppSendErrorTests(SimpleTestCase):
    @patch('applications.whatsapp.meta_api.urlopen')
    def test_direct_oversized_send_fails_without_truncating(self, urlopen):
        with self.assertRaises(WhatsAppAPIError) as raised:
            send_text_message('51912345678', 'x' * 4097)
        self.assertFalse(raised.exception.retryable)
        urlopen.assert_not_called()

    @patch('applications.whatsapp.meta_api.urlopen')
    def test_meta_error_metadata_is_preserved(self, urlopen):
        body = json.dumps({'error': {'code': 130429, 'message': 'Rate limit'}}).encode()
        urlopen.side_effect = HTTPError('https://example.test', 400, 'Bad request', {}, io.BytesIO(body))
        with self.assertRaises(WhatsAppAPIError) as raised:
            send_text_message('51912345678', 'Hola')
        error = raised.exception
        self.assertEqual(error.http_status, 400)
        self.assertEqual(error.code, 130429)
        self.assertTrue(error.retryable)

    @patch('applications.whatsapp.meta_api.urlopen')
    def test_non_json_http_error_is_retryable(self, urlopen):
        urlopen.side_effect = HTTPError('https://example.test', 503, 'Unavailable', {}, io.BytesIO(b'offline'))
        with self.assertRaises(WhatsAppAPIError) as raised:
            send_text_message('51912345678', 'Hola')
        self.assertEqual(raised.exception.http_status, 503)
        self.assertTrue(raised.exception.retryable)

    @patch('applications.whatsapp.meta_api.urlopen', side_effect=URLError('offline'))
    def test_network_error_is_retryable(self, urlopen):
        with self.assertRaises(WhatsAppAPIError) as raised:
            send_text_message('51912345678', 'Hola')
        self.assertTrue(raised.exception.retryable)

    @override_settings(WHATSAPP_ACCESS_TOKEN='')
    @patch('applications.whatsapp.meta_api.urlopen')
    def test_missing_credentials_are_configuration_error(self, urlopen):
        with self.assertRaises(WhatsAppAPIError) as raised:
            send_text_message('51912345678', 'Hola')
        self.assertTrue(raised.exception.configuration)
        self.assertFalse(raised.exception.retryable)
        urlopen.assert_not_called()

    def test_documented_error_classification(self):
        for code in [2, 4, 80007, 130429, 131000, 131016, 131056, 999999]:
            with self.subTest(code=code):
                self.assertTrue(WhatsAppAPIError('error', code=code).retryable)
        for code in [100, 130403, 131009, 131021, 131026, 131047, 131050]:
            with self.subTest(code=code):
                self.assertFalse(WhatsAppAPIError('error', code=code).retryable)


@override_settings(WHATSAPP_DEFAULT_SOURCE='google_sheets')
class WhatsAppQueueTests(TestCase):
    def abandoned_batch(self, count=1, age=601, attempts=1, legacy=False):
        contact = WhatsAppContact.objects.create(phone_number='51900000000')
        token = None if legacy else uuid.uuid4()
        messages = [WhatsAppMessage.objects.create(
            contact=contact, message_id=f'abandoned-{index}', text=f'Consulta {index}',
            status=WhatsAppMessage.PROCESSING,
            processing_started_at=None if legacy else timezone.now() - timedelta(seconds=age),
            processing_token=token, processing_attempts=attempts,
        ) for index in range(count)]
        return messages, token

    @patch('applications.whatsapp.services.answer_sheet_message', return_value='Recuperado')
    def test_stale_batch_is_recovered_together(self, answer):
        messages, old_token = self.abandoned_batch(count=3)
        self.assertEqual(process_pending_messages(force=True), 1)
        answer.assert_called_once()
        self.assertIn('Consulta 0\nConsulta 1\nConsulta 2', answer.call_args.args[1])
        for message in messages:
            message.refresh_from_db()
            self.assertEqual(message.status, WhatsAppMessage.PROCESSED)
            self.assertEqual(message.processing_attempts, 2)
            self.assertIsNone(message.processing_token)
            self.assertIsNone(message.processing_started_at)
        self.assertEqual(Outbox.objects.count(), 1)

    @patch('applications.whatsapp.services.answer_sheet_message')
    def test_recent_processing_is_not_reclaimed(self, answer):
        messages, _ = self.abandoned_batch(age=10)
        self.assertEqual(process_pending_messages(force=True), 0)
        messages[0].refresh_from_db()
        self.assertEqual(messages[0].status, WhatsAppMessage.PROCESSING)
        answer.assert_not_called()

    @override_settings(WHATSAPP_PROCESSING_MAX_ATTEMPTS=3)
    @patch('applications.whatsapp.services.answer_sheet_message')
    def test_exhausted_batch_is_failed(self, answer):
        messages, _ = self.abandoned_batch(count=2, attempts=3)
        self.assertEqual(process_pending_messages(force=True), 0)
        for message in messages:
            message.refresh_from_db()
            self.assertEqual(message.status, WhatsAppMessage.FAILED)
            self.assertIsNotNone(message.processed_at)
        answer.assert_not_called()
        # The customer is told instead of being left without an answer.
        self.assertEqual(Outbox.objects.get().content, PROCESSING_ERROR)

    def test_error_reply_keeps_the_previous_candidates_context(self):
        messages, token = self.abandoned_batch()
        contact = messages[0].contact
        WhatsAppMessage.objects.create(
            contact=contact, message_id='local-previous', text='Opciones', is_from_customer=False,
            status=WhatsAppMessage.PROCESSED, inventory_context={'candidates': ['A1', 'B2']},
        )
        self.assertTrue(_finish_processing(messages, token, PROCESSING_ERROR, failed=True))
        latest = contact.messages.filter(is_from_customer=False).order_by('-created_at', '-pk').first()
        self.assertEqual(latest.text, PROCESSING_ERROR)
        self.assertEqual(latest.inventory_context, {'candidates': ['A1', 'B2']})

    def test_error_reply_without_previous_context_stays_empty(self):
        messages, token = self.abandoned_batch()
        self.assertTrue(_finish_processing(messages, token, PROCESSING_ERROR, failed=True))
        latest = messages[0].contact.messages.filter(is_from_customer=False).first()
        self.assertEqual(latest.inventory_context, {})

    @override_settings(WHATSAPP_PROCESSING_MAX_ATTEMPTS=3)
    @patch('applications.whatsapp.services.answer_sheet_message')
    def test_exhausted_batch_in_human_mode_stays_silent(self, answer):
        messages, _ = self.abandoned_batch(attempts=3)
        WhatsAppContact.objects.filter(pk=messages[0].contact_id).update(mode=WhatsAppContact.HUMAN)
        process_pending_messages(force=True)
        messages[0].refresh_from_db()
        self.assertNotEqual(messages[0].status, WhatsAppMessage.PROCESSING)
        self.assertFalse(Outbox.objects.exists())
        answer.assert_not_called()

    @patch('applications.whatsapp.services.answer_sheet_message', return_value='Legacy recuperado')
    def test_legacy_processing_without_timestamp_is_recovered(self, answer):
        messages, _ = self.abandoned_batch(legacy=True, attempts=0)
        process_pending_messages(force=True)
        messages[0].refresh_from_db()
        self.assertEqual(messages[0].status, WhatsAppMessage.PROCESSED)

    def test_old_attempt_cannot_commit_response_or_error(self):
        messages, token = self.abandoned_batch(count=2)
        _recover_abandoned_messages()
        new_token = uuid.uuid4()
        WhatsAppMessage.objects.filter(pk__in=[m.pk for m in messages]).update(
            status=WhatsAppMessage.PROCESSING, processing_token=new_token,
            processing_started_at=timezone.now(),
        )
        self.assertFalse(_finish_processing(messages, token, 'Respuesta antigua'))
        self.assertFalse(_finish_processing(messages, token, 'Error antiguo', failed=True))
        self.assertEqual(Outbox.objects.count(), 0)
        self.assertTrue(_finish_processing(messages, new_token, 'Respuesta nueva'))
        self.assertEqual(Outbox.objects.get().content, 'Respuesta nueva')

    def test_partial_ownership_cannot_commit_batch(self):
        messages, token = self.abandoned_batch(count=2)
        WhatsAppMessage.objects.filter(pk=messages[1].pk).update(processing_token=uuid.uuid4())
        self.assertFalse(_finish_processing(messages, token, 'Parcial'))
        self.assertFalse(Outbox.objects.exists())

    @patch('applications.whatsapp.services.Outbox.objects.create', side_effect=RuntimeError('database failure'))
    def test_response_commit_rolls_back_if_outbox_creation_fails(self, create):
        messages, token = self.abandoned_batch()
        with self.assertRaises(RuntimeError):
            _finish_processing(messages, token, 'Respuesta')
        messages[0].refresh_from_db()
        self.assertEqual(messages[0].status, WhatsAppMessage.PROCESSING)
        self.assertEqual(messages[0].processing_token, token)
        self.assertFalse(WhatsAppMessage.objects.filter(is_from_customer=False).exists())

    @override_settings(WHATSAPP_PROCESSING_TIMEOUT_SECONDS=5)
    def test_recovery_timeout_is_configurable(self):
        messages, _ = self.abandoned_batch(age=10)
        _recover_abandoned_messages()
        messages[0].refresh_from_db()
        self.assertEqual(messages[0].status, WhatsAppMessage.PENDING)

    @patch('applications.whatsapp.services.answer_sheet_message', return_value='Confirmado')
    def test_committed_response_is_not_generated_twice(self, answer):
        self.abandoned_batch()
        process_pending_messages(force=True)
        process_pending_messages(force=True)
        answer.assert_called_once()
        self.assertEqual(Outbox.objects.count(), 1)

    @patch('applications.whatsapp.services.answer_sheet_message', side_effect=RuntimeError('fallo'))
    @patch('applications.whatsapp.services.send_text_message')
    def test_processing_error_is_committed_to_outbox(self, send, answer):
        messages, _ = self.abandoned_batch()
        process_pending_messages(force=True)
        messages[0].refresh_from_db()
        self.assertEqual(messages[0].status, WhatsAppMessage.FAILED)
        self.assertIsNone(messages[0].processing_token)
        self.assertEqual(Outbox.objects.count(), 1)
        send.assert_not_called()

    @override_settings(MESSAGE_DEBOUNCE_SECONDS=0)
    @patch('applications.whatsapp.services.answer_sheet_message', return_value='Respuesta del bot')
    def test_message_is_queued_and_processed(self, answer):
        self.assertTrue(enqueue_message('51999999999', 'Cliente', 'wamid-1', 'Hola'))
        with patch('applications.whatsapp.services.send_text_message'):
            self.assertEqual(process_pending_messages(), 1)
            self.assertEqual(process_outbox(), 1)
        self.assertEqual(WhatsAppMessage.objects.filter(is_from_customer=True).first().status, 'processed')
        self.assertTrue(Outbox.objects.get().sent)
        answer.assert_called_once()

    @override_settings(MESSAGE_DEBOUNCE_SECONDS=0)
    @patch('applications.whatsapp.services.answer_sheet_message')
    def test_human_mode_does_not_call_ai(self, answer):
        contact = WhatsAppContact.objects.create(phone_number='51111111111', mode=WhatsAppContact.HUMAN)
        enqueue_message(contact.phone_number, '', 'wamid-2', 'Necesito ayuda')
        self.assertEqual(process_pending_messages(), 1)
        answer.assert_not_called()
        self.assertEqual(WhatsAppMessage.objects.get(message_id='wamid-2').status, 'processed')
