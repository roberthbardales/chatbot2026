import hashlib
import hmac
import io
import json
from contextlib import nullcontext
from datetime import timedelta
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import DatabaseError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from ..health import worker_health
from ..models import Outbox, WhatsAppContact, WhatsAppMessage, WorkerHeartbeat
from ..services import enqueue_message, process_pending_messages
from ..worker_runtime import Heartbeat


@override_settings(WHATSAPP_APP_SECRET='test-secret', WHATSAPP_PHONE_NUMBER_ID='number-123')
class AsyncWebhookTests(TestCase):
    def payload(self, **message_fields):
        message = {'from': '51911112222', 'id': 'wamid.async', 'type': 'text', 'text': {'body': 'Consulta'}}
        message.update(message_fields)
        return {'entry': [{'changes': [{'value': {
            'metadata': {'phone_number_id': 'number-123'},
            'contacts': [{'wa_id': '51911112222', 'profile': {'name': 'Cliente'}}],
            'messages': [message],
        }}]}]}

    def post(self, payload, valid=True):
        body = json.dumps(payload).encode()
        digest = hmac.new(b'test-secret', body, hashlib.sha256).hexdigest()
        return self.client.post(reverse('webhook'), data=body, content_type='application/json',
                                HTTP_X_HUB_SIGNATURE_256='sha256=' + (digest if valid else 'bad'))

    def verify(self, token):
        return self.client.get(reverse('webhook'), {
            'hub.mode': 'subscribe', 'hub.verify_token': token, 'hub.challenge': 'abc123'})

    def test_non_ascii_signature_is_rejected_with_403(self):
        response = self.client.post(reverse('webhook'), data=b'{}', content_type='application/json',
                                    HTTP_X_HUB_SIGNATURE_256='sha256=é')
        self.assertEqual(response.status_code, 403)

    def test_malformed_items_inside_signed_payload_are_ignored(self):
        payload = {'entry': [{'changes': [{'value': {
            'metadata': {'phone_number_id': 'number-123'},
            'contacts': ['x', {'wa_id': '51911112222', 'profile': None}],
            'messages': ['x', 5, {'from': '51911112222', 'id': 'wamid.ok', 'type': 'text', 'text': {'body': 'Hola'}}],
        }}]}]}
        self.assertEqual(self.post(payload).status_code, 200)
        self.assertTrue(WhatsAppMessage.objects.filter(message_id='wamid.ok').exists())

    @override_settings(WHATSAPP_VERIFY_TOKEN='secreto')
    def test_verification_accepts_only_the_configured_token(self):
        ok = self.verify('secreto')
        self.assertEqual((ok.status_code, ok.content), (200, b'abc123'))
        self.assertEqual(self.verify('otro').status_code, 403)
        self.assertEqual(self.verify('').status_code, 403)

    @override_settings(WHATSAPP_VERIFY_TOKEN='')
    def test_verification_is_rejected_when_token_is_not_configured(self):
        self.assertEqual(self.verify('').status_code, 403)

    @patch('applications.whatsapp.services.process_pending_messages')
    @patch('applications.whatsapp.services.process_outbox')
    @patch('applications.whatsapp.meta_api.urlopen')
    def test_webhook_only_persists_then_acknowledges(self, urlopen, outgoing, incoming):
        response = self.post(self.payload())
        self.assertEqual(response.status_code, 200)
        message = WhatsAppMessage.objects.get(message_id='wamid.async')
        self.assertEqual(message.status, WhatsAppMessage.PENDING)
        self.assertEqual(message.text, 'Consulta')
        self.assertFalse(Outbox.objects.exists())
        incoming.assert_not_called()
        outgoing.assert_not_called()
        urlopen.assert_not_called()

    def test_duplicate_notification_does_not_create_another_message(self):
        self.assertEqual(self.post(self.payload()).status_code, 200)
        self.assertEqual(self.post(self.payload()).status_code, 200)
        self.assertEqual(WhatsAppMessage.objects.count(), 1)
        self.assertEqual(WhatsAppContact.objects.count(), 1)

    def test_invalid_signature_cannot_enqueue(self):
        self.assertEqual(self.post(self.payload(), valid=False).status_code, 403)
        self.assertFalse(WhatsAppMessage.objects.exists())

    @patch('applications.whatsapp.views.enqueue_message', side_effect=DatabaseError('unavailable'))
    def test_persistence_error_is_not_acknowledged_as_success(self, enqueue):
        self.assertEqual(self.post(self.payload()).status_code, 500)

    def test_image_is_queued_without_downloading(self):
        with patch('applications.whatsapp.meta_api.urlopen') as urlopen:
            response = self.post(self.payload(type='image', image={'id': 'media-123', 'mime_type': 'image/jpeg', 'caption': 'Foto'}))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(WhatsAppMessage.objects.get().media_id, 'media-123')
        urlopen.assert_not_called()

    def test_status_only_and_other_phone_notifications_do_not_enqueue(self):
        self.assertEqual(self.post({'entry': [{'changes': [{'value': {'statuses': []}}]}]}).status_code, 200)
        payload = self.payload()
        payload['entry'][0]['changes'][0]['value']['metadata']['phone_number_id'] = 'other'
        self.assertEqual(self.post(payload).status_code, 200)
        self.assertFalse(WhatsAppMessage.objects.exists())

    def status_payload(self, meta_id, status, **extra):
        return {'entry': [{'changes': [{'value': {
            'metadata': {'phone_number_id': 'number-123'},
            'statuses': [{'id': meta_id, 'status': status, **extra}],
        }}]}]}

    def sent_item(self, meta_id='wamid.OUT1'):
        contact = WhatsAppContact.objects.create(phone_number='51955550000')
        message = WhatsAppMessage.objects.create(contact=contact, message_id='out-1', text='Respuesta',
                                                 is_from_customer=False, status=WhatsAppMessage.PROCESSED)
        return Outbox.objects.create(contact=contact, message=message, content='Respuesta', sent=True,
                                     meta_message_id=meta_id, delivery_status='sent')

    def test_delivery_statuses_advance_and_never_go_back(self):
        item = self.sent_item()
        for status, expected in [('delivered', 'delivered'), ('read', 'read'), ('delivered', 'read'), ('sent', 'read')]:
            self.assertEqual(self.post(self.status_payload('wamid.OUT1', status)).status_code, 200)
            item.refresh_from_db()
            self.assertEqual(item.delivery_status, expected)
        self.assertIsNotNone(item.delivery_updated_at)

    def test_failed_status_records_the_meta_error(self):
        item = self.sent_item()
        payload = self.status_payload('wamid.OUT1', 'failed', errors=[{'code': 131047, 'title': 'Re-engagement message'}])
        self.assertEqual(self.post(payload).status_code, 200)
        item.refresh_from_db()
        self.assertEqual(item.delivery_status, 'failed')
        self.assertEqual(item.delivery_error, '131047 Re-engagement message')

    def test_failed_status_does_not_overwrite_a_read_message(self):
        item = self.sent_item()
        self.post(self.status_payload('wamid.OUT1', 'read'))
        self.post(self.status_payload('wamid.OUT1', 'failed', errors=[{'code': 1, 'title': 'x'}]))
        item.refresh_from_db()
        self.assertEqual((item.delivery_status, item.delivery_error), ('read', ''))

    def test_unknown_or_malformed_statuses_are_ignored(self):
        item = self.sent_item()
        for payload in [self.status_payload('wamid.OTHER', 'delivered'),
                        self.status_payload('wamid.OUT1', 'inventado'),
                        self.status_payload(None, 'delivered'),
                        {'entry': [{'changes': [{'value': {'statuses': ['x', 5, {'id': 1, 'status': []}]}}]}]}]:
            self.assertEqual(self.post(payload).status_code, 200)
        item.refresh_from_db()
        self.assertEqual(item.delivery_status, 'sent')

    def test_non_object_payload_is_rejected(self):
        self.assertEqual(self.post([]).status_code, 400)


