import io
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier, Event
from unittest.mock import Mock, patch

from django.core.management import call_command
from django.db import close_old_connections
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from applications.users.models import User
from .limits import (LimitExceeded, ai_admission, enforce_rates,
                     identity, principal_slot)
from .models import AIUsageDay, Conversation, Message, UsageBucket, UsageLease
from .providers import OPENROUTER, ProviderError, _openrouter, generate_response


@override_settings(CHAT_RATE_WINDOW_SECONDS=60, CHAT_RATE_PER_SESSION=2,
                   CHAT_RATE_PER_USER=3, CHAT_RATE_PER_IP=20,
                   CHAT_MAX_CONCURRENT_PER_PRINCIPAL=1)
class WebAdmissionTests(TestCase):
    def setUp(self):
        self.answer = patch('applications.chat.views.answer_message', return_value='Respuesta').start()
        self.addCleanup(patch.stopall)

    def send(self, client=None, **extra):
        return (client or self.client).post(reverse('chat:send'), {'message': 'Hola'}, **extra)

    def test_session_limit_returns_429_and_no_new_conversation_or_message(self):
        self.assertEqual(self.send().status_code, 200)
        self.assertEqual(self.send().status_code, 200)
        response = self.send()
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.json()['code'], 'rate_limit')
        self.assertEqual(response['Retry-After'], str(response.json()['retry_after']))
        self.assertTrue(1 <= response.json()['retry_after'] <= 60)
        self.assertEqual(Conversation.objects.count(), 2)
        self.assertEqual(Message.objects.count(), 4)
        self.assertEqual(self.answer.call_count, 2)

    def test_other_session_is_not_blocked_by_a_session_limit(self):
        self.send()
        self.send()
        self.assertEqual(self.send().status_code, 429)
        self.assertEqual(self.send(Client()).status_code, 200)

    @override_settings(CHAT_RATE_PER_IP=1)
    def test_new_session_or_forged_forwarded_header_does_not_bypass_ip_limit(self):
        self.assertEqual(self.send(REMOTE_ADDR='192.0.2.1', HTTP_X_FORWARDED_FOR='198.51.100.1').status_code, 200)
        self.assertEqual(self.send(Client(), REMOTE_ADDR='192.0.2.1', HTTP_X_FORWARDED_FOR='198.51.100.2').status_code, 429)
        self.assertEqual(self.send(Client(), REMOTE_ADDR='192.0.2.2').status_code, 200)
        self.assertTrue(all('192.0.2' not in key for key in UsageBucket.objects.values_list('key', flat=True)))

    @override_settings(CHAT_RATE_PER_IP=1)
    def test_ipv6_privacy_addresses_share_prefix_limit(self):
        self.assertEqual(self.send(REMOTE_ADDR='2001:db8:1:1::1').status_code, 200)
        self.assertEqual(self.send(Client(), REMOTE_ADDR='2001:db8:1:1::2').status_code, 429)

    @override_settings(CHAT_RATE_PER_USER=1)
    def test_user_limit_applies_across_sessions_and_ips(self):
        user = User.objects.create_user('limited@test.com')
        self.client.force_login(user)
        other = Client()
        other.force_login(user)
        self.assertEqual(self.send(REMOTE_ADDR='192.0.2.1').status_code, 200)
        self.assertEqual(self.send(other, REMOTE_ADDR='192.0.2.2').status_code, 429)

    def test_window_expires(self):
        now = timezone.now()
        with patch('applications.chat.limits.timezone.now', return_value=now):
            self.send()
            self.send()
            self.assertEqual(self.send().status_code, 429)
        with patch('applications.chat.limits.timezone.now', return_value=now + timedelta(seconds=60)):
            self.assertEqual(self.send().status_code, 200)

    def test_principal_slot_blocks_overlap_and_releases_after_exception(self):
        with self.assertRaises(RuntimeError):
            with principal_slot('test-principal'):
                with self.assertRaises(LimitExceeded):
                    with principal_slot('test-principal'):
                        self.fail('Second slot should be blocked')
                with principal_slot('different-principal'):
                    pass
                raise RuntimeError('interrupted')
        self.assertFalse(UsageLease.objects.exists())
        with principal_slot('test-principal'):
            pass

    def test_web_concurrency_error_returns_429_before_answer(self):
        user = User.objects.create_user('busy@test.com')
        self.client.force_login(user)
        with principal_slot(identity('user', user.pk)):
            response = self.send()
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.json()['code'], 'concurrency_limit')
        self.answer.assert_not_called()

    def test_expired_request_lease_is_reclaimed(self):
        UsageLease.objects.create(scope='request:test', expires_at=timezone.now() - timedelta(seconds=1))
        with principal_slot('test'):
            self.assertEqual(UsageLease.objects.count(), 1)
        self.assertFalse(UsageLease.objects.exists())

    def test_invalid_message_does_not_consume_rate(self):
        self.client.post(reverse('chat:send'), {'message': ' '})
        self.assertFalse(UsageBucket.objects.exists())

    def test_denial_does_not_partially_increment_other_identity_counters(self):
        enforce_rates([('a', 10), ('b', 1)])
        with self.assertRaises(LimitExceeded):
            enforce_rates([('a', 10), ('b', 1)])
        self.assertEqual(UsageBucket.objects.get(key='rate:a').count, 1)

    def test_cleanup_preserves_active_leases_and_current_usage(self):
        now = timezone.now()
        old = now - timedelta(days=10)
        UsageBucket.objects.create(key='rate:old', window_started_at=old)
        UsageBucket.objects.create(key='rate:current', window_started_at=now)
        UsageBucket.objects.create(key='lock:request:active', window_started_at=old)
        UsageLease.objects.create(scope='request:active', expires_at=now + timedelta(seconds=60))
        UsageLease.objects.create(scope='expired', expires_at=now - timedelta(seconds=1))
        AIUsageDay.objects.create(day=now.date())
        AIUsageDay.objects.create(day=(now - timedelta(days=100)).date())
        call_command('cleanup_chat_limits', stdout=io.StringIO())
        self.assertFalse(UsageBucket.objects.filter(key='rate:old').exists())
        self.assertTrue(UsageBucket.objects.filter(key='rate:current').exists())
        self.assertTrue(UsageBucket.objects.filter(key='lock:request:active').exists())
        self.assertEqual(UsageLease.objects.count(), 1)
        self.assertEqual(AIUsageDay.objects.count(), 1)


