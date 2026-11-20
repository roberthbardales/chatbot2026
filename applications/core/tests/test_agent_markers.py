import json
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase, override_settings

from ..agent import AgentError, AgentRejected, answer_with_agent, render_markers, run_tool
from ..models import Product


def _text(content):
    return {'content': content, 'tool_calls': [], 'message': {'role': 'assistant', 'content': content}}


def _call(name, arguments, call_id='c1'):
    return {'content': '', 'tool_calls': [{'id': call_id, 'name': name, 'arguments': arguments}],
            'message': {'role': 'assistant', 'content': None, 'tool_calls': []}}


def _product(sku='A-100', **values):
    data = {'sku': sku, 'name': 'Anua Toner', 'brand': 'Anua', 'category': 'Tónico y bruma',
            'price': '70.00', 'stock': 5, 'unit': 'unidades', 'available': True}
    data.update(values)
    from decimal import Decimal
    data['price'] = Decimal(str(data['price'])) if data['price'] is not None else None
    return data


class RenderMarkersTests(SimpleTestCase):
    def setUp(self):
        self.seen = {'A-100': _product(), 'B-200': _product('B-200', name='Otro', price='99.5', stock=1, brand='Skin1004'),
                     'C-300': _product('C-300', stock=0, price=None, brand='', category='')}

    def render(self, text, question=''):
        return render_markers(text, self.seen, question)

    def test_markers_are_replaced_with_real_values(self):
        self.assertEqual(self.render('Cuesta {precio:A-100} y {stock:A-100}.'), 'Cuesta S/ 70.00 y hay 5 unidades.')
        self.assertEqual(self.render('{marca:A-100} · {categoria:A-100}'), 'Anua · Tónico y bruma')

    def test_price_always_has_two_decimals(self):
        self.assertEqual(self.render('{precio:B-200}'), 'S/ 99.50')

    def test_stock_phrases(self):
        self.assertEqual(self.render('{stock:B-200}'), 'hay 1 unidad')
        self.assertEqual(self.render('{stock:C-300}'), 'está agotado')
        self.seen['D-1'] = _product('D-1', stock=None, available=True)
        self.assertEqual(self.render('{stock:D-1}'), 'está disponible; consulta la cantidad exacta')
        self.seen['D-2'] = _product('D-2', stock=None, available=None)
        self.assertEqual(self.render('{stock:D-2}'), 'no tengo el stock')

    def test_missing_values_have_a_neutral_phrase(self):
        self.assertEqual(self.render('{precio:C-300}'), 'precio no disponible')
        self.assertEqual(self.render('{marca:C-300}'), 'sin marca registrada')
        self.assertEqual(self.render('{categoria:C-300}'), 'sin categoría registrada')

    def test_double_asterisks_become_single(self):
        self.assertEqual(self.render('**Anua Toner** hola'), '*Anua Toner* hola')

    def test_unknown_sku_is_rejected(self):
        for text in ('{precio:ZZZ}', 'Mira {stock:a-100}'):
            with self.subTest(text=text), self.assertRaises(AgentRejected) as caught:
                self.render(text)
            self.assertEqual(caught.exception.reason, 'unknown_sku')

    def test_unknown_marker_kind_or_stray_braces_are_rejected(self):
        for text in ('{descuento:A-100}', 'Cuesta {precio:A-100', 'precio {}'):
            with self.subTest(text=text), self.assertRaises(AgentRejected) as caught:
                self.render(text)
            self.assertEqual(caught.exception.reason, 'stray_braces')

    def test_hand_written_figures_are_rejected(self):
        for text in ('Cuesta 70.00', 'Cuesta 70,50 hoy', 'Vale S/ 70', 'Vale S/. 70.5', 'Cuesta $ 20', 'Son 70 soles',
                     'Hay 5 unidades', 'Quedan 3', 'tenemos 12 en stock', '7 uds', 'Cuesta USD 20'):
            with self.subTest(text=text), self.assertRaises(AgentRejected) as caught:
                self.render(text)
            self.assertEqual(caught.exception.reason, 'hand_written_figure')

    def test_figures_that_are_not_commercial_are_allowed(self):
        for text in ('Es de 50ml', 'FPS50+ y SPF 50', 'Contiene 0.25% de retinol', 'Hay 7 productos en total',
                     'Los 5 más vendidos', 'Tamaño 1.69 oz', 'Dura 12 horas', 'Uso 2 veces al día'):
            with self.subTest(text=text):
                self.assertEqual(self.render(text), text)

    def test_numbers_the_customer_wrote_may_be_repeated(self):
        self.assertEqual(self.render('Busco algo de menos de S/ 80:', 'algo de menos de 80 soles'), 'Busco algo de menos de S/ 80:')
        with self.assertRaises(AgentRejected):
            self.render('Cuesta 70.00', 'algo de menos de 80 soles')

    def test_visible_identifiers_are_rejected(self):
        for text in ('El código es A-100', 'sku 8779935645868'):
            with self.subTest(text=text), self.assertRaises(AgentRejected):
                self.render(text)

    def test_empty_or_too_long_text_is_rejected(self):
        for text in ('', '   ', 'x' * 1300):
            with self.subTest(length=len(text)), self.assertRaises(AgentRejected):
                self.render(text)

    def test_figures_already_shown_in_the_conversation_may_be_repeated(self):
        earlier = 'Cuesta S/ 47.92 y hay 13 unidades.'
        self.assertEqual(render_markers('Como te dije, hay 13 unidades a S/ 47.92', self.seen, '', earlier),
                         'Como te dije, hay 13 unidades a S/ 47.92')
        with self.assertRaises(AgentRejected):
            render_markers('Hay 14 unidades', self.seen, '', earlier)

    def test_rejection_keeps_the_text_for_local_debugging(self):
        with self.assertRaises(AgentRejected) as caught:
            self.render('Cuesta {precio:ZZZ}')
        self.assertEqual(caught.exception.text, 'Cuesta {precio:ZZZ}')

    def test_a_product_can_be_mentioned_twice(self):
        self.assertEqual(self.render('{precio:A-100} o {precio:A-100}'), 'S/ 70.00 o S/ 70.00')


