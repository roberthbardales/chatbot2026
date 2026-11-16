"""«Disponible» sin cantidad: se guarda y se dice, sin inventar un número."""
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase, override_settings

from .inventory import product_by_sku
from .inventory_source import InventoryError, _product_values
from .inventory_sync import apply_inventory, preview_inventory
from .models import Product
from .responses import _render_sheet_selection

BASE = {'sku': 'A1', 'producto': 'Crema', 'precio': '10.00'}


def parsed(**extra):
    return _product_values({**BASE, **extra})


class AvailabilityParsingTests(SimpleTestCase):
    def test_si_without_quantity_is_available_and_stock_stays_unknown(self):
        values = parsed(disponible='Sí')
        self.assertIs(values['available'], True)
        self.assertIsNone(values['stock'])

    def test_no_is_unavailable_with_zero_stock(self):
        values = parsed(disponible='No')
        self.assertIs(values['available'], False)
        self.assertEqual(values['stock'], 0)

    def test_accents_case_and_spaces_are_ignored(self):
        for text in ('Sí', 'si', ' SI ', 'Yes', 'disponible'):
            self.assertIs(parsed(disponible=text)['available'], True, text)
        for text in ('NO', 'no', 'Agotado'):
            self.assertIs(parsed(disponible=text)['available'], False, text)

    def test_blank_or_missing_column_means_no_data(self):
        self.assertIsNone(parsed(disponible='')['available'])
        self.assertIsNone(parsed()['available'])

    def test_unknown_value_is_rejected_instead_of_hidden(self):
        with self.assertRaises(InventoryError):
            parsed(disponible='quizás')

    def test_quantity_wins_over_the_disponible_column(self):
        self.assertEqual((parsed(disponible='No', stock='7')['stock'], parsed(disponible='No', stock='7')['available']), (7, True))
        self.assertIs(parsed(disponible='Sí', stock='0')['available'], False)

    def test_availability_changes_the_content_hash(self):
        self.assertNotEqual(parsed(disponible='Sí')['content_hash'], parsed(disponible='')['content_hash'])


def item(**extra):
    return {'sku': 'A1', 'name': 'Crema Eucerin', 'price': '10.00', 'stock': None, 'available': None, 'unit': '', **extra}


def say(product):
    return _render_sheet_selection([(product, ['stock'])], False)


class AvailabilityWordingTests(SimpleTestCase):
    def test_available_without_quantity_asks_to_check_the_exact_amount(self):
        self.assertIn('está disponible; consulta la cantidad exacta', say(item(available=True)))
        self.assertNotIn('no tengo el stock', say(item(available=True)))

    def test_no_data_at_all_still_says_it_does_not_know(self):
        self.assertIn('no tengo el stock en el inventario', say(item()))

    def test_quantity_and_sold_out_keep_their_wording(self):
        self.assertIn('hay 8 unidades', say(item(stock=8, unit='unidades', available=True)))
        self.assertIn('está agotado', say(item(stock=0, available=False)))
        self.assertIn('está agotado', say(item(available=False)))


@override_settings(GOOGLE_SHEET_PRODUCTS_CSV_URL='products')
class AvailabilitySyncTests(TestCase):
    ROWS = [{'sku': 'A1', 'producto': 'Crema A', 'precio': '10', 'disponible': 'Sí'},
            {'sku': 'B2', 'producto': 'Crema B', 'precio': '12', 'disponible': 'No'},
            {'sku': 'C3', 'producto': 'Crema C', 'precio': '14', 'disponible': ''}]

    def sync(self):
        with patch('applications.chat.inventory_sync._download_rows', return_value=self.ROWS):
            plan = preview_inventory()
            apply_inventory(confirm=plan.fingerprint)

    def test_sync_persists_availability_and_search_exposes_it(self):
        self.sync()
        self.assertEqual({p.sku: (p.available, p.stock) for p in Product.objects.all()},
                         {'A1': (True, None), 'B2': (False, 0), 'C3': (None, None)})
        self.assertIs(product_by_sku('A1')['available'], True)

    def test_changing_only_the_availability_is_detected_as_an_update(self):
        self.sync()
        self.ROWS = [{**self.ROWS[0], 'disponible': 'No'}] + self.ROWS[1:]
        with patch('applications.chat.inventory_sync._download_rows', return_value=self.ROWS):
            plan = preview_inventory()
        update = plan.summary['products']['update']
        self.assertEqual([entry['key'] for entry in update], ['A1'])
        self.assertIn('available', update[0]['changes'])
