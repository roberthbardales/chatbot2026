import io
import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import close_old_connections
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from applications.chat.models import AIUsageDay, InventorySyncRun, Product
from applications.users.models import User
from .models import Outbox, OutboxRetryAudit, WhatsAppContact, WhatsAppMessage, WorkerHeartbeat
from .operations import RetryNotAllowed, operations_snapshot, retry_failed_group


class ManualRetryTests(TestCase):
    def setUp(self):
        self.manager = User.objects.create_user('retry-manager@test.com', occupation=User.EMPLEADO)
        self.client.force_login(self.manager)
        self.contact = WhatsAppContact.objects.create(phone_number='51911111111')
        self.message = WhatsAppMessage.objects.create(contact=self.contact, message_id='retry-local', is_from_customer=False)
        self.group = uuid.uuid4()
        self.accepted = Outbox.objects.create(contact=self.contact, message=self.message, group_id=self.group,
            part_number=1, content='Parte 1', sent=True, meta_message_id='wamid.accepted', delivery_status='accepted')
        self.failed = Outbox.objects.create(contact=self.contact, message=self.message, group_id=self.group,
            part_number=2, content='Parte 2', attempts=5, failed_at=timezone.now(), last_error='[configuration code=190] expired token')
        self.dependent = Outbox.objects.create(contact=self.contact, message=self.message, group_id=self.group,
            part_number=3, content='Parte 3', failed_at=timezone.now(), last_error='Dependency failed: part 2')
        self.url = reverse('retry-outbox', args=[self.failed.pk])

    def post(self, **data):
        payload = {'reason': 'Token revisado y corregido', 'confirm_risk': True, **data}
        return self.client.post(self.url, data=json.dumps(payload), content_type='application/json')

    @patch('applications.whatsapp.services.send_text_message')
    def test_reactivation_is_audited_and_does_not_resend_accepted_parts(self, send):
        response = self.post()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['parts'], 2)
        for part in [self.failed, self.dependent]:
            part.refresh_from_db()
            self.assertIsNone(part.failed_at)
            self.assertEqual(part.attempts, 0)
            self.assertEqual(part.manual_retries, 1)
            self.assertIsNotNone(part.last_retry_at)
        self.accepted.refresh_from_db()
        self.assertTrue(self.accepted.sent)
        self.assertEqual(self.accepted.meta_message_id, 'wamid.accepted')
        self.assertEqual(self.accepted.manual_retries, 0)
        audit = OutboxRetryAudit.objects.get()
        self.assertEqual(audit.actor, self.manager)
        self.assertEqual(audit.actor_identifier, str(self.manager.pk))
        self.assertEqual(audit.snapshot[0]['attempts'], 5)
        self.assertIn('expired token', audit.snapshot[0]['last_error'])
        self.assertEqual(audit.reason, 'Token revisado y corregido')
        send.assert_not_called()

    def test_duplicate_request_does_not_reactivate_twice(self):
        self.assertEqual(self.post().status_code, 200)
        self.assertEqual(self.post().status_code, 409)
        self.assertEqual(OutboxRetryAudit.objects.count(), 1)

    def test_reason_and_explicit_risk_confirmation_are_required(self):
        for payload in [{'reason': ''}, {'reason': 'x'}, {'reason': 'x' * 501}, {'confirm_risk': False}, {'confirm_risk': 'true'}]:
            with self.subTest(payload=payload):
                self.assertEqual(self.post(**payload).status_code, 400)
        self.assertFalse(OutboxRetryAudit.objects.exists())

    def test_accepted_or_delivered_part_cannot_be_retried(self):
        self.url = reverse('retry-outbox', args=[self.accepted.pk])
        self.assertEqual(self.post().status_code, 409)
        self.accepted.delivery_status = 'failed'
        self.accepted.delivery_failed_at = timezone.now()
        self.accepted.save()
        self.assertEqual(self.post().status_code, 409)
        self.assertFalse(OutboxRetryAudit.objects.exists())

    def test_later_dependent_part_cannot_bypass_earlier_failure(self):
        self.url = reverse('retry-outbox', args=[self.dependent.pk])
        self.assertEqual(self.post().status_code, 409)

    def test_active_group_lock_prevents_reactivation(self):
        self.failed.locked_at = timezone.now()
        self.failed.lock_token = uuid.uuid4()
        self.failed.save()
        self.assertEqual(self.post().status_code, 409)

    def test_known_delivery_failure_in_group_blocks_local_reactivation(self):
        self.accepted.delivery_status = 'failed'
        self.accepted.save()
        self.assertEqual(self.post().status_code, 409)

    def test_recent_manual_retry_is_rate_limited(self):
        self.failed.last_retry_at = timezone.now()
        self.failed.save()
        self.assertEqual(self.post().status_code, 409)

    def test_audit_failure_rolls_back_all_changes(self):
        with patch('applications.whatsapp.operations.OutboxRetryAudit.objects.create', side_effect=RuntimeError('failure')):
            with self.assertRaises(RuntimeError):
                retry_failed_group(self.failed.pk, self.manager, 'Revisión completada', confirmed=True)
        self.failed.refresh_from_db()
        self.assertIsNotNone(self.failed.failed_at)
        self.assertEqual(self.failed.attempts, 5)

    def test_retry_audit_survives_contact_and_actor_deletion(self):
        self.post()
        manager_pk = self.manager.pk
        self.contact.delete()
        self.manager.delete()
        audit = OutboxRetryAudit.objects.get()
        self.assertIsNone(audit.actor_id)
        self.assertIsNone(audit.outbox_id)
        self.assertEqual(audit.actor_identifier, str(manager_pk))
        self.assertEqual(len(audit.snapshot), 2)

    def test_failure_list_marks_safe_and_unsafe_retries(self):
        data = self.client.get(reverse('outbox-failures')).json()
        rows = {row['id']: row for row in data['failures']}
        self.assertTrue(rows[self.failed.pk]['can_retry'])
        self.assertFalse(rows[self.dependent.pk]['can_retry'])
        self.assertNotIn(self.accepted.pk, rows)

    def test_retry_requires_csrf(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.manager)
        response = client.post(self.url, data=json.dumps({'reason': 'Revisado', 'confirm_risk': True}), content_type='application/json')
        self.assertEqual(response.status_code, 403)

    def test_all_operational_endpoints_require_management_permissions(self):
        regular = User.objects.create_user('retry-normal@test.com', occupation=User.USUARIO)
        for user in [None, regular]:
            self.client.logout()
            if user:
                self.client.force_login(user)
            self.assertEqual(self.client.get(reverse('operations-summary')).status_code, 302)
            self.assertEqual(self.client.get(reverse('outbox-failures')).status_code, 302)
            self.assertEqual(self.post().status_code, 302)

    def test_internal_retry_service_also_checks_actor_permissions(self):
        regular = User.objects.create_user('internal-normal@test.com', occupation=User.USUARIO)
        with self.assertRaises(RetryNotAllowed):
            retry_failed_group(self.failed.pk, regular, 'Motivo de revisión', confirmed=True)
        self.assertFalse(OutboxRetryAudit.objects.exists())

    def test_known_remote_id_blocks_retry_even_without_local_sent_flag(self):
        self.failed.meta_message_id = 'wamid.ambiguous-local-state'
        self.failed.save()
        self.assertEqual(self.post().status_code, 409)

    def test_failure_pagination_and_missing_retry_target_are_controlled(self):
        self.assertEqual(self.client.get(reverse('outbox-failures'), {'offset': '-1'}).status_code, 400)
        self.url = reverse('retry-outbox', args=[99999999])
        self.assertEqual(self.post().status_code, 404)


