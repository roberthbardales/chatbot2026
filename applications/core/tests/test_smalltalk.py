from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

from ..services import _smalltalk_reply, answer_sheet_message


class SmalltalkTests(SimpleTestCase):
    def setUp(self):
        self.conversation = SimpleNamespace(provider='openrouter', messages=None)
        self.generate = patch('applications.core.services.generate_response').start()
        self.search = patch('applications.core.services.sheet_search_products').start()
        self.addCleanup(patch.stopall)

    def test_greetings_thanks_and_farewells_are_recognised(self):
        cases = {
            'Hola': 'asistente', 'hola, buenas tardes!': 'asistente', '¿Qué tal? Buenos días': 'asistente',
            'Gracias': 'De nada', 'muchas gracias!!': 'De nada', 'ok listo': 'De nada', 'Perfecto, gracias': 'De nada',
            'adiós': 'Hasta pronto', 'chao, hasta luego': 'Hasta pronto', 'hola, gracias': 'De nada',
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertIn(expected, _smalltalk_reply(text))

    def test_messages_with_a_product_request_are_not_smalltalk(self):
        for text in ('hola, tienes protector solar?', 'gracias, y el precio del serum', 'sku ABC-1', '', '   ',
                     'la 2', 'sí', 'cuánto cuesta'):
            with self.subTest(text=text):
                self.assertIsNone(_smalltalk_reply(text))

    def test_reply_does_not_search_the_catalog_or_call_the_ai(self):
        answer = answer_sheet_message(self.conversation, 'Hola')
        self.assertIn('asistente', answer)
        self.search.assert_not_called()
        self.generate.assert_not_called()

    def test_reply_keeps_the_previous_context(self):
        context = {'selected_sku': 'VAL-01'}
        with patch('applications.core.services.latest_inventory_context', return_value=context):
            answer = answer_sheet_message(self.conversation, 'gracias')
        self.assertEqual(answer.inventory_context, context)

    def test_an_image_is_never_treated_as_smalltalk(self):
        self.generate.return_value = 'desconocido'
        self.search.return_value = []
        answer = answer_sheet_message(self.conversation, 'hola', image_data_url='data:image/jpeg;base64,abc')
        self.assertIn('No encontré', answer)
