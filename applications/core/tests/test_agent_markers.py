import json
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase, override_settings

from ..agent import AgentError, AgentRejected, answer_with_agent, render_markers, requested_figures, run_tool
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


def agent_tools():
    from ..agent import TOOLS
    return TOOLS


class StockClaimTests(SimpleTestCase):
    def setUp(self):
        self.seen = {
            'IN': _product('IN', name='Crema Alfa 50ml', stock=4),
            'OUT': _product('OUT', name='Crema Beta 50ml', stock=0),
            'LONG': _product('LONG', name='Crema Alfa 50ml tono claro', stock=0),
            'NIL': _product('NIL', name='Crema Gama', stock=None, available=None),
        }

    def render(self, text):
        return render_markers(text, self.seen, '')

    def test_true_claims_pass(self):
        for text in ('La *Crema Beta 50ml* está agotada.', 'La *Crema Alfa 50ml* está disponible.',
                     '1. *Crema Beta 50ml* - {precio:OUT} (agotado)', 'La *Crema Beta 50ml* no está disponible.',
                     'La *Crema Beta 50ml* está sin stock.'):
            with self.subTest(text=text):
                self.render(text)

    def test_false_claims_are_rejected(self):
        for text in ('La *Crema Alfa 50ml* está agotada.', 'La *Crema Beta 50ml* está disponible.',
                     'La *Crema Beta 50ml* sí tiene stock: hay stock.', 'La *Crema Gama* está disponible.',
                     'La *Crema Gama* está agotada.', 'La *Crema Alfa 50ml* no está disponible.'):
            with self.subTest(text=text), self.assertRaises(AgentRejected) as caught:
                self.render(text)
            self.assertEqual(caught.exception.reason, 'wrong_stock_state')

    def test_the_claim_is_checked_per_line(self):
        self.render('La *Crema Alfa 50ml* es ligera.\nLa *Crema Beta 50ml* está agotada.')
        with self.assertRaises(AgentRejected):
            self.render('La *Crema Beta 50ml* está agotada.\nLa *Crema Alfa 50ml* está agotada.')

    def test_a_line_naming_several_products_is_not_judged(self):
        self.render('La *Crema Alfa 50ml* está disponible pero la *Crema Beta 50ml* no.')
        self.render('Entre *Crema Alfa 50ml* y *Crema Beta 50ml*, una está agotada.')

    def test_a_name_inside_a_longer_name_does_not_count_twice(self):
        self.render('La *Crema Alfa 50ml tono claro* está agotada.')
        with self.assertRaises(AgentRejected):
            self.render('La *Crema Alfa 50ml tono claro* está disponible.')

    def test_a_marker_for_the_stock_is_always_fine(self):
        self.assertEqual(self.render('La *Crema Beta 50ml*: {stock:OUT}.'), 'La *Crema Beta 50ml*: está agotado.')

    def test_claims_without_a_named_product_are_ignored(self):
        self.render('Está disponible en la tienda.')


class LeaseTests(TestCase):
    def lease_seconds(self, **settings):
        from django.utils import timezone
        from ..limits import principal_slot
        from ..models import UsageLease
        with override_settings(OPENROUTER_TIMEOUT=30, CHAT_REQUEST_LEASE_SECONDS=300, **settings):
            before = timezone.now()
            with principal_slot('contacto-1'):
                lease = UsageLease.objects.get(scope='request:contacto-1')
                return (lease.expires_at - before).total_seconds()

    def test_the_lock_covers_the_agents_worst_case(self):
        self.assertGreater(self.lease_seconds(AI_AGENT_ENABLED=True, AI_AGENT_MAX_TOOL_CALLS=4), 30 * 12 + 55)

    def test_without_the_agent_it_keeps_the_old_duration(self):
        self.assertAlmostEqual(self.lease_seconds(AI_AGENT_ENABLED=False), 300, delta=5)


@override_settings(AI_AGENT_MAX_TOOL_CALLS=4, AI_HISTORY_MESSAGES=10, MAX_PRODUCTS_IN_CONTEXT=2)
class TotalNoteTests(TestCase):
    NOTE = 'Hay 3 productos en total y te muestro 2.'

    def setUp(self):
        for index in (1, 2, 3):
            Product.objects.create(sku=f'S-{index}', name=f'Serum Anua {index}', category='Sérum', price='10.00', stock=2)
        self.chat = patch('applications.core.agent.chat_with_tools').start()
        self.addCleanup(patch.stopall)
        self.conversation = SimpleNamespace(provider='openrouter')

    def ask(self, final_text, question='tienes serum?', consulta='serum'):
        self.chat.side_effect = [_call('buscar_productos', {'consulta': consulta}), _text(final_text)]
        return str(answer_with_agent(self.conversation, question))

    def test_the_tool_tells_the_model_the_total(self):
        result = run_tool('buscar_productos', {'consulta': 'serum'}, {})
        self.assertIn('3 productos en total', result['nota'])
        self.assertNotIn('nota', run_tool('buscar_productos', {'consulta': 'serum anua 1'}, {}))

    def test_the_server_adds_the_total_when_the_model_forgets_it(self):
        answer = self.ask('Tengo estos:\n1. *Serum Anua 1*\n2. *Serum Anua 2*\n¿Cuál te interesa?')
        self.assertTrue(answer.endswith('Si me dices la marca, el tipo o el tamaño, afino la búsqueda 😉'))
        self.assertIn(self.NOTE, answer)

    def test_no_note_when_the_answer_already_says_the_total(self):
        answer = self.ask('Hay 3 en total. Te muestro *Serum Anua 1* y *Serum Anua 2*.')
        self.assertNotIn('afino la búsqueda', answer)

    def test_no_note_when_the_answer_is_about_a_single_product(self):
        answer = self.ask('El *Serum Anua 1* es el más sencillo.')
        self.assertNotIn('en total', answer)

    def test_no_note_when_everything_fits(self):
        answer = self.ask('Solo tengo *Serum Anua 1*.', consulta='serum anua 1')
        self.assertNotIn('en total', answer)


