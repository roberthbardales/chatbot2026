import hashlib
import hmac
import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch

from django.db import DatabaseError, close_old_connections
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from applications.users.models import User
from .delivery import record_delivery_status, reconcile_delivery_events
from .meta_api import WhatsAppAPIError, send_text_message
from .models import DeliveryEvent, Outbox, WhatsAppContact, WhatsAppMessage
from .services import _finish_outbox, process_outbox


@override_settings(WHATSAPP_APP_SECRET='delivery-secret', WHATSAPP_PHONE_NUMBER_ID='phone-123')
class DeliveryTrackingTests(TestCase):
    def setUp(self):
        self.contact = WhatsAppContact.objects.create(phone_number='51912345678')
        self.message = WhatsAppMessage.objects.create(
            contact=self.contact, message_id='local-response', text='Respuesta',
            is_from_customer=False, status=WhatsAppMessage.PROCESSED,
        )
        self.item = Outbox.objects.create(contact=self.contact, message=self.message, content='Respuesta',
                                          sent=True, sent_at=timezone.now(), meta_message_id='wamid.delivery', delivery_status='accepted')
        self.timestamp = int(timezone.now().timestamp()) - 60

    def status(self, value, **extra):
        return {'id': 'wamid.delivery', 'status': value, 'recipient_id': self.contact.phone_number,
                'timestamp': str(self.timestamp), **extra}

    def record(self, value, **extra):
        return record_delivery_status(self.status(value, **extra), 'phone-123')

    def post_status(self, statuses, number='phone-123', valid=True):
        body = json.dumps({'entry': [{'changes': [{'value': {
            'metadata': {'phone_number_id': number}, 'statuses': statuses,
        }}]}]}).encode()
        digest = hmac.new(b'delivery-secret', body, hashlib.sha256).hexdigest()
        return self.client.post(reverse('webhook'), data=body, content_type='application/json',
                                HTTP_X_HUB_SIGNATURE_256='sha256=' + (digest if valid else 'bad'))

    def test_successful_callbacks_advance_without_requiring_all_intermediate_events(self):
        for status in ['sent', 'delivered', 'read']:
            self.record(status)
            self.item.refresh_from_db()
            self.assertEqual(self.item.delivery_status, status)
        self.assertIsNotNone(self.item.provider_sent_at)
        self.assertIsNotNone(self.item.delivered_at)
        self.assertIsNotNone(self.item.read_at)
        self.assertEqual(DeliveryEvent.objects.filter(applied_at__isnull=False).count(), 3)

    def test_read_before_delivered_and_sent_does_not_regress(self):
        self.record('read')
        self.record('sent', timestamp=str(self.timestamp - 10))
        self.record('delivered', timestamp=str(self.timestamp - 5))
        self.item.refresh_from_db()
        self.assertEqual(self.item.delivery_status, 'read')
        self.assertEqual(int(self.item.read_at.timestamp()), self.timestamp)

    def test_duplicate_notifications_are_idempotent(self):
        self.assertTrue(self.record('delivered'))
        self.assertFalse(self.record('delivered'))
        self.assertEqual(DeliveryEvent.objects.count(), 1)
        self.item.refresh_from_db()
        self.assertEqual(int(self.item.delivered_at.timestamp()), self.timestamp)

    def test_success_timestamp_is_not_replaced_by_later_duplicate_status(self):
        self.record('delivered')
        self.record('delivered', timestamp=str(self.timestamp + 20))
        self.item.refresh_from_db()
        self.assertEqual(int(self.item.delivered_at.timestamp()), self.timestamp)

    @patch('applications.whatsapp.services.send_text_message')
    def test_asynchronous_failure_is_visible_without_resending(self, send):
        self.record('failed', errors=[{'code': 131026, 'error_data': {'details': 'Recipient unavailable'}}])
        self.item.refresh_from_db()
        self.assertEqual(self.item.delivery_status, 'failed')
        self.assertIn('131026', self.item.delivery_error)
        self.assertTrue(self.item.sent)
        self.assertIsNone(self.item.failed_at)
        process_outbox()
        send.assert_not_called()

    def test_sent_event_cannot_clear_delivery_failure(self):
        self.record('failed')
        self.record('sent', timestamp=str(self.timestamp + 5))
        self.item.refresh_from_db()
        self.assertEqual(self.item.delivery_status, 'failed')

    def test_delivered_or_read_evidence_wins_over_failure(self):
        self.record('failed')
        self.record('delivered')
        self.record('failed', timestamp=str(self.timestamp + 1))
        self.item.refresh_from_db()
        self.assertEqual(self.item.delivery_status, 'delivered')
        self.record('read')
        self.item.refresh_from_db()
        self.assertEqual(self.item.delivery_status, 'read')

    def test_unknown_id_is_retained_then_applied_when_send_is_persisted(self):
        self.item.meta_message_id = None
        self.item.sent = False
        self.item.lock_token = uuid.uuid4()
        self.item.save()
        self.record('read')
        self.assertIsNone(DeliveryEvent.objects.get().applied_at)
        self.assertTrue(_finish_outbox(self.item, self.item.lock_token, meta_message_id='wamid.delivery'))
        self.item.refresh_from_db()
        self.assertEqual(self.item.delivery_status, 'read')
        self.assertEqual(DeliveryEvent.objects.get().outbox_id, self.item.pk)

    def test_reconciliation_handles_event_missed_during_send_commit(self):
        self.item.meta_message_id = None
        self.item.save()
        self.record('delivered')
        Outbox.objects.filter(pk=self.item.pk).update(meta_message_id='wamid.delivery')
        reconcile_delivery_events()
        self.item.refresh_from_db()
        self.assertEqual(self.item.delivery_status, 'delivered')

    def test_unknown_or_mismatched_events_do_not_starve_reconciliation(self):
        now = timezone.now()
        DeliveryEvent.objects.bulk_create([
            DeliveryEvent(fingerprint=f'unknown-{i}', meta_message_id=f'unknown-{i}',
                          recipient_id=self.contact.phone_number, phone_number_id='phone-123', status='sent', occurred_at=now)
            for i in range(210)
        ])
        self.item.meta_message_id = None
        self.item.save()
        self.record('read')
        Outbox.objects.filter(pk=self.item.pk).update(meta_message_id='wamid.delivery')
        reconcile_delivery_events(limit=1)
        self.item.refresh_from_db()
        self.assertEqual(self.item.delivery_status, 'read')

    def test_wrong_recipient_or_account_cannot_modify_outbox(self):
        self.record('read', recipient_id='51999999999')
        record_delivery_status(self.status('read'), 'other-phone')
        reconcile_delivery_events()
        self.item.refresh_from_db()
        self.assertEqual(self.item.delivery_status, 'accepted')
        self.assertEqual(DeliveryEvent.objects.count(), 1)
        self.assertIsNone(DeliveryEvent.objects.get().applied_at)

    def test_plus_prefixed_contact_is_reconciled(self):
        self.contact.phone_number = '+' + self.contact.phone_number
        self.contact.save()
        self.item.meta_message_id = None
        self.item.save()
        self.record('read', recipient_id=self.contact.phone_number.lstrip('+'))
        Outbox.objects.filter(pk=self.item.pk).update(meta_message_id='wamid.delivery')
        reconcile_delivery_events()
        self.item.refresh_from_db()
        self.assertEqual(self.item.delivery_status, 'read')

    def test_invalid_events_are_ignored(self):
        for extra in [{'status': []}, {'status': 'deleted'}, {'timestamp': 'bad'}, {'timestamp': -1},
                      {'timestamp': '999999999999999999999'}, {'id': ''}, {'recipient_id': None}]:
            with self.subTest(extra=extra):
                record_delivery_status(self.status('sent', **extra), 'phone-123')
        self.assertFalse(DeliveryEvent.objects.exists())

    @patch('applications.whatsapp.services.send_text_message')
    def test_signed_status_webhook_updates_without_sending_or_enqueuing(self, send):
        self.assertEqual(self.post_status([self.status('read')]).status_code, 200)
        self.item.refresh_from_db()
        self.assertEqual(self.item.delivery_status, 'read')
        self.assertEqual(WhatsAppMessage.objects.count(), 1)
        send.assert_not_called()

    def test_unsigned_or_other_account_webhook_cannot_change_status(self):
        self.assertEqual(self.post_status([self.status('read')], valid=False).status_code, 403)
        self.assertEqual(self.post_status([self.status('read')], number='other').status_code, 200)
        self.assertFalse(DeliveryEvent.objects.exists())

    @patch('applications.whatsapp.views.record_delivery_status', side_effect=DatabaseError('unavailable'))
    def test_failed_event_persistence_is_not_acknowledged_as_success(self, record):
        self.assertEqual(self.post_status([self.status('read')]).status_code, 500)

    def test_panel_aggregate_and_dates_include_confirmed_read(self):
        self.record('read')
        manager = User.objects.create_user('delivery-view@test.com', occupation=User.EMPLEADO)
        self.client.force_login(manager)
        data = self.client.get(reverse('conversation-messages', args=[self.contact.pk])).json()['messages'][0]
        self.assertEqual(data['sending']['read_parts'], 1)
        self.assertEqual(data['sending']['delivered_parts'], 1)
        self.assertEqual(data['delivery_parts'][0]['status'], 'read')
        self.assertIsNotNone(data['delivery_parts'][0]['read_at'])
        self.assertIsNone(data['delivery_parts'][0]['delivered_at'])

    def test_panel_reports_async_failure_separately_from_acceptance(self):
        self.record('failed')
        manager = User.objects.create_user('delivery-failure@test.com', occupation=User.EMPLEADO)
        self.client.force_login(manager)
        data = self.client.get(reverse('conversation-messages', args=[self.contact.pk])).json()['messages'][0]
        self.assertEqual(data['sending']['accepted_parts'], 1)
        self.assertEqual(data['sending']['failed_parts'], 1)
        self.assertEqual(data['delivery_parts'][0]['status'], 'failed')
        self.assertTrue(data['delivery_parts'][0]['last_error'])

    def test_stale_send_attempt_cannot_store_meta_id(self):
        self.item.sent = False
        self.item.meta_message_id = None
        self.item.lock_token = uuid.uuid4()
        self.item.save()
        self.assertFalse(_finish_outbox(self.item, uuid.uuid4(), meta_message_id='old-attempt'))
        self.item.refresh_from_db()
        self.assertIsNone(self.item.meta_message_id)

    @patch('applications.whatsapp.services.send_text_message', return_value={'messages': [{'id': 'wamid.new'}]})
    def test_outbox_records_meta_identifier(self, send):
        self.item.sent = False
        self.item.meta_message_id = None
        self.item.save()
        self.assertEqual(process_outbox(), 1)
        self.item.refresh_from_db()
        self.assertEqual(self.item.meta_message_id, 'wamid.new')
        self.assertEqual(self.item.delivery_status, 'accepted')

    def test_legacy_sent_row_is_not_claimed_as_delivered(self):
        self.item.meta_message_id = None
        self.item.delivery_status = ''
        self.item.save()
        self.assertIsNone(self.item.delivered_at)
        self.assertIsNone(self.item.read_at)


