from unittest.mock import patch
import uuid
from datetime import timedelta
import io
import json
from urllib.error import HTTPError, URLError

from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone


from ..models import Outbox, WhatsAppContact, WhatsAppMessage
from ..meta_api import WhatsAppAPIError, send_text_message
from ..services import _finish_outbox, enqueue_message, enqueue_outbox, process_outbox, process_pending_messages, split_whatsapp_text


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

    def test_later_parts_wait_while_an_earlier_part_is_retrying(self):
        parts = self.queue()
        self.send.side_effect = WhatsAppAPIError('rate limit', code=130429)
        self.assertEqual(process_outbox(), 0)
        self.assertEqual(self.send.call_count, 1)
        self.assertEqual(self.send.call_args.args[1], parts[0].content)
        self.assertEqual(list(Outbox.objects.order_by('part_number').values_list('attempts', flat=True)), [1, 0, 0])
        # Cuando vence la espera, las partes salen en orden.
        self.send.side_effect = None
        Outbox.objects.update(next_attempt_at=None)
        self.assertEqual(process_outbox(), 3)
        self.assertEqual([call.args[1] for call in self.send.call_args_list][1:], [part.content for part in parts])

    def test_later_parts_are_discarded_when_an_earlier_part_fails_for_good(self):
        self.queue()
        self.send.side_effect = WhatsAppAPIError('bad recipient', code=131026)
        self.assertEqual(process_outbox(), 0)
        self.assertEqual(process_outbox(), 0)
        self.assertEqual(self.send.call_count, 1)
        self.assertEqual(Outbox.objects.filter(failed_at__isnull=False).count(), 3)
        self.assertEqual(Outbox.objects.filter(sent=True).count(), 0)

    def test_meta_message_id_is_stored_when_the_send_is_accepted(self):
        self.send.return_value = {'messages': [{'id': 'wamid.ACCEPTED'}]}
        enqueue_outbox(self.contact, self.message, 'Hola')
        self.assertEqual(process_outbox(), 1)
        item = Outbox.objects.get()
        self.assertEqual((item.meta_message_id, item.delivery_status), ('wamid.ACCEPTED', 'sent'))

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

    @override_settings(WHATSAPP_DEFAULT_SOURCE='google_sheets')
    @patch('applications.whatsapp.services.answer_sheet_message', return_value='y' * 9000)
    def test_automated_long_response_uses_shared_splitter(self, answer):
        enqueue_message(self.contact.phone_number, '', 'long-question', 'Consulta')
        process_pending_messages(force=True)
        self.assertEqual(Outbox.objects.count(), 3)
        assistant = WhatsAppMessage.objects.filter(is_from_customer=False).exclude(pk=self.message.pk).get()
        self.assertEqual(assistant.text, 'y' * 9000)
        self.send.assert_not_called()

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