class WorkerCommandTests(TestCase):
    def setUp(self):
        prefix = 'applications.whatsapp.management.commands.whatsapp_worker.'
        self.incoming = patch(prefix + 'process_pending_messages', return_value=1).start()
        self.outgoing = patch(prefix + 'process_outbox', return_value=2).start()
        self.heartbeat = patch(prefix + 'Heartbeat', side_effect=lambda *args: nullcontext()).start()
        self.connections = patch(prefix + 'close_old_connections').start()
        self.sync = patch('applications.core.inventory_sync.apply_inventory').start()
        self.addCleanup(patch.stopall)

    def test_once_processes_both_queues_without_inventory_sync(self):
        call_command('whatsapp_worker', once=True, stdout=io.StringIO())
        self.incoming.assert_called_once()
        self.assertNotIn('force', self.incoming.call_args.kwargs)
        self.assertIn('max_contacts', self.incoming.call_args.kwargs)
        self.outgoing.assert_called_once()
        self.sync.assert_not_called()
        row = WorkerHeartbeat.objects.get()
        self.assertIsNotNone(row.last_success_at)
        self.assertIsNotNone(row.stopped_at)

    def test_outbox_runs_between_contacts_and_failure_does_not_stop_incoming(self):
        for number in ('51900000001', '51900000002'):
            enqueue_message(number, '', f'between-{number}', 'Consulta')
        # Run the real incoming phase (the setUp mock replaces it by default).
        self.incoming.side_effect = lambda **kwargs: process_pending_messages(force=True, **kwargs)
        calls = []

        def answer(*args, **kwargs):
            calls.append('answer')
            return 'Respuesta'

        def send(**kwargs):
            calls.append('outbox')
            raise RuntimeError('outbox down')

        self.outgoing.side_effect = send
        with patch('applications.whatsapp.services.answer_sheet_message', side_effect=answer):
            with self.assertRaises(CommandError):
                call_command('whatsapp_worker', once=True, stdout=io.StringIO())
        # answer, outbox (between contacts), answer, outbox (final)
        self.assertEqual(calls, ['answer', 'outbox', 'answer', 'outbox'])
        self.assertEqual(WorkerHeartbeat.objects.get().last_error, 'outgoing:RuntimeError')

    def test_incoming_error_does_not_block_outbox_and_once_exits_nonzero(self):
        self.incoming.side_effect = RuntimeError('private details')
        with self.assertRaises(CommandError):
            call_command('whatsapp_worker', once=True, stdout=io.StringIO())
        self.outgoing.assert_called_once()
        row = WorkerHeartbeat.objects.get()
        self.assertEqual(row.last_error, 'incoming:RuntimeError')
        self.assertNotIn('private details', row.last_error)
        self.assertIsNotNone(row.stopped_at)

    def test_outbox_error_is_recorded(self):
        self.outgoing.side_effect = RuntimeError('failure')
        with self.assertRaises(CommandError):
            call_command('whatsapp_worker', once=True, stdout=io.StringIO())
        self.assertEqual(WorkerHeartbeat.objects.get().last_error, 'outgoing:RuntimeError')

    def test_loop_uses_poll_interval_and_stops_on_interrupt(self):
        with patch('applications.whatsapp.management.commands.whatsapp_worker.time.sleep', side_effect=KeyboardInterrupt) as sleep:
            call_command('whatsapp_worker', poll_seconds=3, stdout=io.StringIO())
        sleep.assert_called_once_with(3)
        self.assertIsNotNone(WorkerHeartbeat.objects.get().stopped_at)

    def test_progress_callbacks_update_main_loop_progress(self):
        def incoming(**kwargs):
            kwargs['progress_callback']()
            self.assertEqual(WorkerHeartbeat.objects.get().phase, 'incoming')
            return 0

        def outgoing(**kwargs):
            kwargs['progress_callback']()
            self.assertEqual(WorkerHeartbeat.objects.get().phase, 'outgoing')
            return 0

        self.incoming.side_effect = incoming
        self.outgoing.side_effect = outgoing
        call_command('whatsapp_worker', once=True, stdout=io.StringIO())

    def test_invalid_interval_does_not_start_worker(self):
        with self.assertRaises(CommandError):
            call_command('whatsapp_worker', poll_seconds=0, stdout=io.StringIO())
        self.assertFalse(WorkerHeartbeat.objects.exists())
        self.incoming.assert_not_called()


