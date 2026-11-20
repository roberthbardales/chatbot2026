import io
import json
from urllib.error import HTTPError
from unittest.mock import patch

from django.test import TestCase, override_settings

from ..limits import LimitExceeded
from ..models import AIUsageDay
from ..providers import OPENROUTER, ProviderError, chat_with_tools

TOOLS = [{'type': 'function', 'function': {
    'name': 'buscar_productos', 'description': 'Busca productos',
    'parameters': {'type': 'object', 'properties': {'consulta': {'type': 'string'}}, 'required': ['consulta']},
}}]
MESSAGES = [{'role': 'user', 'content': 'tienes protector solar?'}]


def _reply(message):
    return json.dumps({'choices': [{'message': message}]}).encode('utf-8')


@override_settings(OPENROUTER_API_KEY='clave', AI_AGENT_TEMPERATURE=0.3, AI_MAX_OUTPUT_TOKENS=300)
class ChatWithToolsTests(TestCase):
    def setUp(self):
        self.urlopen = patch('applications.core.providers.urlopen').start()
        self.addCleanup(patch.stopall)

    def answer(self, message):
        self.urlopen.return_value.__enter__.return_value.read.return_value = _reply(message)

    def call(self, **kwargs):
        return chat_with_tools(OPENROUTER, kwargs.get('messages', MESSAGES), kwargs.get('tools', TOOLS), 'Eres el asistente.')

    def test_text_reply(self):
        self.answer({'role': 'assistant', 'content': ' Hola, claro '})
        result = self.call()
        self.assertEqual(result['content'], 'Hola, claro')
        self.assertEqual(result['tool_calls'], [])
        self.assertEqual(result['message'], {'role': 'assistant', 'content': 'Hola, claro'})

    def test_tool_call_reply(self):
        calls = [{'id': 'c1', 'type': 'function',
                  'function': {'name': 'buscar_productos', 'arguments': '{"consulta": "protector solar"}'}}]
        self.answer({'role': 'assistant', 'content': None, 'tool_calls': calls})
        result = self.call()
        self.assertEqual(result['content'], '')
        self.assertEqual(result['tool_calls'], [{'id': 'c1', 'name': 'buscar_productos', 'arguments': {'consulta': 'protector solar'}}])
        self.assertEqual(result['message']['tool_calls'], calls)
        self.assertIsNone(result['message']['content'])

    def test_invalid_arguments_become_none(self):
        for arguments in ('no es json', '[1, 2]', '"texto"'):
            with self.subTest(arguments=arguments):
                self.answer({'role': 'assistant', 'content': None, 'tool_calls': [
                    {'id': 'c1', 'function': {'name': 'buscar_productos', 'arguments': arguments}}]})
                self.assertIsNone(self.call()['tool_calls'][0]['arguments'])

    def test_missing_arguments_are_an_empty_object(self):
        self.answer({'role': 'assistant', 'content': None, 'tool_calls': [
            {'id': 'c1', 'function': {'name': 'buscar_productos'}}]})
        self.assertEqual(self.call()['tool_calls'][0]['arguments'], {})

    def test_malformed_replies_are_rejected(self):
        bad = [
            {'role': 'assistant', 'content': ''},
            {'role': 'assistant', 'content': None},
            {'role': 'assistant', 'content': None, 'tool_calls': 'x'},
            {'role': 'assistant', 'content': None, 'tool_calls': [{'id': '1'}]},
            {'role': 'assistant', 'content': None, 'tool_calls': [{'function': {'name': 5}}]},
        ]
        for message in bad:
            with self.subTest(message=message):
                self.answer(message)
                with self.assertRaises(ProviderError):
                    self.call()

    def test_missing_choices_are_rejected(self):
        self.urlopen.return_value.__enter__.return_value.read.return_value = b'{}'
        with self.assertRaises(ProviderError):
            self.call()

    def test_payload_has_system_tools_temperature_and_caps(self):
        self.answer({'role': 'assistant', 'content': 'ok'})
        self.call()
        payload = json.loads(self.urlopen.call_args.args[0].data)
        self.assertEqual(payload['messages'][0], {'role': 'system', 'content': 'Eres el asistente.'})
        self.assertEqual(payload['messages'][1:], MESSAGES)
        self.assertEqual(payload['tools'], TOOLS)
        self.assertEqual(payload['temperature'], 0.3)
        self.assertEqual(payload['max_tokens'], 300)

    def test_tools_are_omitted_when_empty(self):
        self.answer({'role': 'assistant', 'content': 'ok'})
        self.call(tools=[])
        self.assertNotIn('tools', json.loads(self.urlopen.call_args.args[0].data))

    def test_http_error_becomes_provider_error(self):
        self.urlopen.side_effect = HTTPError('https://x', 500, 'boom', {}, io.BytesIO(b'detalle'))
        with self.assertRaises(ProviderError):
            self.call()

    def test_input_checks(self):
        with self.assertRaises(ProviderError):
            chat_with_tools('otro', MESSAGES, TOOLS, 'sistema')
        with self.assertRaises(ProviderError):
            chat_with_tools(OPENROUTER, [], TOOLS, 'sistema')
        with self.assertRaises(ProviderError):
            chat_with_tools(OPENROUTER, MESSAGES, TOOLS, '  ')
        self.urlopen.assert_not_called()

    @override_settings(OPENROUTER_API_KEY='')
    def test_missing_key(self):
        with self.assertRaises(ProviderError):
            self.call()
        self.urlopen.assert_not_called()

    def test_each_call_counts_against_the_daily_budget(self):
        self.answer({'role': 'assistant', 'content': 'ok'})
        self.call()
        self.call()
        self.assertEqual(AIUsageDay.objects.get().requests, 2)

    @override_settings(AI_DAILY_REQUEST_LIMIT=1)
    def test_daily_budget_blocks_before_calling_the_provider(self):
        self.answer({'role': 'assistant', 'content': 'ok'})
        self.call()
        with self.assertRaises(LimitExceeded):
            self.call()
        self.assertEqual(self.urlopen.call_count, 1)