@override_settings(WHATSAPP_ACCESS_TOKEN='test', WHATSAPP_PHONE_NUMBER_ID='phone-123')
class MetaIdentifierValidationTests(TestCase):
    @patch('applications.whatsapp.meta_api.urlopen')
    def test_response_without_valid_id_is_not_accepted(self, urlopen):
        for messages in [[{}], [{'id': ''}], ['bad'], {'id': 'bad'}]:
            with self.subTest(messages=messages):
                urlopen.return_value.__enter__.return_value.read.return_value = json.dumps({'messages': messages}).encode()
                with self.assertRaises(WhatsAppAPIError):
                    send_text_message('51912345678', 'Hola')


@override_settings(WHATSAPP_PHONE_NUMBER_ID='phone-123')
class ConcurrentDeliveryTests(TransactionTestCase):
    def test_simultaneous_callbacks_do_not_regress_or_duplicate_state(self):
        contact = WhatsAppContact.objects.create(phone_number='51912345678')
        message = WhatsAppMessage.objects.create(contact=contact, message_id='concurrent', is_from_customer=False)
        item = Outbox.objects.create(contact=contact, message=message, content='Hola', sent=True,
                                      meta_message_id='wamid.concurrent', delivery_status='accepted')
        barrier = Barrier(3)
        stamp = str(int(timezone.now().timestamp()))

        def callback(status):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                return record_delivery_status({'id': 'wamid.concurrent', 'recipient_id': contact.phone_number,
                                               'status': status, 'timestamp': stamp}, 'phone-123')
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=3) as pool:
            results = list(pool.map(callback, ['read', 'sent', 'read']))
        self.assertEqual(sum(results), 2)
        self.assertEqual(DeliveryEvent.objects.count(), 2)
        item.refresh_from_db()
        self.assertEqual(item.delivery_status, 'read')
