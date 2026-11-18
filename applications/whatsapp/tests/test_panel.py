"""Panel de operadores: permisos, datos que expone y acciones (modo humano y envío manual)."""
import json
from datetime import timedelta
from unittest.mock import patch

from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from applications.core.models import AIUsageDay
from applications.users.models import User

from ..models import Outbox, WhatsAppContact, WhatsAppMessage, WorkerHeartbeat


def post_json(client, url, payload):
    return client.post(url, data=json.dumps(payload), content_type='application/json')


class PanelAccessTests(TestCase):
    def setUp(self):
        self.contact = WhatsAppContact.objects.create(phone_number='51944440000')
        self.urls = [reverse('dashboard'), reverse('system-status'), reverse('conversations'),
                     reverse('conversation-messages', args=[self.contact.pk])]

    def login(self, **kwargs):
        user = User.objects.create_user(f'u{User.objects.count()}@test.com', **kwargs)
        self.client.force_login(user)
        return user

    def test_anonymous_users_are_sent_to_login(self):
        for url in self.urls:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 302)
                self.assertIn('login', response['Location'])

    def test_logged_in_users_without_a_management_role_are_forbidden(self):
        self.login(occupation=User.USUARIO)
        for url in self.urls:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 403)
        mode_url = reverse('change-mode', args=[self.contact.pk])
        send_url = reverse('send-manual-message', args=[self.contact.pk])
        self.assertEqual(post_json(self.client, mode_url, {'mode': 'human'}).status_code, 403)
        self.assertEqual(post_json(self.client, send_url, {'content': 'x'}).status_code, 403)
        self.contact.refresh_from_db()
        self.assertEqual(self.contact.mode, WhatsAppContact.AI)
        self.assertFalse(Outbox.objects.exists())

    def test_admin_employee_and_superuser_can_enter(self):
        for kwargs in ({'occupation': User.ADMINISTRADOR}, {'occupation': User.EMPLEADO}):
            with self.subTest(**kwargs):
                self.login(**kwargs)
                for url in self.urls:
                    self.assertEqual(self.client.get(url).status_code, 200)
        user = User.objects.create_superuser('root@test.com', 'Passw0rd!123')
        self.client.force_login(user)
        self.assertEqual(self.client.get(reverse('dashboard')).status_code, 200)

    def test_actions_require_post_and_a_csrf_token(self):
        user = self.login(occupation=User.EMPLEADO)
        url = reverse('change-mode', args=[self.contact.pk])
        self.assertEqual(self.client.get(url).status_code, 405)
        strict = Client(enforce_csrf_checks=True)
        strict.force_login(user)
        self.assertEqual(post_json(strict, url, {'mode': 'human'}).status_code, 403)
        self.contact.refresh_from_db()
        self.assertEqual(self.contact.mode, WhatsAppContact.AI)

    def test_dashboard_page_carries_the_csrf_token(self):
        self.login(occupation=User.EMPLEADO)
        response = self.client.get(reverse('dashboard'))
        self.assertContains(response, 'id="csrf-token"')
        self.assertContains(response, 'Panel de WhatsApp')


class PanelDataTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('panel@test.com', occupation=User.EMPLEADO)
        self.client.force_login(self.user)
        self.contact = WhatsAppContact.objects.create(phone_number='51955550001', display_name='Ana Pérez',
                                                      last_message_at=timezone.now())

    def message(self, text='Hola', **kwargs):
        kwargs.setdefault('message_id', f'm-{WhatsAppMessage.objects.count()}')
        return WhatsAppMessage.objects.create(contact=self.contact, text=text, **kwargs)

    def reply(self, text='Respuesta', manual=False):
        return self.message(text, is_from_customer=False, is_manual=manual, status=WhatsAppMessage.PROCESSED)

    def outbox(self, message, **kwargs):
        return Outbox.objects.create(contact=self.contact, message=message, content=message.text, **kwargs)

    def test_status_reports_worker_queues_catalog_and_ai(self):
        now = timezone.now()
        WorkerHeartbeat.objects.create(heartbeat_at=now, last_progress_at=now, last_success_at=now, phase='idle')
        self.message(status=WhatsAppMessage.PENDING)
        self.message(status=WhatsAppMessage.FAILED)
        self.outbox(self.reply())
        self.outbox(self.reply(), failed_at=now)
        AIUsageDay.objects.create(day=now.date(), requests=7, reserved_tokens=1234)
        WhatsAppContact.objects.create(phone_number='51955550002', mode=WhatsAppContact.HUMAN)
        data = self.client.get(reverse('system-status')).json()
        self.assertEqual(data['worker']['state'], 'running')
        self.assertEqual((data['queues']['incoming_pending'], data['queues']['incoming_failed']), (1, 1))
        self.assertEqual((data['queues']['outbox_pending'], data['queues']['outbox_failed']), (1, 1))
        self.assertEqual((data['ai']['requests_today'], data['ai']['reserved_tokens_today']), (7, 1234))
        self.assertEqual(data['human_contacts'], 1)
        self.assertIn('freshness', data['catalog'])

    def test_conversation_list_search_and_waiting_counter(self):
        WhatsAppContact.objects.create(phone_number='51955550003', display_name='Luis')
        self.message(status=WhatsAppMessage.PENDING)
        self.message(status=WhatsAppMessage.PROCESSED)
        everyone = self.client.get(reverse('conversations')).json()
        self.assertEqual(len(everyone['conversations']), 2)
        found = self.client.get(reverse('conversations'), {'q': 'ana'}).json()['conversations']
        self.assertEqual([(item['name'], item['waiting']) for item in found], [('Ana Pérez', 1)])
        self.assertEqual(len(self.client.get(reverse('conversations'), {'q': '5555000'}).json()['conversations']), 2)
        self.assertEqual(self.client.get(reverse('conversations'), {'q': 'nadie'}).json()['conversations'], [])

    def test_conversation_list_is_paginated_and_validates_the_offset(self):
        for index in range(60):
            WhatsAppContact.objects.create(phone_number=f'5196{index:07d}')
        first = self.client.get(reverse('conversations')).json()
        self.assertEqual((len(first['conversations']), first['has_more'], first['next_offset']), (50, True, 50))
        second = self.client.get(reverse('conversations'), {'offset': 50}).json()
        self.assertEqual((len(second['conversations']), second['has_more']), (11, False))
        for offset in ('-1', 'x', '2000000'):
            self.assertEqual(self.client.get(reverse('conversations'), {'offset': offset}).status_code, 400)

    def test_messages_show_who_wrote_each_one(self):
        self.message('Quiero un serum', media_id='media-1')
        self.reply('Te muestro opciones')
        self.reply('Hola, soy Marta', manual=True)
        data = self.client.get(reverse('conversation-messages', args=[self.contact.pk])).json()
        self.assertEqual([m['sender'] for m in data['messages']], ['customer', 'bot', 'operator'])
        self.assertTrue(data['messages'][0]['has_image'])
        self.assertEqual(data['conversation']['mode'], 'ai')

    def test_delivery_state_of_each_reply(self):
        now = timezone.now()
        cases = {
            'queued': {},
            'retrying': {'attempts': 1, 'next_attempt_at': now + timedelta(minutes=5), 'last_error': '[temporary] x'},
            'sent': {'sent': True, 'delivery_status': 'sent'},
            'delivered': {'sent': True, 'delivery_status': 'delivered'},
            'read': {'sent': True, 'delivery_status': 'read'},
            'failed': {'sent': True, 'delivery_status': 'failed', 'delivery_error': '131047 Re-engagement'},
        }
        expected = {}
        for state, fields in cases.items():
            message = self.reply(state)
            self.outbox(message, **fields)
            expected[message.text] = state
        data = self.client.get(reverse('conversation-messages', args=[self.contact.pk])).json()
        got = {m['text']: m['delivery']['state'] for m in data['messages']}
        self.assertEqual(got, expected)
        failed = next(m for m in data['messages'] if m['text'] == 'failed')
        self.assertIn('131047', failed['delivery']['error'])

    def test_multipart_reply_reports_progress_and_the_slowest_part(self):
        message = self.reply('larga')
        self.outbox(message, sent=True, delivery_status='read', part_number=1)
        self.outbox(message, part_number=2)
        data = self.client.get(reverse('conversation-messages', args=[self.contact.pk])).json()
        self.assertEqual(data['messages'][0]['delivery'], {'state': 'sending', 'parts': 2, 'accepted': 1, 'error': ''})
        Outbox.objects.filter(part_number=2).update(sent=True, delivery_status='delivered')
        data = self.client.get(reverse('conversation-messages', args=[self.contact.pk])).json()
        self.assertEqual(data['messages'][0]['delivery']['state'], 'delivered')

    def test_pending_outbox_counts_only_bot_replies(self):
        url = reverse('conversation-messages', args=[self.contact.pk])
        self.outbox(self.reply('del operador', manual=True))
        self.assertEqual(self.client.get(url).json()['pending_outbox'], 0)
        self.outbox(self.reply('del bot'))
        self.assertEqual(self.client.get(url).json()['pending_outbox'], 1)
        Outbox.objects.filter(message__text='del bot').update(sent=True)
        self.assertEqual(self.client.get(url).json()['pending_outbox'], 0)

    def test_unknown_contact_is_404(self):
        self.assertEqual(self.client.get(reverse('conversation-messages', args=[999999])).status_code, 404)


