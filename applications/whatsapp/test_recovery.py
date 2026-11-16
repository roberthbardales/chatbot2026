import hashlib
import hmac
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier, Event, Lock
from unittest.mock import patch

from django.db import close_old_connections
from django.test import Client, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import Outbox, WhatsAppContact, WhatsAppMessage
from .services import enqueue_message, enqueue_outbox, process_outbox, process_pending_messages


class SimulatedCrash(BaseException):
    """Fault injection that bypasses ordinary processing error handling."""


def isolated_connection(function):
    close_old_connections()
    try:
        return function()
    finally:
        close_old_connections()


@override_settings(WHATSAPP_DEFAULT_SOURCE='google_sheets', MESSAGE_DEBOUNCE_SECONDS=0,
                   WHATSAPP_RATE_PER_CONTACT=100, CHAT_MAX_CONCURRENT_PER_PRINCIPAL=1,
                   WHATSAPP_PROCESSING_TIMEOUT_SECONDS=600, WHATSAPP_OUTBOX_LOCK_SECONDS=600)
class WorkerConcurrencyTests(TransactionTestCase):
    def contact(self, suffix):
        return WhatsAppContact.objects.create(phone_number=f'5190000000{suffix}')

    def queued(self, contact, content):
        message = WhatsAppMessage.objects.create(contact=contact, message_id=content,
                                                 text=content, is_from_customer=False)
        return enqueue_outbox(contact, message, content)[0]

    def test_two_workers_only_generate_one_response_for_same_pending_batch(self):
        contact = self.contact(1)
        enqueue_message(contact.phone_number, '', 'same-batch', 'Consulta')
        barrier = Barrier(2)

        def process():
            barrier.wait(timeout=10)
            return process_pending_messages(force=True)

        with patch('applications.whatsapp.services.answer_sheet_message', return_value='Respuesta') as answer:
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(lambda _: isolated_connection(process), range(2)))
        self.assertEqual(sum(results), 1)
        answer.assert_called_once()
        self.assertEqual(Outbox.objects.count(), 1)
        self.assertEqual(WhatsAppMessage.objects.filter(is_from_customer=False).count(), 1)

    def test_new_message_while_same_contact_is_busy_is_deferred_not_lost(self):
        contact = self.contact(1)
        enqueue_message(contact.phone_number, '', 'in-flight-first', 'Primera')
        entered, release = Event(), Event()

        def answer(*args, **kwargs):
            entered.set()
            if not release.wait(timeout=15):
                raise RuntimeError('Test synchronization timed out')
            return 'Respuesta'

        with patch('applications.whatsapp.services.answer_sheet_message', side_effect=answer):
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(isolated_connection, lambda: process_pending_messages(force=True))
                try:
                    self.assertTrue(entered.wait(timeout=10))
                    enqueue_message(contact.phone_number, '', 'in-flight-second', 'Segunda')
                    self.assertEqual(process_pending_messages(force=True), 0)
                    second = WhatsAppMessage.objects.get(message_id='in-flight-second')
                    self.assertEqual(second.status, WhatsAppMessage.PENDING)
                    self.assertIsNotNone(second.retry_after_at)
                finally:
                    release.set()
                self.assertEqual(future.result(timeout=10), 1)
        WhatsAppMessage.objects.filter(message_id='in-flight-second').update(retry_after_at=timezone.now())
        with patch('applications.whatsapp.services.answer_sheet_message', return_value='Respuesta segunda'):
            self.assertEqual(process_pending_messages(force=True), 1)
        self.assertEqual(Outbox.objects.count(), 2)

    def test_two_workers_send_independent_parts_once(self):
        for i in range(4):
            self.queued(self.contact(i), f'Independent-{i}')
        barrier = Barrier(2)
        calls, mutex = [], Lock()

        def send(phone, text):
            with mutex:
                calls.append(text)
            return {'messages': [{'id': 'wamid.' + text}]}

        def process():
            barrier.wait(timeout=10)
            return process_outbox()

        with patch('applications.whatsapp.services.send_text_message', side_effect=send):
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(lambda _: isolated_connection(process), range(2)))
        self.assertEqual(sum(results), 4)
        self.assertEqual(sorted(calls), [f'Independent-{i}' for i in range(4)])
        self.assertEqual(Outbox.objects.filter(sent=True, attempts=1).count(), 4)

    def test_second_worker_cannot_send_next_part_while_first_is_in_flight(self):
        contact = self.contact(1)
        message = WhatsAppMessage.objects.create(contact=contact, message_id='multipart-in-flight', is_from_customer=False)
        parts = enqueue_outbox(contact, message, 'x' * 9000)
        entered, release = Event(), Event()
        calls = []

        def send(phone, text):
            index = next(i for i, part in enumerate(parts) if part.content == text)
            self.assertFalse(Outbox.objects.filter(group_id=parts[0].group_id, part_number__lt=index + 1, sent=False).exists())
            calls.append(index + 1)
            if index == 0:
                entered.set()
                if not release.wait(timeout=15):
                    raise RuntimeError('Test synchronization timed out')
            return {'messages': [{'id': f'wamid.part-{index + 1}'}]}

        with patch('applications.whatsapp.services.send_text_message', side_effect=send):
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(isolated_connection, process_outbox)
                try:
                    self.assertTrue(entered.wait(timeout=10))
                    self.assertEqual(process_outbox(), 0)
                    self.assertEqual(calls, [1])
                finally:
                    release.set()
                self.assertEqual(future.result(timeout=10), 3)
        self.assertEqual(calls, [1, 2, 3])