@override_settings(WHATSAPP_ACCESS_TOKEN='test', WHATSAPP_PHONE_NUMBER_ID='phone', WHATSAPP_APP_SECRET='secret',
                   WHATSAPP_QUEUE_ALERT_SECONDS=120, WHATSAPP_QUEUE_ALERT_COUNT=100)
class OperationsSummaryTests(TestCase):
    def setUp(self):
        now = timezone.now()
        Product.objects.create(sku='OPS', name='Producto de prueba')
        InventorySyncRun.objects.create(status='applied', finished_at=now)
        WorkerHeartbeat.objects.create(heartbeat_at=now, last_progress_at=now)
        self.contact = WhatsAppContact.objects.create(phone_number='51911112222')

    def test_empty_snapshot_has_no_false_backlog_or_latency(self):
        snapshot = operations_snapshot()
        self.assertEqual(snapshot['incoming']['pending'], 0)
        self.assertIsNone(snapshot['metrics_24h']['average_processing_seconds'])
        self.assertEqual(snapshot['alerts'], [])

    def test_old_eligible_queue_alerts_but_future_deferrals_do_not(self):
        old = timezone.now() - timedelta(minutes=10)
        deferred = WhatsAppMessage.objects.create(contact=self.contact, message_id='deferred', status=WhatsAppMessage.PENDING,
                                                   retry_after_at=timezone.now() + timedelta(hours=1))
        WhatsAppMessage.objects.filter(pk=deferred.pk).update(created_at=old)
        self.assertFalse(any(alert['code'] == 'incoming_backlog' for alert in operations_snapshot()['alerts']))
        WhatsAppMessage.objects.filter(pk=deferred.pk).update(retry_after_at=None)
        self.assertTrue(any(alert['code'] == 'incoming_backlog' for alert in operations_snapshot()['alerts']))

    def test_latency_and_reserved_ai_are_reported_without_provider_calls(self):
        now = timezone.now()
        message = WhatsAppMessage.objects.create(contact=self.contact, message_id='processed', status=WhatsAppMessage.PROCESSED, processed_at=now)
        WhatsAppMessage.objects.filter(pk=message.pk).update(created_at=now - timedelta(seconds=20))
        outbox = Outbox.objects.create(contact=self.contact, message=message, content='Hola', sent=True, sent_at=now)
        Outbox.objects.filter(pk=outbox.pk).update(created_at=now - timedelta(seconds=5))
        AIUsageDay.objects.create(day=now.date(), requests=12, reserved_tokens=1234)
        snapshot = operations_snapshot()
        self.assertEqual(snapshot['metrics_24h']['average_processing_seconds'], 20)
        self.assertEqual(snapshot['metrics_24h']['average_acceptance_seconds'], 5)
        self.assertEqual(snapshot['ai']['requests'], 12)
        self.assertEqual(snapshot['ai']['reserved_tokens'], 1234)

    def test_configuration_and_budget_alerts_are_explicit(self):
        message = WhatsAppMessage.objects.create(contact=self.contact, message_id='config')
        Outbox.objects.create(contact=self.contact, message=message, content='Hola', failed_at=timezone.now(),
                              last_error='[configuration http=400 code=190] expired')
        with override_settings(AI_DAILY_REQUEST_LIMIT=10):
            AIUsageDay.objects.create(day=timezone.now().date(), requests=8)
            codes = {alert['code'] for alert in operations_snapshot()['alerts']}
        self.assertIn('configuration_failures', codes)
        self.assertIn('ai_budget', codes)

    def test_operations_check_is_read_only_and_supports_warning_exit(self):
        output = io.StringIO()
        call_command('whatsapp_operations_check', stdout=output)
        self.assertEqual(json.loads(output.getvalue())['alerts'], [])
        self.assertFalse(OutboxRetryAudit.objects.exists())
        with override_settings(WHATSAPP_ACCESS_TOKEN=''):
            with self.assertRaises(CommandError):
                call_command('whatsapp_operations_check', stdout=io.StringIO())

    def test_warning_exit_is_configurable(self):
        WhatsAppMessage.objects.create(contact=self.contact, message_id='failed-warning',
                                       status=WhatsAppMessage.FAILED, processed_at=timezone.now())
        call_command('whatsapp_operations_check', stdout=io.StringIO())
        with self.assertRaises(CommandError):
            call_command('whatsapp_operations_check', warnings_as_errors=True, stdout=io.StringIO())


class ConcurrentRetryTests(TransactionTestCase):
    def test_two_operators_cannot_reactivate_the_same_failure_twice(self):
        actor = User.objects.create_user('concurrent-retry@test.com', occupation=User.EMPLEADO)
        contact = WhatsAppContact.objects.create(phone_number='51911113333')
        message = WhatsAppMessage.objects.create(contact=contact, message_id='retry-concurrent')
        item = Outbox.objects.create(contact=contact, message=message, content='Hola', failed_at=timezone.now())
        barrier = Barrier(2)

        def retry():
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                try:
                    retry_failed_group(item.pk, actor, 'Causa revisada y corregida', confirmed=True)
                    return True
                except RetryNotAllowed:
                    return False
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: retry(), range(2)))
        self.assertEqual(sorted(results), [False, True])
        self.assertEqual(OutboxRetryAudit.objects.count(), 1)
