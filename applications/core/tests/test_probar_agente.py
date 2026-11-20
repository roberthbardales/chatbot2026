from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import SimpleTestCase

from ..agent import AgentRejected


class ProbarAgenteCommandTests(SimpleTestCase):
    def run_chat(self, inputs, answers):
        out = StringIO()
        with patch('builtins.input', side_effect=inputs), \
                patch('applications.core.management.commands.probar_agente.answer_with_agent', side_effect=answers) as agent:
            call_command('probar_agente', stdout=out)
        return out.getvalue(), agent

    def test_conversation_keeps_the_history_and_can_be_reset(self):
        output, agent = self.run_chat(['hola', 'y el precio?', 'nuevo', 'otra', 'salir'], ['Hola', 'Cuesta', 'Otra respuesta'])
        self.assertIn('Bot: Hola', output)
        self.assertIn('(conversación nueva)', output)
        self.assertEqual(agent.call_args_list[1].args[2], [{'role': 'user', 'content': 'hola'},
                                                          {'role': 'assistant', 'content': 'Hola'}])
        self.assertEqual(agent.call_args_list[2].args[2], [])

    def test_failures_show_the_rejected_text_and_do_not_stop_the_chat(self):
        output, agent = self.run_chat(['precio?', 'hola', ''], [AgentRejected('hand_written_figure', 'Cuesta 10.00'), 'Hola'])
        self.assertIn('[el agente falló: hand_written_figure]', output)
        self.assertIn('Texto rechazado: Cuesta 10.00', output)
        self.assertIn('Bot: Hola', output)
        self.assertEqual(agent.call_args_list[1].args[2], [])

    def test_empty_input_ends_without_calling_the_agent(self):
        _output, agent = self.run_chat([''], [])
        agent.assert_not_called()
