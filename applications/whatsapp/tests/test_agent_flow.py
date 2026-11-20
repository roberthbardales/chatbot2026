"""Conexión del agente con el worker: historial, respaldo a plantillas, imágenes y cupo."""
import uuid
from unittest.mock import patch

from django.test import TestCase, override_settings

from applications.core.agent import AgentError, AgentRejected
from applications.core.limits import LimitExceeded
from applications.core.responses import InventoryAnswer

from ..models import Outbox, WhatsAppContact, WhatsAppMessage
from ..services import PROCESSING_ERROR, enqueue_message, process_pending_messages

IMAGE = 'El usuario envió una imagen.'


@override_settings(AI_AGENT_ENABLED=True, AI_HISTORY_MESSAGES=10, MESSAGE_DEBOUNCE_SECONDS=0)
class AgentFlowTests(TestCase):
    def setUp(self):
        self.contact = WhatsAppContact.objects.create(phone_number='51944440000')
        self.agent = patch('applications.whatsapp.services.answer_with_agent',
                           return_value=InventoryAnswer('Respuesta del agente', {'selected_sku': 'A-1'})).start()
        self.engine = patch('applications.whatsapp.services.answer_sheet_message', return_value='Respuesta de plantillas').start()
        self.keywords = patch('applications.whatsapp.services.image_keywords', return_value='sérum anua').start()
        self.download = patch('applications.whatsapp.services.download_media_data', return_value='data:image/jpeg;base64,AAAA').start()
        self.send = patch('applications.whatsapp.services.send_text_message').start()
        self.addCleanup(patch.stopall)

    def incoming(self, message_id, text='Consulta', media_id=''):
        enqueue_message(self.contact.phone_number, '', message_id, text, media_id, 'image/jpeg' if media_id else '')
        return WhatsAppMessage.objects.get(message_id=message_id)

    def past(self, text, customer=True, manual=False, status=WhatsAppMessage.PROCESSED, media_id=''):
        return WhatsAppMessage.objects.create(
            contact=self.contact, message_id=f'past-{uuid.uuid4()}', text=text, is_from_customer=customer,
            is_manual=manual, status=status, media_id=media_id)

    def reply(self):
        return Outbox.objects.get().content

    def test_the_agent_answers_and_its_context_is_saved(self):
        self.incoming('m1', 'tienes protector solar?')
        self.assertEqual(process_pending_messages(force=True), 1)
        self.assertEqual(self.reply(), 'Respuesta del agente')
        self.engine.assert_not_called()
        assistant = WhatsAppMessage.objects.get(is_from_customer=False)
        self.assertEqual(assistant.inventory_context, {'selected_sku': 'A-1'})
        args = self.agent.call_args.args
        self.assertEqual(args[1], 'tienes protector solar?')
        self.assertEqual(args[3], None)
        self.assertEqual(args[0].provider, 'openrouter')

    @override_settings(AI_AGENT_ENABLED=False)
    def test_with_the_agent_off_the_template_engine_answers(self):
        self.incoming('m1')
        process_pending_messages(force=True)
        self.assertEqual(self.reply(), 'Respuesta de plantillas')
        self.agent.assert_not_called()
        self.keywords.assert_not_called()

    def test_an_agent_failure_falls_back_to_the_template_engine(self):
        for error in (AgentError('caído'), AgentRejected('hand_written_figure'), RuntimeError('bug inesperado')):
            with self.subTest(error=type(error).__name__):
                Outbox.objects.all().delete()
                self.agent.side_effect = error
                self.incoming(f'm-{type(error).__name__}')
                process_pending_messages(force=True)
                self.assertEqual(self.reply(), 'Respuesta de plantillas')

    def test_a_spent_budget_defers_the_message_instead_of_falling_back(self):
        message = self.incoming('m1')
        self.agent.side_effect = LimitExceeded('Cupo diario agotado', 3600, 'daily_budget')
        self.assertEqual(process_pending_messages(force=True), 0)
        message.refresh_from_db()
        self.assertEqual(message.status, WhatsAppMessage.PENDING)
        self.assertIsNotNone(message.retry_after_at)
        self.engine.assert_not_called()
        self.assertFalse(Outbox.objects.exists())

    def test_history_has_the_previous_messages_in_order_and_not_the_current_one(self):
        self.past('hola')
        self.past('¡Hola! ¿Qué buscas?', customer=False)
        self.past('Tenemos 3 horarios', customer=False, manual=True)
        self.past('foto', media_id='mid-1', customer=True)
        self.past('', media_id='mid-2', customer=True)
        self.past('esperando', status=WhatsAppMessage.PENDING)
        self.past(PROCESSING_ERROR, customer=False)
        self.incoming('m1', 'tienes protector solar?')
        process_pending_messages(force=True)
        self.assertEqual(self.agent.call_args.args[2], [
            {'role': 'user', 'content': 'hola'},
            {'role': 'assistant', 'content': '¡Hola! ¿Qué buscas?'},
            {'role': 'assistant', 'content': 'Tenemos 3 horarios'},
            {'role': 'user', 'content': 'foto'},
            {'role': 'user', 'content': '[El cliente envió una imagen]'},
        ])

    @override_settings(AI_HISTORY_MESSAGES=3)
    def test_history_keeps_only_the_latest_messages(self):
        for index in range(6):
            self.past(f'mensaje {index}')
        self.incoming('m1')
        process_pending_messages(force=True)
        self.assertEqual([m['content'] for m in self.agent.call_args.args[2]], ['mensaje 3', 'mensaje 4', 'mensaje 5'])

    @override_settings(AI_HISTORY_MESSAGES=0)
    def test_history_can_be_disabled(self):
        self.past('antes')
        self.incoming('m1')
        process_pending_messages(force=True)
        self.assertEqual(self.agent.call_args.args[2], [])

    def test_a_batch_of_messages_is_one_question_and_none_of_them_is_history(self):
        self.past('antes')
        self.incoming('m1', 'tienes')
        self.incoming('m2', 'protector solar?')
        process_pending_messages(force=True)
        self.assertEqual(self.agent.call_args.args[1], 'tienes\nprotector solar?')
        self.assertEqual(self.agent.call_args.args[2], [{'role': 'user', 'content': 'antes'}])
        self.assertEqual(self.agent.call_count, 1)

    def test_an_image_without_text_is_sent_as_keywords(self):
        self.incoming('m1', '', media_id='media-1')
        process_pending_messages(force=True)
        args = self.agent.call_args.args
        self.assertEqual((args[1], args[3]), ('', 'sérum anua'))

    def test_an_image_with_text_keeps_both(self):
        self.incoming('m1', 'cuánto cuesta este?', media_id='media-1')
        process_pending_messages(force=True)
        args = self.agent.call_args.args
        self.assertEqual((args[1], args[3]), ('cuánto cuesta este?', 'sérum anua'))

    def test_an_unidentified_image_is_explained_to_the_agent(self):
        self.keywords.return_value = 'desconocido'
        self.incoming('m1', '', media_id='media-1')
        process_pending_messages(force=True)
        args = self.agent.call_args.args
        self.assertIn('no se pudo identificar', args[1])
        self.assertIsNone(args[3])

    def test_human_mode_never_reaches_the_agent(self):
        WhatsAppContact.objects.filter(pk=self.contact.pk).update(mode=WhatsAppContact.HUMAN)
        self.incoming('m1')
        process_pending_messages(force=True)
        self.agent.assert_not_called()
        self.engine.assert_not_called()
        self.assertFalse(Outbox.objects.exists())

    def test_a_greeting_also_goes_through_the_agent(self):
        self.incoming('m1', 'hola')
        process_pending_messages(force=True)
        self.assertEqual(self.agent.call_args.args[1], 'hola')