class RequestedFiguresTests(SimpleTestCase):
    def test_detection(self):
        cases = {
            'cuánto cuesta?': {'precio'}, 'precio del toner': {'precio'}, 'cuál es el más barato?': {'precio'},
            'y cuánto stock hay del primero?': {'stock'}, 'cuántas unidades quedan': {'stock'},
            'hay disponible?': {'stock'}, 'precio y stock': {'precio', 'stock'}, 'cuánto es?': {'precio'},
            'tienes protector solar?': set(), 'para qué sirve?': set(), 'hay protector?': set(),
        }
        for question, expected in cases.items():
            with self.subTest(question=question):
                self.assertEqual(requested_figures(question), expected)


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

    def test_a_sku_not_returned_in_this_message_is_rejected_even_after_the_retry(self):
        self.chat.side_effect = [_text('Cuesta {precio:P-2}'), _text('Cuesta {precio:P-2}')]
        with self.assertRaises(AgentRejected):
            answer_with_agent(self.conversation, 'cuánto cuesta la crema barata?')
        self.assertEqual(self.chat.call_count, 2)

    def test_a_made_up_price_is_rejected_even_after_the_retry(self):
        bad = _text('La *Crema barata* cuesta S/ 25.00')
        self.chat.side_effect = [_call('ver_producto', {'sku': 'P-2'}), bad, bad]
        with self.assertRaises(AgentRejected):
            answer_with_agent(self.conversation, 'cuánto cuesta la crema barata?')

    def test_the_retry_can_fix_a_stock_answer_given_from_memory(self):
        history = [{'role': 'assistant', 'content': '1. *Crema barata*\n2. *Crema cara* está agotada'}]
        self.chat.side_effect = [_text('La *Crema barata* está agotada.'),
                                 _call('ver_producto', {'nombre': 'Crema barata'}),
                                 _text('De la *Crema barata** {stock:P-2}.')]
        answer = answer_with_agent(self.conversation, 'y cuánto stock hay de la primera?', history)
        self.assertEqual(str(answer), 'De la *Crema barata* hay 3 unidades.')
        notices = [m for m in self.chat.call_args_list[1].args[1] if '[Aviso del sistema]' in str(m.get('content'))]
        self.assertEqual(len(notices), 1)
        self.assertEqual(notices[0]['role'], 'user')
        self.assertIn('stock', notices[0]['content'])
        self.assertEqual(self.chat.call_args_list[1].args[2], agent_tools())

    def test_a_price_question_without_a_marker_is_rejected_when_products_were_found(self):
        self.chat.side_effect = [_call('buscar_productos', {'consulta': 'crema'}),
                                 _text('Tengo dos cremas muy buenas'),
                                 _text('Tengo dos cremas muy buenas')]
        with self.assertRaises(AgentRejected) as caught:
            answer_with_agent(self.conversation, 'qué precio tienen las cremas?')
        self.assertEqual(caught.exception.reason, 'missing_requested_figure')

    def test_no_marker_is_needed_when_the_search_found_nothing(self):
        self.chat.side_effect = [_call('buscar_productos', {'consulta': 'labial mate'}),
                                 _text('No encontré ese producto. ¿Me das la marca?')]
        answer = answer_with_agent(self.conversation, 'cuánto cuesta el labial mate?')
        self.assertIn('No encontré', answer)
        self.assertEqual(self.chat.call_count, 2)

    def test_the_retry_happens_only_once(self):
        self.chat.side_effect = [_text('Cuesta 10.00'), _text('Cuesta 11.00'), _text('{precio:P-2}')]
        with self.assertRaises(AgentRejected):
            answer_with_agent(self.conversation, 'hola')
        self.assertEqual(self.chat.call_count, 2)

    def test_the_price_marker_works_after_looking_up_by_name(self):
        self.chat.side_effect = [_call('ver_producto', {'nombre': 'Crema cara'}), _text('Está en {precio:P-1} y {stock:P-1}.')]
        self.assertEqual(str(answer_with_agent(self.conversation, 'y la cara?')), 'Está en S/ 120.00 y está agotado.')

    def test_figures_from_the_history_are_not_rejected(self):
        history = [{'role': 'user', 'content': 'la más barata?'},
                   {'role': 'assistant', 'content': 'La *Crema barata* cuesta S/ 30.00 y hay 3 unidades.'}]
        self.chat.side_effect = [_text('Sí, son esas 3 unidades a S/ 30.00.')]
        self.assertEqual(str(answer_with_agent(self.conversation, 'cuántas había?', history)), 'Sí, son esas 3 unidades a S/ 30.00.')

    def test_rejection_is_an_agent_error_so_the_caller_can_fall_back(self):
        self.chat.side_effect = [_text('Cuesta 10.00'), _text('Cuesta 10.00')]
        with self.assertRaises(AgentError):
            answer_with_agent(self.conversation, 'precio?')
