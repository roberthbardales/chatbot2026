import json
from types import SimpleNamespace
from unittest.mock import patch

from django.test import TestCase

from ..inventory import search_products
from ..models import Product
from ..providers import ProviderError
from ..services import NO_DESCRIPTION_DATA, answer_sheet_message


def _selection(sku, fields):
    return json.dumps({'productos': [{'sku': sku, 'campos': fields}]})


class DescriptionSearchTests(TestCase):
    def setUp(self):
        Product.objects.create(
            sku='ANU-1', name='Anua Heartleaf Protector Solar', price='80.00', stock=4,
            description='Protector ligero para piel sensible y grasa, no deja cast blanco.')
        Product.objects.create(sku='ANU-2', name='Anua Heartleaf Toner', price='70.00', stock=3,
                               description='Calma la piel irritada con extracto de heartleaf.')

    def test_name_search_wins_and_ignores_the_description(self):
        self.assertEqual([p['sku'] for p in search_products('toner', None)], ['ANU-2'])

    def test_description_is_used_when_the_name_finds_nothing(self):
        self.assertEqual([p['sku'] for p in search_products('piel sensible', None)], ['ANU-1'])

    def test_terms_can_be_split_between_name_and_description(self):
        self.assertEqual([p['sku'] for p in search_products('anua sensible', None)], ['ANU-1'])

    def test_unknown_words_still_find_nothing(self):
        self.assertEqual(search_products('piel seca', None), [])

    def test_question_words_do_not_block_the_search(self):
        self.assertEqual([p['sku'] for p in search_products('para qué sirve el anua heartleaf toner', None)], ['ANU-2'])


class DescriptionAnswerTests(TestCase):
    def setUp(self):
        self.product = Product.objects.create(
            sku='ANU-1', name='Anua Heartleaf Protector Solar', price='80.00', stock=4,
            description='Protector ligero para piel sensible y grasa, no deja cast blanco.')
        self.conversation = SimpleNamespace(provider='openrouter', messages=None)
        self.generate = patch('applications.core.services.generate_response').start()
        self.addCleanup(patch.stopall)

    def ask(self, question='para qué sirve el Anua Heartleaf Protector Solar'):
        return answer_sheet_message(self.conversation, question)

    def test_ai_answers_from_the_description(self):
        self.generate.side_effect = [_selection('ANU-1', ['descripcion']), 'Es un protector ligero para piel sensible y grasa.']
        answer = self.ask()
        self.assertIn('Anua Heartleaf Protector Solar', answer)
        self.assertIn('Es un protector ligero para piel sensible y grasa.', answer)
        self.assertEqual(self.generate.call_count, 2)
        description_prompt = self.generate.call_args_list[1].args[1]
        self.assertIn('Protector ligero para piel sensible', description_prompt)
        self.assertNotIn('80', description_prompt)

    def test_selection_prompt_does_not_carry_the_full_description(self):
        self.generate.side_effect = [_selection('ANU-1', ['precio']), ]
        self.ask('precio del Anua Heartleaf Protector Solar')
        prompt = self.generate.call_args_list[0].args[1]
        self.assertNotIn('no deja cast blanco', prompt)
        self.assertIn('tiene_descripcion', prompt)

    def test_missing_information_is_stated_plainly(self):
        self.generate.side_effect = [_selection('ANU-1', ['descripcion']), 'NO_DATO']
        self.assertIn(NO_DESCRIPTION_DATA, self.ask('para qué sirve el Anua Heartleaf Protector Solar'))

    def test_follow_up_question_uses_the_selected_product(self):
        history = SimpleNamespace(
            filter=lambda **kwargs: SimpleNamespace(order_by=lambda *a: SimpleNamespace(
                first=lambda: SimpleNamespace(inventory_context={'selected_sku': 'ANU-1'}))))
        conversation = SimpleNamespace(provider='openrouter', messages=history)
        self.generate.side_effect = [_selection('ANU-1', ['descripcion']), 'Sí, es apto para piel grasa.']
        answer = answer_sheet_message(conversation, 'es para piel grasa?')
        self.assertIn('Anua Heartleaf Protector Solar', answer)
        self.assertIn('piel grasa', answer)

    def test_follow_up_with_unknown_words_is_not_forced_onto_the_selected_product(self):
        history = SimpleNamespace(
            filter=lambda **kwargs: SimpleNamespace(order_by=lambda *a: SimpleNamespace(
                first=lambda: SimpleNamespace(inventory_context={'selected_sku': 'ANU-1'}))))
        conversation = SimpleNamespace(provider='openrouter', messages=history)
        self.assertIn('No encontré', answer_sheet_message(conversation, 'base mate'))
        self.generate.assert_not_called()

    def test_price_and_description_are_combined(self):
        self.generate.side_effect = [_selection('ANU-1', ['precio', 'descripcion']), 'Sirve para piel sensible.']
        answer = self.ask()
        self.assertIn('está a 80.00', answer)
        self.assertIn('Sirve para piel sensible.', answer)

    def test_literal_description_if_the_ai_fails_on_the_second_call(self):
        self.generate.side_effect = [_selection('ANU-1', ['descripcion']), ProviderError('boom')]
        self.assertIn('su descripción: Protector ligero para piel sensible', self.ask())

    def test_product_without_description_does_not_call_the_ai_twice(self):
        Product.objects.filter(pk=self.product.pk).update(description='')
        self.generate.side_effect = [_selection('ANU-1', ['descripcion'])]
        answer = self.ask()
        self.assertIn('no tengo la descripción en el inventario', answer)
        self.assertEqual(self.generate.call_count, 1)