@override_settings(OPENROUTER_API_KEY='test-key', SYSTEM_PROMPT='s',
                   AI_MAX_OUTPUT_TOKENS=10, AI_MAX_PROMPT_CHARACTERS=1000,
                   AI_DAILY_REQUEST_LIMIT=2, AI_DAILY_TOKEN_LIMIT=10000,
                   AI_MAX_CONCURRENT_REQUESTS=1, AI_IMAGE_TOKEN_RESERVE=100)
class AIBudgetTests(TestCase):
    def setUp(self):
        self.handler = Mock(return_value='Respuesta')
        self.registry = patch.dict('applications.chat.providers._PROVIDERS', {OPENROUTER: self.handler})
        self.registry.start()
        self.addCleanup(self.registry.stop)

    def test_request_budget_blocks_before_provider_call(self):
        generate_response(OPENROUTER, 'hola')
        generate_response(OPENROUTER, 'hola')
        with self.assertRaises(LimitExceeded) as caught:
            generate_response(OPENROUTER, 'hola')
        self.assertEqual(caught.exception.code, 'daily_budget')
        self.assertEqual(self.handler.call_count, 2)
        self.assertEqual(AIUsageDay.objects.get().requests, 2)
        self.assertFalse(UsageLease.objects.exists())

    @override_settings(AI_DAILY_TOKEN_LIMIT=80)
    def test_token_reservation_blocks_calls_over_budget(self):
        # UTF-8 bytes + system + framing allowance + maximum output = 79.
        generate_response(OPENROUTER, 'hola')
        self.assertEqual(AIUsageDay.objects.get().reserved_tokens, 79)
        with self.assertRaises(LimitExceeded):
            generate_response(OPENROUTER, 'hola')
        self.handler.assert_called_once()

    def test_image_reservation_includes_configured_allowance(self):
        generate_response(OPENROUTER, 'hola', image_data_url='data:image/jpeg;base64,abc')
        self.assertEqual(AIUsageDay.objects.get().reserved_tokens, 179)

    def test_prompt_too_large_is_blocked_without_consuming_budget(self):
        with self.assertRaises(LimitExceeded) as caught:
            generate_response(OPENROUTER, 'x' * 1001)
        self.assertEqual(caught.exception.code, 'context_limit')
        self.assertFalse(AIUsageDay.objects.exists())
        self.handler.assert_not_called()

    def test_failed_call_still_consumes_budget_and_releases_slot(self):
        self.handler.side_effect = ProviderError('offline')
        with self.assertRaises(ProviderError):
            generate_response(OPENROUTER, 'hola')
        self.assertEqual(AIUsageDay.objects.get().requests, 1)
        self.assertFalse(UsageLease.objects.exists())

    def test_global_concurrency_blocks_and_does_not_consume_second_reservation(self):
        with ai_admission('first'):
            with self.assertRaises(LimitExceeded) as caught:
                generate_response(OPENROUTER, 'second')
            self.assertEqual(caught.exception.code, 'ai_concurrency_limit')
        self.assertEqual(AIUsageDay.objects.get().requests, 1)
        self.handler.assert_not_called()
        self.assertFalse(UsageLease.objects.exists())

    def test_expired_ai_slot_is_reclaimed(self):
        UsageLease.objects.create(scope='ai:global', expires_at=timezone.now() - timedelta(seconds=1))
        generate_response(OPENROUTER, 'hola')
        self.assertFalse(UsageLease.objects.exists())

    def test_new_utc_day_has_new_budget(self):
        now = timezone.now()
        with patch('applications.chat.limits.timezone.now', return_value=now):
            generate_response(OPENROUTER, 'hola')
            generate_response(OPENROUTER, 'hola')
        with patch('applications.chat.limits.timezone.now', return_value=now + timedelta(days=1)):
            generate_response(OPENROUTER, 'hola')
        self.assertEqual(AIUsageDay.objects.count(), 2)

    @override_settings(AI_BUDGET_ALERT_PERCENT=50)
    @patch('applications.chat.limits.logger.warning')
    def test_threshold_alert_is_logged_once(self, warning):
        with self.captureOnCommitCallbacks(execute=True):
            generate_response(OPENROUTER, 'hola')
        with self.captureOnCommitCallbacks(execute=True):
            generate_response(OPENROUTER, 'hola')
        alerts = [call for call in warning.call_args_list if call.args[0].startswith('AI budget alert:')]
        self.assertEqual(len(alerts), 1)

    def test_budget_reservation_survives_web_message_failure(self):
        def answer(*args):
            generate_response(OPENROUTER, 'hola')
            raise RuntimeError('failure after external call')
        with patch('applications.chat.views.answer_message', side_effect=answer):
            response = self.client.post(reverse('chat:send'), {'message': 'Hola'})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(AIUsageDay.objects.get().requests, 1)
        self.assertFalse(Message.objects.exists())
        self.assertFalse(Conversation.objects.exists())

    def test_budget_denial_returns_web_429_without_persistence(self):
        AIUsageDay.objects.create(day=timezone.now().date(), requests=2)
        with patch('applications.chat.views.answer_message', side_effect=lambda *args: generate_response(OPENROUTER, 'hola')):
            response = self.client.post(reverse('chat:send'), {'message': 'Hola'})
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.json()['code'], 'daily_budget')
        self.assertFalse(Message.objects.exists())
        self.assertFalse(Conversation.objects.exists())
        self.handler.assert_not_called()

    def test_usage_command_is_read_only(self):
        output = io.StringIO()
        call_command('ai_usage', stdout=output)
        self.assertIn('no facturación real', output.getvalue())
        self.assertFalse(AIUsageDay.objects.exists())
        self.handler.assert_not_called()

    @override_settings(OPENROUTER_API_KEY='')
    def test_missing_key_does_not_reserve_budget(self):
        with self.assertRaises(ProviderError):
            generate_response(OPENROUTER, 'hola')
        self.assertFalse(AIUsageDay.objects.exists())

    @patch('applications.chat.providers.urlopen')
    def test_openrouter_payload_caps_output_tokens(self, urlopen):
        urlopen.return_value.__enter__.return_value.read.return_value = json.dumps({
            'choices': [{'message': {'content': 'Hola'}}],
        }).encode()
        self.assertEqual(_openrouter('Hola'), 'Hola')
        payload = json.loads(urlopen.call_args.args[0].data)
        self.assertEqual(payload['max_tokens'], 10)


