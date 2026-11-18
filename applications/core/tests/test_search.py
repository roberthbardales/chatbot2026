
from django.test import TestCase


from ..models import Product
from ..inventory import category_products, search_products as search_inventory
from ..services import answer_sheet_message
from types import SimpleNamespace


class InventorySearchTests(TestCase):
    def product(self, sku, name, **kwargs):
        return Product.objects.create(sku=sku, name=name, **kwargs)

    def test_size_variants_find_the_same_product(self):
        self.product('SER-30', 'Serum Vitamina C 30ml')
        for query in ('serum vitamina c 30 ml', 'serum vitamina c 30ml', 'serum vitamina c 30 mililitros'):
            with self.subTest(query=query):
                self.assertEqual([item['sku'] for item in search_inventory(query)], ['SER-30'])

    def test_synonyms_connect_spanish_and_english_words(self):
        self.product('SUN-1', 'Beauty of Joseon Relief Sun Sunscreen', category='Protector solar')
        self.product('CLE-1', 'Cosrx Low pH Cleanser', category='Limpiador')
        self.product('CRE-1', 'Crema Hidratante de Ceramidas', category='Crema e hidratante')
        self.product('LAB-1', 'Carmex Bálsamo Labial', category='Labios')
        self.product('SER-1', 'Anua Niacinamide Serum', category='Sérum y ampolla')
        cases = [('bloqueador solar', 'SUN-1'), ('sunscreen', 'SUN-1'), ('limpiadores', 'CLE-1'),
                 ('cleanser', 'CLE-1'), ('cream de ceramidas', 'CRE-1'),
                 ('labial', 'LAB-1'), ('serums', 'SER-1'), ('ampollas', 'SER-1')]
        for query, sku in cases:
            with self.subTest(query=query):
                found = [item['sku'] for item in search_inventory(query)]
                self.assertEqual(found, [sku] if sku else [])

    def test_all_terms_are_required_not_partial_matches(self):
        self.product('CRE-50', 'Crema Hidratante 50ml')
        self.product('CRE-100', 'Crema Hidratante 100ml')
        self.assertEqual([item['sku'] for item in search_inventory('crema hidratante 50 ml')], ['CRE-50'])
        self.assertEqual(search_inventory('crema hidratante 200 ml'), [])
        self.assertEqual(search_inventory('crema hidratante ZX999'), [])

    def test_results_keep_name_order_and_respect_the_limit(self):
        for index in (3, 1, 2):
            self.product(f'CRE-{index}', f'Crema {index}')
        self.assertEqual([item['sku'] for item in search_inventory('crema')], ['CRE-1', 'CRE-2', 'CRE-3'])
        self.assertEqual([item['sku'] for item in search_inventory('crema', limit=2)], ['CRE-1', 'CRE-2'])

    def test_a_query_with_only_stop_words_finds_nothing(self):
        self.product('CRE-1', 'Crema')
        self.assertEqual(search_inventory('hola, tienes precio?'), [])

    def test_product_terms_follow_changes_made_by_a_sync(self):
        item = self.product('SER-1', 'Serum Antiguo', content_hash='a')
        self.assertEqual([p['sku'] for p in search_inventory('serum antiguo')], ['SER-1'])
        Product.objects.filter(pk=item.pk).update(name='Serum Nuevo', content_hash='b')
        self.assertEqual(search_inventory('serum antiguo'), [])
        self.assertEqual([p['sku'] for p in search_inventory('serum nuevo')], ['SER-1'])

    def test_exact_reference_wins_without_confusing_similar_skus(self):
        self.product('VAL-01', 'Válvula de bronce')
        self.product('VAL-010', 'Válvula de acero')
        self.product('ABC/2.5_X', 'Adaptador especial')
        self.assertEqual([item['sku'] for item in search_inventory('¿Cuánto cuesta val-01?')], ['VAL-01'])
        self.assertEqual([item['sku'] for item in search_inventory('ABC/2.5_X')], ['ABC/2.5_X'])
        self.assertEqual(search_inventory('VAL-019'), [])

    def test_inactive_products_are_excluded_and_specs_are_searchable(self):
        self.product('OFF', 'Serum', active=False)
        self.product('ON', 'Serum', specs='Niacinamida 10%')
        self.assertEqual([item['sku'] for item in search_inventory('serum niacinamida')], ['ON'])
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
