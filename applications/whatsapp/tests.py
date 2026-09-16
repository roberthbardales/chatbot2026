from unittest.mock import patch

from django.test import TestCase, override_settings

from .models import Outbox, WhatsAppContact, WhatsAppMessage
from .services import enqueue_message, process_outbox, process_pending_messages


@override_settings(WHATSAPP_DEFAULT_SOURCE='google_sheets')
class WhatsAppQueueTests(TestCase):
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

    @override_settings(MESSAGE_DEBOUNCE_SECONDS=0)
    @patch('applications.whatsapp.services.answer_sheet_message')
    def test_human_mode_does_not_call_ai(self, answer):
        contact = WhatsAppContact.objects.create(phone_number='51111111111', mode=WhatsAppContact.HUMAN)
        enqueue_message(contact.phone_number, '', 'wamid-2', 'Necesito ayuda')
        self.assertEqual(process_pending_messages(), 1)
        answer.assert_not_called()
        self.assertEqual(WhatsAppMessage.objects.get(message_id='wamid-2').status, 'processed')

    @override_settings(MESSAGE_DEBOUNCE_SECONDS=0, WHATSAPP_DEFAULT_SOURCE='dummyjson')
    @patch('applications.whatsapp.services.answer_message', return_value='Respuesta DummyJSON')
    def test_default_dummyjson_source_uses_chat_service(self, answer):
        enqueue_message('51222222222', 'Cliente', 'wamid-3', 'Busca un producto')
        with patch('applications.whatsapp.services.send_text_message'):
            self.assertEqual(process_pending_messages(), 1)
        answer.assert_called_once()
