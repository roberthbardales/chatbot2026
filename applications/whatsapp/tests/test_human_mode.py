"""Modo humano: el bot calla cuando un operador atiende al contacto."""
from unittest.mock import patch

from django.test import TestCase, override_settings

from applications.core.models import UsageBucket

from ..models import Outbox, WhatsAppContact, WhatsAppMessage
from ..services import _finish_processing, enqueue_message, process_outbox, process_pending_messages


@override_settings(MESSAGE_DEBOUNCE_SECONDS=0)
class HumanModeTests(TestCase):
    def setUp(self):
        self.contact = WhatsAppContact.objects.create(phone_number='51933330000', mode=WhatsAppContact.HUMAN)
        self.answer = patch('applications.whatsapp.services.answer_sheet_message', return_value='Respuesta IA').start()
        self.download = patch('applications.whatsapp.services.download_media_data').start()
        self.send = patch('applications.whatsapp.services.send_text_message').start()
        self.addCleanup(patch.stopall)

    def incoming(self, message_id='h-1', text='Consulta', media_id=''):
        enqueue_message(self.contact.phone_number, '', message_id, text, media_id, 'image/jpeg' if media_id else '')
        return WhatsAppMessage.objects.get(message_id=message_id)

    def set_mode(self, mode):
        WhatsAppContact.objects.filter(pk=self.contact.pk).update(mode=mode)

    def assert_silent(self, message):
        message.refresh_from_db()
        self.assertEqual(message.status, WhatsAppMessage.PROCESSED)
        self.assertEqual(message.response, '')
        self.assertIsNone(message.processing_token)
        self.assertFalse(Outbox.objects.exists())
        self.assertFalse(WhatsAppMessage.objects.filter(is_from_customer=False).exists())
        self.send.assert_not_called()

    def test_text_is_recorded_without_calling_the_ai(self):
        message = self.incoming()
        self.assertEqual(process_pending_messages(force=True), 1)
        self.assert_silent(message)
        self.answer.assert_not_called()

    def test_human_mode_does_not_consume_the_bot_rate(self):
        self.incoming()
        process_pending_messages(force=True)
        self.assertFalse(UsageBucket.objects.exists())

    def test_image_is_not_downloaded(self):
        message = self.incoming(media_id='media-1')
        process_pending_messages(force=True)
        self.assert_silent(message)
        self.download.assert_not_called()
        self.assertEqual(message.media_id, 'media-1')

    def test_switching_back_to_the_bot_answers_new_messages(self):
        self.incoming('h-1')
        process_pending_messages(force=True)
        self.set_mode(WhatsAppContact.AI)
        self.incoming('h-2')
        process_pending_messages(force=True)
        self.answer.assert_called_once()
        self.assertEqual(Outbox.objects.get().content, 'Respuesta IA')

    def test_switching_to_human_during_the_ai_call_discards_the_response(self):
        self.set_mode(WhatsAppContact.AI)
        message = self.incoming()

        def answer(*args, **kwargs):
            self.set_mode(WhatsAppContact.HUMAN)
            return 'Respuesta que no debe salir'
        self.answer.side_effect = answer
        process_pending_messages(force=True)
        self.assert_silent(message)

    def test_switching_to_human_during_the_download_skips_the_ai(self):
        self.set_mode(WhatsAppContact.AI)
        message = self.incoming(media_id='media-2')

        def download(media_id):
            self.set_mode(WhatsAppContact.HUMAN)
            return 'data:image/jpeg;base64,abc'
        self.download.side_effect = download
        process_pending_messages(force=True)
        self.assert_silent(message)
        self.answer.assert_not_called()

    def test_error_replies_are_also_silenced_in_human_mode(self):
        self.set_mode(WhatsAppContact.AI)
        message = self.incoming()

        def failing(*args, **kwargs):
            self.set_mode(WhatsAppContact.HUMAN)
            raise RuntimeError('fallo inesperado')
        self.answer.side_effect = failing
        process_pending_messages(force=True)
        self.assert_silent(message)

    def test_failed_batch_does_not_send_the_apology_in_human_mode(self):
        message = self.incoming()
        token = '11111111-1111-1111-1111-111111111111'
        WhatsAppMessage.objects.filter(pk=message.pk).update(status=WhatsAppMessage.PROCESSING, processing_token=token)
        message.refresh_from_db()
        self.assertTrue(_finish_processing([message], message.processing_token, 'Disculpa', failed=True))
        self.assert_silent(message)

    def test_replies_already_queued_are_still_sent_after_switching_to_human(self):
        self.set_mode(WhatsAppContact.AI)
        self.incoming()
        process_pending_messages(force=True)
        self.set_mode(WhatsAppContact.HUMAN)
        self.send.return_value = {'messages': [{'id': 'wamid.1'}]}
        self.assertEqual(process_outbox(), 1)
        self.send.assert_called_once()


# Estas pruebas simulan el motor de plantillas: no deben depender de AI_AGENT_ENABLED del .env
# (con el agente encendido llamarían a OpenRouter de verdad).
from django.test import override_settings as _override_settings  # noqa: E402

_AGENT_OFF = _override_settings(AI_AGENT_ENABLED=False)


def setUpModule():
    _AGENT_OFF.enable()


def tearDownModule():
    _AGENT_OFF.disable()