class PriceToolTests(TestCase):
    def setUp(self):
        for sku, name, price in (('P-1', 'Crema cara', '120.00'), ('P-2', 'Crema barata', '30.00'),
                                 ('P-3', 'Crema media', '75.50'), ('P-4', 'Crema sin precio', None)):
            Product.objects.create(sku=sku, name=name, category='Cremas', price=price, stock=3)

    def names(self, name, arguments):
        return [p['nombre'] for p in run_tool(name, arguments, {})['productos']]

    def test_cheapest_first(self):
        self.assertEqual(self.names('buscar_productos', {'consulta': 'crema', 'ordenar': 'precio_asc'}),
                         ['Crema barata', 'Crema media', 'Crema cara', 'Crema sin precio'])

    def test_most_expensive_first(self):
        self.assertEqual(self.names('buscar_productos', {'consulta': 'crema', 'ordenar': 'precio_desc'})[:3],
                         ['Crema cara', 'Crema media', 'Crema barata'])

    def test_price_cap_excludes_unpriced_products(self):
        result = run_tool('buscar_productos', {'consulta': 'crema', 'precio_maximo': 80}, {})
        self.assertEqual(sorted(p['nombre'] for p in result['productos']), ['Crema barata', 'Crema media'])
        self.assertEqual(result['total'], 2)

    def test_cap_and_order_together_and_on_categories(self):
        self.assertEqual(self.names('listar_categoria', {'categoria': 'cremas', 'precio_maximo': '100,5', 'ordenar': 'precio_desc'}),
                         ['Crema media', 'Crema barata'])

    def test_invalid_options_return_an_error(self):
        for arguments in ({'consulta': 'crema', 'precio_maximo': 'barato'}, {'consulta': 'crema', 'precio_maximo': True},
                          {'consulta': 'crema', 'ordenar': 'alfabetico'}):
            with self.subTest(arguments=arguments):
                self.assertIn('error', run_tool('buscar_productos', arguments, {}))

    def test_summaries_flag_price_and_stock_state_without_values(self):
        Product.objects.filter(sku='P-2').update(stock=0)
        by_name = {p['nombre']: p for p in run_tool('buscar_productos', {'consulta': 'crema'}, {})['productos']}
        self.assertEqual((by_name['Crema barata']['tiene_precio'], by_name['Crema barata']['estado_stock']), (True, 'agotado'))
        self.assertEqual((by_name['Crema sin precio']['tiene_precio'], by_name['Crema sin precio']['estado_stock']), (False, 'disponible'))
        self.assertNotIn('30.00', json.dumps(by_name))


