"""Borrado seguro de conversaciones, limpieza de datos y orden de envío."""
import io
import json
from datetime import timedelta

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from unittest.mock import patch
from django.urls import reverse
from django.utils import timezone

from applications.users.models import User
from .services import PROCESSING_ERROR, enqueue_message, process_pending_messages
from .models import DeliveryEvent, Outbox, WhatsAppContact, WhatsAppMessage, WorkerHeartbeat


class DeleteConversationTests(TestCase):
    def setUp(self):
        self.manager = User.objects.create_user('delete-manager@test.com', occupation=User.EMPLEADO)
        self.client.force_login(self.manager)
        self.contact = WhatsAppContact.objects.create(phone_number='51933334444')
        self.url = reverse('delete-conversation', args=[self.contact.pk])

    def delete(self, body=None):
        return self.client.delete(self.url, data=json.dumps(body) if body is not None else '',
                                  content_type='application/json')

    def test_requires_explicit_confirmation(self):
        self.assertEqual(self.delete().status_code, 400)
        self.assertEqual(self.delete({'confirm': 'yes'}).status_code, 400)
        self.assertTrue(WhatsAppContact.objects.filter(pk=self.contact.pk).exists())

    def test_confirmed_delete_removes_contact_and_logs_masked_phone(self):
        WhatsAppMessage.objects.create(contact=self.contact, message_id='done-1', status=WhatsAppMessage.PROCESSED)
        with self.assertLogs('applications.whatsapp.views', level='WARNING') as logs:
            response = self.delete({'confirm': True})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(WhatsAppContact.objects.exists())
        self.assertIn('***4444', logs.output[0])
        self.assertNotIn('51933334444', logs.output[0])

    def test_pending_incoming_message_blocks_delete(self):
        WhatsAppMessage.objects.create(contact=self.contact, message_id='pending-1', status=WhatsAppMessage.PENDING)
        self.assertEqual(self.delete({'confirm': True}).status_code, 409)
        self.assertTrue(WhatsAppContact.objects.exists())

    def test_unsent_outbox_blocks_delete_but_failed_does_not(self):
        message = WhatsAppMessage.objects.create(contact=self.contact, message_id='local-1', is_from_customer=False,
                                                 status=WhatsAppMessage.PROCESSED)
        item = Outbox.objects.create(contact=self.contact, message=message, content='Hola')
        self.assertEqual(self.delete({'confirm': True}).status_code, 409)
        Outbox.objects.filter(pk=item.pk).update(failed_at=timezone.now())
        self.assertEqual(self.delete({'confirm': True}).status_code, 200)

    def test_unknown_contact_returns_404(self):
        response = self.client.delete(reverse('delete-conversation', args=[999999]),
                                      data='{"confirm": true}', content_type='application/json')
        self.assertEqual(response.status_code, 404)


class CleanupWhatsAppDataTests(TestCase):
    def run_command(self, *args):
        output = io.StringIO()
        call_command('cleanup_whatsapp_data', *args, stdout=output)
        return output.getvalue()

    def heartbeat(self, age_days):
        stamp = timezone.now() - timedelta(days=age_days)
        return WorkerHeartbeat.objects.create(heartbeat_at=stamp, last_progress_at=stamp, stopped_at=stamp)

    def event(self, name, age_days):
        event = DeliveryEvent.objects.create(
            fingerprint=name, meta_message_id=name, recipient_id='51900000000', phone_number_id='1',
            status='sent', occurred_at=timezone.now())
        DeliveryEvent.objects.filter(pk=event.pk).update(received_at=timezone.now() - timedelta(days=age_days))
        return event

    def test_removes_only_old_rows_and_keeps_the_newest_heartbeat(self):
        old, older, recent = self.heartbeat(30), self.heartbeat(20), self.heartbeat(1)
        self.event('old', 120), self.event('fresh', 5)
        self.run_command()
        self.assertEqual(list(WorkerHeartbeat.objects.values_list('pk', flat=True)), [recent.pk])
        self.assertEqual(list(DeliveryEvent.objects.values_list('fingerprint', flat=True)), ['fresh'])

    def test_newest_heartbeat_survives_even_when_old(self):
        self.heartbeat(30)
        newest = self.heartbeat(10)
        self.run_command()
        self.assertEqual(list(WorkerHeartbeat.objects.values_list('pk', flat=True)), [newest.pk])

    def test_dry_run_changes_nothing(self):
        self.heartbeat(30), self.heartbeat(20)
        self.event('old', 120)
        self.assertIn('Simulación', self.run_command('--dry-run'))
        self.assertEqual(WorkerHeartbeat.objects.count(), 2)
        self.assertEqual(DeliveryEvent.objects.count(), 1)

    def test_invalid_retention_is_rejected(self):
        with self.assertRaises(CommandError):
            self.run_command('--event-days', '0')


class ProcessingEdgeCaseTests(TestCase):
    def setUp(self):
        self.contact = WhatsAppContact.objects.create(phone_number='51944445555')

    @patch('applications.whatsapp.services.answer_sheet_message')
    def test_inactive_contact_is_not_answered(self, answer):
        WhatsAppContact.objects.filter(pk=self.contact.pk).update(is_active=False)
        enqueue_message(self.contact.phone_number, '', 'inactive-1', 'Consulta')
        process_pending_messages(force=True)
        answer.assert_not_called()
        self.assertFalse(Outbox.objects.exists())
        self.assertEqual(WhatsAppMessage.objects.get(message_id='inactive-1').status, WhatsAppMessage.PROCESSED)

    @override_settings(WHATSAPP_MAX_MESSAGE_LENGTH=10)
    def test_truncation_is_logged_without_the_full_number(self):
        with self.assertLogs('applications.whatsapp.services', level='WARNING') as logs:
            enqueue_message(self.contact.phone_number, '', 'long-1', 'x' * 25)
        self.assertEqual(WhatsAppMessage.objects.get(message_id='long-1').text, 'x' * 10)
        self.assertIn('length=25', logs.output[0])
        self.assertIn('***5555', logs.output[0])
        self.assertNotIn(self.contact.phone_number, logs.output[0])

    @patch('applications.whatsapp.services._finish_processing', side_effect=[RuntimeError('db down'), True])
    @patch('applications.whatsapp.services.answer_sheet_message', return_value='Respuesta lista')
    def test_commit_failure_is_not_retried_as_an_ai_error(self, answer, finish):
        enqueue_message(self.contact.phone_number, '', 'commit-1', 'Consulta')
        with self.assertRaises(RuntimeError):
            process_pending_messages(force=True)
        # One attempt with the real answer; no second call replacing it with the apology.
        finish.assert_called_once()
        self.assertEqual(finish.call_args.args[2], 'Respuesta lista')
        self.assertNotEqual(finish.call_args.args[2], PROCESSING_ERROR)
