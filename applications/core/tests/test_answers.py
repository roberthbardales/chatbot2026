from unittest.mock import patch

from django.test import SimpleTestCase, TestCase

from applications.whatsapp.models import WhatsAppContact, WhatsAppMessage
from ..models import Product
from ..services import answer_sheet_message
from ..providers import ProviderError
from types import SimpleNamespace


class SheetResponseSafetyTests(SimpleTestCase):
    def setUp(self):
        self.conversation = SimpleNamespace(provider='openrouter')
        self.products = [
            {'sku': 'VAL-01', 'name': 'Válvula de bronce', 'price': 25, 'stock': 8, 'unit': 'unidades'},
            {'sku': 'VAL-02', 'name': 'Válvula de acero', 'price': 40, 'stock': 15, 'unit': 'unidades'},
        ]
        self.search = patch('applications.core.services.sheet_search_products', return_value=self.products[:1]).start()
        self.generate = patch('applications.core.services.generate_response').start()
        self.addCleanup(patch.stopall)

    def test_no_matches_does_not_call_model(self):
        self.search.return_value = []
        self.assertIn('No encontré', answer_sheet_message(self.conversation, 'válvula'))
        self.generate.assert_not_called()

    def test_values_are_rendered_from_selected_product(self):
        self.generate.return_value = '{"productos":[{"sku":"VAL-01","campos":["precio","stock"]}]}'
        result = answer_sheet_message(self.conversation, 'válvula')
        self.assertIn('está a 25', result)
        self.assertIn('hay 8 unidades', result)
        self.assertNotIn('40', result)
        self.assertNotIn('VAL-02', result)

    def test_invalid_selections_use_safe_fallback(self):
        for raw in [
            'Precio inventado: 999',
            '{"productos":[{"sku":"VAL-99","campos":["precio"]}]}',
            '{"productos":[{"sku":"VAL-01","campos":["descuento"]}]}',
            '{"productos":[{"sku":"VAL-01","campos":["precio"],"precio":999}]}',
            '{"productos":[]}', 'null',
            '{"productos":[{"sku":[],"campos":["precio"]}]}',
        ]:
            with self.subTest(raw=raw):
                self.generate.return_value = raw
                result = answer_sheet_message(self.conversation, 'válvula')
                self.assertIn('está a 25', result)
                self.assertNotIn('está a 40', result)
                self.assertNotIn('999', result)

    def test_provider_failure_preserves_local_information(self):
        self.generate.side_effect = ProviderError('offline')
        self.assertIn('hay 8 unidades', answer_sheet_message(self.conversation, 'válvula'))

    def test_missing_values_and_zero_stock_are_distinct(self):
        self.products[0].update(price=None, stock=0)
        self.generate.return_value = '{}'
        result = answer_sheet_message(self.conversation, 'válvula')
        self.assertIn('no tengo el precio en el inventario', result)
        self.assertIn('está agotado', result)
        self.assertNotIn('no tengo el stock', result)

    def test_image_is_only_used_for_search_not_final_selection(self):
        self.generate.side_effect = ['válvula bronce', '{"productos":[{"sku":"VAL-01","campos":["precio"]}]}']
        result = answer_sheet_message(self.conversation, 'El usuario envió una imagen.', image_data_url='data:image/jpeg;base64,abc')
        self.assertIn('posibles coincidencias', result)
        self.assertIn('número de opción', result)
        self.generate.assert_called_once()

    def test_unrecognized_image_requests_keywords(self):
        self.generate.return_value = 'desconocido'
        result = answer_sheet_message(self.conversation, 'El usuario envió una imagen.', image_data_url='image')
        self.assertIn('entre 3 y 5 palabras clave', result)
        self.search.assert_not_called()

    @patch('applications.core.services.sheet_product_by_sku', return_value=None)
    def test_unknown_explicit_sku_does_not_suggest_other_products(self, lookup):
        self.assertIn('No encontré', answer_sheet_message(self.conversation, 'SKU UNKNOWN-1'))
        self.generate.assert_not_called()
        self.search.assert_not_called()