@override_settings(CHAT_RATE_WINDOW_SECONDS=60, AI_MAX_CONCURRENT_REQUESTS=1,
                   AI_DAILY_REQUEST_LIMIT=10, AI_DAILY_TOKEN_LIMIT=100000,
                   AI_MAX_PROMPT_CHARACTERS=1000, SYSTEM_PROMPT='s')
class SharedAdmissionConcurrencyTests(TransactionTestCase):
    def test_two_connections_cannot_exceed_same_rate_bucket(self):
        barrier = Barrier(2)

        def attempt():
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                try:
                    enforce_rates([('same-principal', 1)])
                    return True
                except LimitExceeded:
                    return False
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: attempt(), range(2)))
        self.assertEqual(sorted(results), [False, True])
        self.assertEqual(UsageBucket.objects.get(key='rate:same-principal').count, 1)

    def test_ai_concurrency_is_shared_across_connections(self):
        entered, release = Event(), Event()

        def hold_slot():
            close_old_connections()
            try:
                with ai_admission('hola'):
                    entered.set()
                    if not release.wait(timeout=15):
                        raise RuntimeError('Test synchronization timed out')
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(hold_slot)
            try:
                self.assertTrue(entered.wait(timeout=10))
                with self.assertRaises(LimitExceeded):
                    with ai_admission('second'):
                        self.fail('Shared slot should be busy')
            finally:
                release.set()
            future.result(timeout=10)
        self.assertEqual(AIUsageDay.objects.get().requests, 1)
        self.assertFalse(UsageLease.objects.exists())
