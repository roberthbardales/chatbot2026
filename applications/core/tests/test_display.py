"""Lo que ve el cliente: SKU internos ocultos y stock agotado."""
from django.test import SimpleTestCase

from ..responses import _render_sheet_selection, display_sku
from ..services import _sheet_ambiguity

HASH = '7523921e81df859b694f6d0494747d3e'


def product(sku, **extra):
    return {'sku': sku, 'name': f'Producto {sku[:4]}', 'price': '10.00', 'stock': 5, 'unit': '', **extra}


class DisplaySkuTests(SimpleTestCase):
    def test_hash_like_ids_are_hidden_but_readable_skus_are_kept(self):
        self.assertEqual(display_sku(HASH), '')
        self.assertEqual(display_sku(HASH.upper()), '')
        self.assertEqual(display_sku('VAL-01'), 'VAL-01')
        self.assertEqual(display_sku('A' * 31), 'A' * 31)
        self.assertEqual(display_sku(HASH + '-X'), HASH + '-X')

    def test_ambiguity_list_hides_internal_ids_and_does_not_ask_for_a_sku(self):
        text = str(_sheet_ambiguity([product(HASH), product('53ae37d5450bbd896a5522a638eb965f')]))
        self.assertNotIn('SKU', text)
        self.assertIn('1. Producto 7523', text)
        self.assertIn('con el número de opción y', text)

    def test_ambiguity_context_still_stores_the_real_skus(self):
        answer = _sheet_ambiguity([product(HASH)])
        self.assertEqual(answer.inventory_context, {'candidates': [HASH]})

    def test_ambiguity_keeps_readable_skus(self):
        text = str(_sheet_ambiguity([product('VAL-01'), product('VAL-02')]))
        self.assertIn('— SKU VAL-01', text)
        self.assertIn('o el SKU', text)

    def test_answer_hides_internal_id_but_shows_readable_sku(self):
        hidden = _render_sheet_selection([(product(HASH), ['precio'])], False)
        self.assertNotIn('SKU', hidden)
        shown = _render_sheet_selection([(product('VAL-01'), ['precio'])], False)
        self.assertIn('(SKU VAL-01)', shown)


class ZeroStockWordingTests(SimpleTestCase):
    def test_zero_stock_says_agotado_with_or_without_unit(self):
        for unit in ('', 'unidades'):
            text = _render_sheet_selection([(product('VAL-01', stock=0, unit=unit), ['stock'])], False)
            self.assertIn('está agotado', text)
            self.assertNotIn('hay 0', text)

    def test_unknown_stock_is_not_reported_as_sold_out(self):
        text = _render_sheet_selection([(product('VAL-01', stock=None), ['stock'])], False)
        self.assertIn('no tengo el stock', text)
        self.assertNotIn('agotado', text)

    def test_positive_stock_is_unchanged(self):
        text = _render_sheet_selection([(product('VAL-01', stock=8, unit='unidades'), ['stock'])], False)
        self.assertIn('hay 8 unidades', text)

    def test_single_unit_is_singular(self):
        text = _render_sheet_selection([(product('VAL-01', stock=1, unit='unidades'), ['stock'])], False)
        self.assertIn('hay 1 unidad.', text)
        self.assertNotIn('1 unidades', text)


class CosmeticsWordingTests(SimpleTestCase):
    """Los textos que ve el cliente no deben conservar vocabulario del catálogo industrial."""

    INDUSTRIAL = ('medida', 'conexión', 'conexion', 'pulgada', 'válvula', 'valvula', 'tubería', 'bronce', 'npt')

    def texts(self):
        from ..responses import _CLOSERS
        from ..services import PRODUCT_NOT_FOUND, _sheet_not_found
        image_help = str(_sheet_not_found('data:image/jpeg;base64,abc'))
        from_image = _render_sheet_selection([(product('VAL-01'), ['precio'])], True)
        return [*_CLOSERS, PRODUCT_NOT_FOUND, image_help, from_image]

    def test_customer_texts_have_no_industrial_vocabulary(self):
        for text in self.texts():
            for word in self.INDUSTRIAL:
                with self.subTest(text=text[:40], word=word):
                    self.assertNotIn(word, text.lower())