class InventoryConversationTests(TestCase):
    def new_conversation(self):
        self.contact = WhatsAppContact.objects.create(phone_number=f'519{WhatsAppContact.objects.count():08d}')
        self.conversation = SimpleNamespace(provider='openrouter', messages=self.contact.messages)

    def setUp(self):
        self.new_conversation()
        self.valve = Product.objects.create(sku='VAL-01', name='Válvula de bronce 1/2 pulgada', price='25.00', stock=8)
        self.other = Product.objects.create(sku='VAL-02', name='Válvula de bronce 3/4 pulgada', price='40.00', stock=15)
        self.generate = patch('applications.core.services.generate_response', return_value='{}').start()
        self.addCleanup(patch.stopall)

    def ask(self, text, **kwargs):
        WhatsAppMessage.objects.create(contact=self.contact, message_id=f'in-{WhatsAppMessage.objects.count()}', text=text)
        result = answer_sheet_message(self.conversation, text, **kwargs)
        WhatsAppMessage.objects.create(contact=self.contact, message_id=f'out-{WhatsAppMessage.objects.count()}',
                                       text=result, is_from_customer=False,
                                       inventory_context=getattr(result, 'inventory_context', {}))
        return result

    def test_follow_up_remembers_sku_but_refreshes_stock_and_price(self):
        self.ask('SKU VAL-01')
        Product.objects.filter(pk=self.valve.pk).update(price='27.00', stock=3)
        answer = self.ask('¿Y cuántas unidades hay?')
        self.assertIn('hay 3', answer)
        self.assertIn('está a 27', answer)
        self.assertNotIn('VAL-02', answer)

    def test_ambiguity_requires_customer_confirmation_without_ai_guessing(self):
        answer = self.ask('Busco una válvula de bronce')
        self.assertIn('varios productos', answer)
        self.assertIn('VAL-01', answer)
        self.assertIn('VAL-02', answer)
        self.assertNotIn('está a', answer)
        self.generate.assert_not_called()
        follow_up = self.ask('¿Cuántas unidades hay?')
        self.assertIn('número de opción', follow_up)
        self.assertNotIn('hay 8', follow_up)
        self.assertNotIn('hay 15', follow_up)

    def test_customer_can_choose_option_and_then_follow_up(self):
        self.ask('válvula bronce')
        selected = self.ask('la segunda')
        self.assertIn('VAL-02', selected)
        self.assertNotIn('VAL-01', selected)
        self.assertIn('hay 15', self.ask('¿Y el stock?'))

    def test_measure_can_disambiguate_previous_options(self):
        self.ask('válvula bronce')
        selected = self.ask('la de 1/2 pulgada')
        self.assertIn('VAL-01', selected)
        self.assertNotIn('VAL-02', selected)

    def test_refinement_does_not_switch_to_unrelated_product(self):
        Product.objects.create(sku='TUB-01', name='Tubo de 30 ml', specs='30 ml')
        self.ask('válvula bronce')
        result = self.ask('la de 30 ml')
        self.assertIn('No encontré', result)
        self.assertNotIn('TUB-01', result)

    def test_new_product_replaces_previous_context(self):
        Product.objects.create(sku='COD-01', name='Codo de acero', stock=2)
        self.ask('SKU VAL-01')
        self.ask('Busco un codo de acero')
        result = self.ask('¿Cuántas unidades hay?')
        self.assertIn('COD-01', result)
        self.assertIn('hay 2', result)
        self.assertNotIn('VAL-01', result)

    def test_unknown_reference_clears_previous_selection(self):
        self.ask('SKU VAL-01')
        self.assertIn('No encontré', self.ask('SKU UNKNOWN-1'))
        self.assertIn('No encontré', self.ask('¿Y su precio?'))

    def test_removed_or_inactive_selection_is_not_reused(self):
        self.ask('SKU VAL-01')
        Product.objects.filter(pk=self.valve.pk).update(active=False)
        self.assertIn('No encontré', self.ask('¿Y el stock?'))

    def test_other_contact_context_is_never_used(self):
        self.ask('SKU VAL-01')
        self.new_conversation()
        result = self.ask('¿Cuántas unidades hay?')
        self.assertIn('No encontré', result)
        self.assertNotIn('VAL-01', result)

    def test_unknown_new_model_does_not_reuse_old_product(self):
        self.ask('SKU VAL-01')
        self.assertIn('No encontré', self.ask('¿Precio de la válvula ZX999?'))

    def test_image_match_requires_confirmation_even_for_single_product(self):
        self.generate.return_value = 'válvula bronce 1/2 pulgada'
        result = self.ask('El usuario envió una imagen.', image_data_url='image')
        self.assertIn('posibles coincidencias', result)
        self.assertNotIn('está a', result)
        self.generate.return_value = '{}'
        confirmed = self.ask('sí')
        self.assertIn('VAL-01', confirmed)
        self.assertIn('está a 25', confirmed)
