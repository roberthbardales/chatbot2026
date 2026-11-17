
from django.test import TestCase


from ..models import Product
from ..inventory import category_products, search_products as search_inventory, search_terms
from ..services import answer_sheet_message
from types import SimpleNamespace


class InventorySearchTests(TestCase):
    def product(self, sku, name, **kwargs):
        return Product.objects.create(sku=sku, name=name, **kwargs)

    def test_fraction_and_inch_variants_are_equivalent(self):
        for value in ['½', '1/2', 'media pulgada', '0.5 pulgadas', '1/2"']:
            with self.subTest(value=value):
                self.assertEqual(search_terms(value), {'dim_inch_1_2'})
        self.assertEqual(search_terms('1½ pulgadas'), search_terms('1 1/2 pulgadas'))

    def test_synonyms_find_catalog_products(self):
        self.product('VAL-01', 'Válvula de bronce 1/2 pulgada')
        self.product('COD-01', 'Codo inoxidable')
        for query, sku in [('Necesito una llave de paso de media pulgada', 'VAL-01'),
                           ('válvulas bronze ½', 'VAL-01'), ('elbow inox', 'COD-01')]:
            with self.subTest(query=query):
                self.assertEqual([item['sku'] for item in search_inventory(query)], [sku])

    def test_measure_and_connection_are_required_not_partial_matches(self):
        self.product('VAL-01', 'Válvula de bronce 1/2 pulgada NPT')
        self.product('VAL-02', 'Válvula de bronce 3/4 pulgada BSP')
        self.assertEqual([item['sku'] for item in search_inventory('válvula bronce media pulgada NPT')], ['VAL-01'])
        self.assertEqual(search_inventory('válvula bronce media pulgada BSP'), [])
        self.assertEqual(search_inventory('válvula bronce ZX999'), [])

    def test_mm_and_inches_are_not_equivalent(self):
        self.product('MM-15', 'Tubo 15 mm')
        self.product('IN-15', 'Tubo 15 pulgadas')
        self.assertEqual([item['sku'] for item in search_inventory('tubería 15 milímetros')], ['MM-15'])
        self.assertEqual([item['sku'] for item in search_inventory('tubo 15 pulgadas')], ['IN-15'])

    def test_exact_reference_wins_without_confusing_similar_skus(self):
        self.product('VAL-01', 'Válvula de bronce')
        self.product('VAL-010', 'Válvula de acero')
        self.product('ABC/2.5_X', 'Adaptador especial')
        self.assertEqual([item['sku'] for item in search_inventory('¿Cuánto cuesta val-01?')], ['VAL-01'])
        self.assertEqual([item['sku'] for item in search_inventory('ABC/2.5_X')], ['ABC/2.5_X'])
        self.assertEqual(search_inventory('VAL-019'), [])

    def test_inactive_products_are_excluded_and_specs_are_searchable(self):
        self.product('OFF', 'Válvula bronce', active=False)
        self.product('ON', 'Válvula', specs='Bronce, conexión NPT 1/2 pulgada')
        self.assertEqual([item['sku'] for item in search_inventory('válvula bronce NPT media pulgada')], ['ON'])
        self.assertEqual(search_inventory('OFF'), [])


class CategorySearchTests(TestCase):
    def setUp(self):
        Product.objects.create(sku='A1', name='Sérum uno', category='Sérum y ampolla')
        Product.objects.create(sku='A2', name='Tónico dos', category='Tónico y bruma')
        Product.objects.create(sku='A3', name='Crema tres', category='Crema e hidratante')
        Product.objects.create(sku='A4', name='Sérum inactivo', category='Sérum y ampolla', active=False)
        Product.objects.create(sku='A5', name='Sin categoría')

    def skus(self, query, **kwargs):
        return [item['sku'] for item in category_products(query, **kwargs)]

    def test_category_matches_with_or_without_accents_and_case(self):
        for query in ['sérum', 'serum', 'SERUM', 'Sérum y ampolla']:
            with self.subTest(query=query):
                self.assertEqual(self.skus(query), ['A1'])
        self.assertEqual(self.skus('tonico'), ['A2'])

    def test_category_excludes_inactive_and_empty_and_respects_limit(self):
        self.assertEqual(self.skus('xyz'), [])
        self.assertEqual(self.skus('   '), [])
        self.assertEqual(self.skus('y', limit=5), ['A1', 'A2'])
        self.assertEqual(self.skus('y', limit=1), ['A1'])

    def test_category_command_in_chat_finds_products_without_accent(self):
        answer = answer_sheet_message(SimpleNamespace(provider='openrouter'), 'categoría: serum')
        self.assertIn('Sérum uno', answer)
