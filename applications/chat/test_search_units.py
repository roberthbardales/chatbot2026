"""Búsqueda: contenido neto con o sin espacio y marcas con iniciales con puntos."""
from django.test import SimpleTestCase, TestCase

from .inventory import search_products, search_terms
from .models import Product


class ContentUnitTests(SimpleTestCase):
    def test_spaced_attached_and_spelled_out_units_are_equivalent(self):
        for value in ('500 ml', '500ml', '500 ML', '500 mililitros', '500 cc'):
            self.assertIn('500ml', search_terms(value), value)
        self.assertEqual(search_terms('55 g'), search_terms('55gr'))
        self.assertIn('55g', search_terms('55 gramos'))
        self.assertIn('1l', search_terms('1 litro'))

    def test_units_inside_product_names_are_normalized_too(self):
        self.assertIn('500ml', search_terms('Bioderma Sensibio Gel Moussant 500ml'))
        self.assertIn('50ml', search_terms('Crema 50 ml'))

    def test_plain_numbers_and_words_are_not_merged(self):
        self.assertEqual(search_terms('spf 50'), {'spf', '50'})
        self.assertNotIn('2l', search_terms('crema 2 lineas'))

    def test_existing_dimensions_are_unchanged(self):
        self.assertEqual(search_terms('1/2 pulgada'), {'dim_inch_1_2'})
        self.assertEqual(search_terms('12 mm'), {'dim_mm_12_1'})


class DottedBrandTests(SimpleTestCase):
    def test_dotted_initials_become_one_word(self):
        for value in ('e.l.f.', 'e.l.f', 'E.L.F. Total Face', 'elf'):
            self.assertIn('elf', search_terms(value), value)

    def test_normal_text_with_dots_is_unaffected(self):
        self.assertEqual(search_terms('1.5 lts'), {'1', '5l'})
        self.assertEqual(search_terms('vitamina c. crema'), {'vitamina', 'c', 'crema'})


class CatalogSearchTests(TestCase):
    def setUp(self):
        for sku, name in (('a1', 'Bioderma Sensibio Gel Moussant 500ml'), ('b2', 'e.l.f. Total Face Blending Sponge Duo'),
                          ('c3', 'Eucerin Hyaluron-Filler Crema Facial Noche 50ml')):
            Product.objects.create(sku=sku, name=name, content_hash='x')

    def names(self, query):
        return [product['name'] for product in search_products(query)]

    def test_spaced_unit_finds_the_product(self):
        self.assertEqual(self.names('gel moussant 500 ml'), ['Bioderma Sensibio Gel Moussant 500ml'])
        self.assertEqual(self.names('crema facial 50 ml'), ['Eucerin Hyaluron-Filler Crema Facial Noche 50ml'])

    def test_dotted_brand_is_found_with_or_without_dots(self):
        for query in ('elf blending sponge', 'e.l.f. sponge', 'e.l.f sponge'):
            self.assertEqual(self.names(query), ['e.l.f. Total Face Blending Sponge Duo'], query)

    def test_different_size_does_not_match(self):
        self.assertEqual(self.names('gel moussant 250 ml'), [])