class PanelActionTests(TestCase):
    def setUp(self):
        self.client.force_login(User.objects.create_user('actions@test.com', occupation=User.ADMINISTRADOR))
        self.contact = WhatsAppContact.objects.create(phone_number='51966660000')
        self.meta = patch('applications.whatsapp.services.send_text_message').start()
        self.addCleanup(patch.stopall)

    def test_mode_can_be_changed_both_ways(self):
        url = reverse('change-mode', args=[self.contact.pk])
        self.assertEqual(post_json(self.client, url, {'mode': 'human'}).json(), {'mode': 'human'})
        self.contact.refresh_from_db()
        self.assertEqual(self.contact.mode, WhatsAppContact.HUMAN)
        self.assertEqual(post_json(self.client, url, {'mode': 'ai'}).json(), {'mode': 'ai'})
        self.contact.refresh_from_db()
        self.assertEqual(self.contact.mode, WhatsAppContact.AI)

    def test_invalid_modes_and_bodies_are_rejected(self):
        url = reverse('change-mode', args=[self.contact.pk])
        for payload in ({'mode': 'robot'}, {'mode': None}, {'mode': ['human']}, {}, []):
            with self.subTest(payload=payload):
                self.assertEqual(post_json(self.client, url, payload).status_code, 400)
        self.assertEqual(self.client.post(url, data='no json', content_type='application/json').status_code, 400)
        self.contact.refresh_from_db()
        self.assertEqual(self.contact.mode, WhatsAppContact.AI)

    def test_manual_message_is_queued_and_not_sent_by_the_request(self):
        before = timezone.now()
        url = reverse('send-manual-message', args=[self.contact.pk])
        response = post_json(self.client, url, {'content': '  Hola, te ayudo yo  '})
        self.assertEqual(response.status_code, 200)
        message = WhatsAppMessage.objects.get(pk=response.json()['message_id'])
        self.assertEqual((message.text, message.is_manual, message.is_from_customer), ('Hola, te ayudo yo', True, False))
        item = Outbox.objects.get()
        self.assertEqual((item.content, item.sent, item.message_id), ('Hola, te ayudo yo', False, message.pk))
        self.meta.assert_not_called()
        self.contact.refresh_from_db()
        self.assertGreaterEqual(self.contact.last_message_at, before)

    def test_invalid_manual_messages_are_rejected(self):
        url = reverse('send-manual-message', args=[self.contact.pk])
        for payload in ({'content': ''}, {'content': '   '}, {'content': 5}, {'content': None}, {}, {'content': 'x' * 4001}):
            with self.subTest(payload=payload):
                self.assertEqual(post_json(self.client, url, payload).status_code, 400)
        self.assertFalse(Outbox.objects.exists())
        missing = reverse('send-manual-message', args=[999999])
        self.assertEqual(post_json(self.client, missing, {'content': 'x'}).status_code, 404)
