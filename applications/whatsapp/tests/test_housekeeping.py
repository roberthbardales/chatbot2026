"""Limpieza de datos y casos límite del procesamiento."""
import io
from datetime import timedelta
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.utils import timezone

from ..models import Outbox, WhatsAppContact, WhatsAppMessage, WorkerHeartbeat
from ..services import PROCESSING_ERROR, enqueue_message, process_pending_messages


class CleanupWhatsAppDataTests(TestCase):
    def run_command(self, *args):
        output = io.StringIO()
        call_command('cleanup_whatsapp_data', *args, stdout=output)
        return output.getvalue()

    def heartbeat(self, age_days):
        stamp = timezone.now() - timedelta(days=age_days)
        return WorkerHeartbeat.objects.create(heartbeat_at=stamp, last_progress_at=stamp, stopped_at=stamp)

    def test_removes_only_old_rows_and_keeps_the_newest_heartbeat(self):
        self.heartbeat(30), self.heartbeat(20)
        recent = self.heartbeat(1)
        self.run_command()
        self.assertEqual(list(WorkerHeartbeat.objects.values_list('pk', flat=True)), [recent.pk])

    def test_newest_heartbeat_survives_even_when_old(self):
        self.heartbeat(30)
        newest = self.heartbeat(10)
        self.run_command()
        self.assertEqual(list(WorkerHeartbeat.objects.values_list('pk', flat=True)), [newest.pk])

    def test_dry_run_changes_nothing(self):
        self.heartbeat(30), self.heartbeat(20)
        self.assertIn('Simulación', self.run_command('--dry-run'))
        self.assertEqual(WorkerHeartbeat.objects.count(), 2)

    def test_invalid_retention_is_rejected(self):
        with self.assertRaises(CommandError):
            self.run_command('--heartbeat-days', '0')


class ProcessingEdgeCaseTests(TestCase):
    def setUp(self):
        self.contact = WhatsAppContact.objects.create(phone_number='51944445555')

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
        self.assertFalse(Outbox.objects.exists())