@override_settings(WHATSAPP_DEFAULT_SOURCE='google_sheets', MESSAGE_DEBOUNCE_SECONDS=0,
                   WHATSAPP_PROCESSING_TIMEOUT_SECONDS=600, WHATSAPP_OUTBOX_LOCK_SECONDS=600)
class CrashRecoveryTests(TransactionTestCase):
    def setUp(self):
        self.contact = WhatsAppContact.objects.create(phone_number='51922223333')
        enqueue_message(self.contact.phone_number, '', 'crash-input', 'Consulta')

    def expire_input(self):
        WhatsAppMessage.objects.filter(message_id='crash-input').update(
            processing_started_at=timezone.now() - timedelta(seconds=601))

    def test_crash_after_claim_recovers_and_generates_once(self):
        with patch('applications.whatsapp.services.answer_sheet_message', side_effect=SimulatedCrash):
            with self.assertRaises(SimulatedCrash):
                process_pending_messages(force=True)
        self.assertEqual(WhatsAppMessage.objects.get(message_id='crash-input').status, WhatsAppMessage.PROCESSING)
        self.assertFalse(Outbox.objects.exists())
        self.expire_input()
        with patch('applications.whatsapp.services.answer_sheet_message', return_value='Recuperado') as answer:
            self.assertEqual(process_pending_messages(force=True), 1)
        self.assertEqual(Outbox.objects.count(), 1)
        self.assertEqual(WhatsAppMessage.objects.get(message_id='crash-input').processing_attempts, 2)
        answer.assert_called_once()

    def test_crash_during_response_transaction_rolls_back_partial_state(self):
        with patch('applications.whatsapp.services.answer_sheet_message', return_value='Respuesta'):
            with patch('applications.whatsapp.services.enqueue_outbox', side_effect=SimulatedCrash):
                with self.assertRaises(SimulatedCrash):
                    process_pending_messages(force=True)
        self.assertFalse(WhatsAppMessage.objects.filter(is_from_customer=False).exists())
        self.assertFalse(Outbox.objects.exists())
        self.assertEqual(WhatsAppMessage.objects.get(message_id='crash-input').status, WhatsAppMessage.PROCESSING)
        self.expire_input()
        with patch('applications.whatsapp.services.answer_sheet_message', return_value='Recuperado'):
            process_pending_messages(force=True)
        self.assertEqual(WhatsAppMessage.objects.filter(is_from_customer=False).count(), 1)
        self.assertEqual(Outbox.objects.count(), 1)

    def test_restart_after_response_commit_does_not_regenerate_it(self):
        with patch('applications.whatsapp.services.answer_sheet_message', return_value='Confirmado') as answer:
            process_pending_messages(force=True)
            self.assertEqual(process_pending_messages(force=True), 0)
        answer.assert_called_once()
        with patch('applications.whatsapp.services.send_text_message', return_value={'messages': [{'id': 'wamid.committed'}]}) as send:
            self.assertEqual(process_outbox(), 1)
            self.assertEqual(process_outbox(), 0)
        send.assert_called_once()

    def test_lost_acceptance_before_commit_exposes_documented_duplicate_risk(self):
        with patch('applications.whatsapp.services.answer_sheet_message', return_value='Respuesta'):
            process_pending_messages(force=True)
        remote_acceptances = []

        def accept(phone, text):
            remote_acceptances.append(text)
            return {'messages': [{'id': f'wamid.acceptance-{len(remote_acceptances)}'}]}

        with patch('applications.whatsapp.services.send_text_message', side_effect=accept):
            with patch('applications.whatsapp.services._finish_outbox', side_effect=SimulatedCrash):
                with self.assertRaises(SimulatedCrash):
                    process_outbox()
            item = Outbox.objects.get()
            self.assertFalse(item.sent)
            self.assertIsNone(item.meta_message_id)
            self.assertEqual(process_outbox(), 0)  # Active lease prevents immediate repetition.
            Outbox.objects.filter(pk=item.pk).update(locked_at=timezone.now() - timedelta(seconds=601))
            self.assertEqual(process_outbox(), 1)
        self.assertEqual(len(remote_acceptances), 2)  # Explicit limitation, not exactly-once delivery.
        item.refresh_from_db()
        self.assertEqual(item.attempts, 2)
        self.assertEqual(item.meta_message_id, 'wamid.acceptance-2')


@override_settings(WHATSAPP_APP_SECRET='concurrent-webhook', WHATSAPP_PHONE_NUMBER_ID='phone-123')
class ConcurrentWebhookTests(TransactionTestCase):
    def test_simultaneous_duplicate_notifications_are_persisted_once(self):
        body = json.dumps({'entry': [{'changes': [{'value': {
            'metadata': {'phone_number_id': 'phone-123'},
            'messages': [{'id': 'wamid.duplicate', 'from': '51911112222', 'type': 'text', 'text': {'body': 'Hola'}}],
        }}]}]}).encode()
        signature = 'sha256=' + hmac.new(b'concurrent-webhook', body, hashlib.sha256).hexdigest()
        barrier = Barrier(2)

        def notify():
            barrier.wait(timeout=10)
            return Client().post(reverse('webhook'), data=body, content_type='application/json',
                                 HTTP_X_HUB_SIGNATURE_256=signature).status_code

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: isolated_connection(notify), range(2)))
        self.assertEqual(results, [200, 200])
        self.assertEqual(WhatsAppContact.objects.count(), 1)
        self.assertEqual(WhatsAppMessage.objects.count(), 1)
        self.assertFalse(Outbox.objects.exists())