@override_settings(WHATSAPP_WORKER_HEALTH_SECONDS=45, WHATSAPP_WORKER_HEARTBEAT_SECONDS=10,
                   WHATSAPP_WORKER_STALL_SECONDS=300, OPENROUTER_TIMEOUT=30)
class WorkerHealthTests(TestCase):
    def worker(self, **kwargs):
        now = timezone.now()
        return WorkerHeartbeat.objects.create(heartbeat_at=kwargs.pop('heartbeat_at', now),
                                               last_progress_at=kwargs.pop('last_progress_at', now), **kwargs)

    def test_not_started_and_healthy_states(self):
        self.assertEqual(worker_health()['state'], 'not_started')
        self.worker()
        self.assertTrue(worker_health()['healthy'])

    def test_stopped_worker_is_not_healthy(self):
        self.worker(stopped_at=timezone.now())
        self.assertEqual(worker_health()['state'], 'stopped')

    def test_missing_heartbeat_and_stalled_progress_are_detected(self):
        row = self.worker(heartbeat_at=timezone.now() - timedelta(seconds=60))
        self.assertEqual(worker_health()['state'], 'unresponsive')
        row.heartbeat_at = timezone.now()
        row.last_progress_at = timezone.now() - timedelta(seconds=301)
        row.save()
        self.assertEqual(worker_health()['state'], 'stalled')

    def test_worker_errors_are_degraded(self):
        self.worker(last_error='incoming:RuntimeError')
        self.assertEqual(worker_health()['state'], 'degraded')

    def test_healthy_worker_is_not_hidden_by_another_stopped_worker(self):
        self.worker()
        self.worker(stopped_at=timezone.now())
        self.assertTrue(worker_health()['healthy'])

    def test_healthcheck_is_read_only_and_nonzero_when_stopped(self):
        with self.assertRaises(CommandError):
            call_command('whatsapp_healthcheck', stdout=io.StringIO())
        self.assertFalse(WorkerHeartbeat.objects.exists())
        self.worker()
        output = io.StringIO()
        call_command('whatsapp_healthcheck', stdout=output)
        self.assertTrue(json.loads(output.getvalue())['healthy'])
        self.assertFalse(WhatsAppMessage.objects.exists())

    @patch('applications.whatsapp.worker_runtime.close_old_connections')
    @patch('applications.whatsapp.worker_runtime.connections.close_all')
    def test_heartbeat_does_not_hide_stalled_main_loop(self, close_all, close_old):
        old = timezone.now() - timedelta(seconds=301)
        row = self.worker(heartbeat_at=old, last_progress_at=old)
        heartbeat = Heartbeat(row.pk)
        with patch.object(heartbeat.stop, 'wait', side_effect=[False, True]):
            heartbeat._run()
        self.assertEqual(worker_health()['state'], 'stalled')

    @override_settings(WHATSAPP_DEFAULT_SOURCE='google_sheets', MESSAGE_DEBOUNCE_SECONDS=0)
    @patch('applications.whatsapp.services.answer_sheet_message', return_value='Respuesta')
    def test_bounded_incoming_batch_keeps_remaining_contacts_pending(self, answer):
        for i in range(3):
            enqueue_message(f'5191111000{i}', '', f'bounded-{i}', 'Hola')
        self.assertEqual(process_pending_messages(max_contacts=2), 2)
        self.assertEqual(WhatsAppMessage.objects.filter(status=WhatsAppMessage.PENDING).count(), 1)


class SendLoggingTests(TestCase):
    @override_settings(WHATSAPP_ACCESS_TOKEN='token', WHATSAPP_PHONE_NUMBER_ID='123')
    def test_http_error_log_does_not_echo_the_response_body_or_full_number(self):
        from urllib.error import HTTPError
        from ..meta_api import WhatsAppAPIError, send_text_message
        body = io.BytesIO(b'{"error": {"code": 131026, "message": "Recipient 51912345678 invalid"}}')
        error = HTTPError('https://graph.facebook.com', 400, 'Bad Request', {}, body)
        with patch('applications.whatsapp.meta_api.urlopen', side_effect=error):
            with self.assertLogs('applications.whatsapp.meta_api', level='WARNING') as logs:
                with self.assertRaises(WhatsAppAPIError) as caught:
                    send_text_message('51912345678', 'Hola')
        output = '\n'.join(logs.output)
        self.assertNotIn('51912345678', output)
        self.assertIn('code=131026', output)
        self.assertIn('***5678', output)
        # The full diagnostic is still kept for operators in Outbox.last_error.
        self.assertIn('51912345678', caught.exception.diagnostic())
