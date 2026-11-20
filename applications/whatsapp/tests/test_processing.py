from unittest.mock import patch
import uuid
from datetime import timedelta

from django.test import TestCase, override_settings
from django.utils import timezone

from applications.core.models import Product
from applications.core.services import InventoryAnswer
from applications.core.limits import LimitExceeded, identity, principal_slot

from ..models import Outbox, WhatsAppContact, WhatsAppMessage
from ..meta_api import WhatsAppAPIError
from ..services import PROCESSING_ERROR, _finish_processing, _recover_abandoned_messages, enqueue_message, process_outbox, process_pending_messages


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
        self.generate = patch('applications.core.services.generate_response', return_value='{}').start()
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
class WhatsAppImageTests(TestCase):
    def setUp(self):
        self.contact = WhatsAppContact.objects.create(phone_number='51933333333')
        self.download = patch('applications.whatsapp.services.download_media_data').start()
        self.answer = patch('applications.whatsapp.services.answer_sheet_message', return_value='Respuesta IA').start()
        self.send = patch('applications.whatsapp.services.send_text_message').start()
        self.addCleanup(patch.stopall)

    def incoming(self):
        enqueue_message(self.contact.phone_number, '', 'image-test', 'Esta es la pieza', 'media-123', 'image/jpeg')
        return WhatsAppMessage.objects.get(message_id='image-test')

    def test_image_is_downloaded_and_sent_to_the_answer_engine(self):
        message = self.incoming()
        self.download.return_value = 'data:image/jpeg;base64,abc'
        process_pending_messages(force=True)
        self.download.assert_called_once_with('media-123')
        self.assertEqual(self.answer.call_args.kwargs['image_data_url'], 'data:image/jpeg;base64,abc')
        self.assertEqual(Outbox.objects.get().content, 'Respuesta IA')
        message.refresh_from_db()
        self.assertEqual(message.response, 'Respuesta IA')

    def test_failed_download_replies_with_the_processing_error(self):
        self.download.side_effect = WhatsAppAPIError('download failed')
        message = self.incoming()
        process_pending_messages(force=True)
        self.answer.assert_not_called()
        message.refresh_from_db()
        self.assertEqual(message.status, WhatsAppMessage.FAILED)
        self.assertEqual(Outbox.objects.get().content, PROCESSING_ERROR)


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


# Estas pruebas simulan el motor de plantillas: no deben depender de AI_AGENT_ENABLED del .env
# (con el agente encendido llamarían a OpenRouter de verdad).
from django.test import override_settings as _override_settings  # noqa: E402

_AGENT_OFF = _override_settings(AI_AGENT_ENABLED=False)


def setUpModule():
    _AGENT_OFF.enable()


def tearDownModule():
    _AGENT_OFF.disable()