@override_settings(AI_AGENT_MAX_TOOL_CALLS=4, AI_HISTORY_MESSAGES=10, MAX_PRODUCTS_IN_CONTEXT=5)
class MarkerFlowTests(TestCase):
    def setUp(self):
        self.cheap = Product.objects.create(sku='P-2', name='Crema barata', category='Cremas', price='30.00', stock=3, unit='unidades')
        self.dear = Product.objects.create(sku='P-1', name='Crema cara', category='Cremas', price='120.00', stock=0)
        self.chat = patch('applications.core.agent.chat_with_tools').start()
        self.addCleanup(patch.stopall)
        self.conversation = SimpleNamespace(provider='openrouter')

    def test_the_cheapest_product_gets_real_values(self):
        self.chat.side_effect = [_call('buscar_productos', {'consulta': 'crema', 'ordenar': 'precio_asc'}),
                                 _text('La más barata es *Crema barata*: {precio:P-2} y {stock:P-2}.')]
        answer = answer_with_agent(self.conversation, 'cuál es la crema más barata?')
        self.assertEqual(str(answer), 'La más barata es *Crema barata*: S/ 30.00 y hay 3 unidades.')

    def test_a_sku_not_returned_in_this_message_is_rejected(self):
        self.chat.side_effect = [_text('Cuesta {precio:P-2}')]
        with self.assertRaises(AgentRejected):
            answer_with_agent(self.conversation, 'cuánto cuesta la crema barata?')

    def test_a_made_up_price_is_rejected(self):
        self.chat.side_effect = [_call('ver_producto', {'sku': 'P-2'}), _text('La *Crema barata* cuesta S/ 25.00')]
        with self.assertRaises(AgentRejected):
            answer_with_agent(self.conversation, 'cuánto cuesta la crema barata?')

    def test_the_price_marker_works_after_looking_up_by_name(self):
        self.chat.side_effect = [_call('ver_producto', {'nombre': 'Crema cara'}), _text('Está en {precio:P-1} y {stock:P-1}.')]
        self.assertEqual(str(answer_with_agent(self.conversation, 'y la cara?')), 'Está en S/ 120.00 y está agotado.')

    def test_figures_from_the_history_are_not_rejected(self):
        history = [{'role': 'user', 'content': 'la más barata?'},
                   {'role': 'assistant', 'content': 'La *Crema barata* cuesta S/ 30.00 y hay 3 unidades.'}]
        self.chat.side_effect = [_text('Sí, son esas 3 unidades a S/ 30.00.')]
        self.assertEqual(str(answer_with_agent(self.conversation, 'cuántas había?', history)), 'Sí, son esas 3 unidades a S/ 30.00.')

    def test_rejection_is_an_agent_error_so_the_caller_can_fall_back(self):
        self.chat.side_effect = [_text('Cuesta 10.00')]
        with self.assertRaises(AgentError):
            answer_with_agent(self.conversation, 'precio?')
