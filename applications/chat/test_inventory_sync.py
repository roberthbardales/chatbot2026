import io
import json
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from datetime import timedelta
from threading import Barrier
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import close_old_connections
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone

from .inventory import preview_inventory, product_by_sku, sync_inventory
from .inventory_source import InventoryError, _decimal, _download_rows, _integer
from .inventory_sync import inventory_status
from .models import InventorySyncLock, InventorySyncRun, Product


@override_settings(GOOGLE_SHEET_PRODUCTS_CSV_URL='products',
                   INVENTORY_MIN_CATALOG_FOR_DROP_GUARD=10, INVENTORY_MAX_DEACTIVATE_PERCENT=25)
class InventorySyncTests(TestCase):
    def setUp(self):
        self.product = Product.objects.create(sku='VAL-01', name='Válvula antigua', price='25.00', stock=8)
        self.rows = [{'sku': 'VAL-01', 'nombre': 'Válvula nueva', 'precio': '27,35', 'stock': '9'}]
        self.download = patch('applications.chat.inventory_sync._download_rows', side_effect=self.source).start()
        self.addCleanup(patch.stopall)

    def source(self, url, **kwargs):
        return self.rows

    def apply(self, **kwargs):
        return sync_inventory(confirm=preview_inventory().fingerprint, **kwargs)

    def test_preview_has_values_and_no_catalog_or_history_writes(self):
        plan = preview_inventory()
        changes = plan.summary['products']['update'][0]['changes']
        self.assertEqual(changes['price'], {'before': '25.00', 'after': '27.35'})
        self.product.refresh_from_db()
        self.assertEqual(self.product.price, Decimal('25.00'))
        self.assertFalse(InventorySyncRun.objects.exists())
        self.assertFalse(InventorySyncLock.objects.exists())

    def test_apply_updates_by_sku_preserving_id_and_canonical_case(self):
        self.rows[0]['sku'] = 'val-01'
        run = self.apply()
        self.product.refresh_from_db()
        self.assertEqual(Product.objects.get().pk, self.product.pk)
        self.assertEqual(self.product.sku, 'VAL-01')
        self.assertEqual(self.product.name, 'Válvula nueva')
        self.assertEqual(self.product.price, Decimal('27.35'))
        self.assertEqual(self.product.stock, 9)
        self.assertEqual(run.status, 'applied')
        self.assertIsNotNone(inventory_status()['last_success_at'])

    def test_missing_products_are_deactivated_and_can_return_with_same_id(self):
        other = Product.objects.create(sku='OTRO', name='Otro')
        self.apply()
        other.refresh_from_db()
        self.assertFalse(other.active)
        self.assertEqual(Product.objects.count(), 2)
        self.rows.append({'sku': 'OTRO', 'nombre': 'Regresó'})
        self.apply()
        other.refresh_from_db()
        self.assertTrue(other.active)
        self.assertEqual(Product.objects.get(sku='OTRO').pk, other.pk)

    def test_unchanged_import_does_not_touch_product_timestamp(self):
        self.apply()
        self.product.refresh_from_db()
        stamp = self.product.updated_at
        plan = preview_inventory()
        self.assertEqual(plan.summary['products']['unchanged'], 1)
        sync_inventory(confirm=plan.fingerprint)
        self.product.refresh_from_db()
        self.assertEqual(self.product.updated_at, stamp)

    def test_default_command_is_preview_and_requires_explicit_confirmation_to_apply(self):
        output = io.StringIO()
        call_command('sync_inventory', json=True, stdout=output)
        data = json.loads(output.getvalue())
        self.assertEqual(data['status'], 'preview')
        self.assertFalse(InventorySyncRun.objects.exists())
        with self.assertRaises(CommandError):
            call_command('sync_inventory', apply=True, stdout=io.StringIO())
        self.assertFalse(InventorySyncRun.objects.exists())
        call_command('sync_inventory', apply=True, confirm=data['fingerprint'], stdout=io.StringIO())
        self.assertEqual(InventorySyncRun.objects.get().status, 'applied')

    def test_changed_sheet_or_database_invalidates_preview(self):
        plan = preview_inventory()
        self.rows[0]['precio'] = '28.00'
        with self.assertRaises(InventoryError):
            sync_inventory(confirm=plan.fingerprint)
        self.product.refresh_from_db()
        self.assertEqual(self.product.price, Decimal('25'))
        self.assertEqual(InventorySyncRun.objects.get().status, 'failed')
        plan = preview_inventory()
        Product.objects.filter(pk=self.product.pk).update(stock=99)
        with self.assertRaises(InventoryError):
            sync_inventory(confirm=plan.fingerprint)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 99)

    def test_duplicates_after_unicode_and_case_normalization_are_rejected(self):
        self.rows.append({'sku': 'ＶＡＬ-01', 'nombre': 'Duplicado'})
        with self.assertRaises(InventoryError):
            preview_inventory()
        self.assertEqual(Product.objects.count(), 1)

    def test_existing_ambiguous_skus_are_not_silently_merged(self):
        Product.objects.create(sku='val-01', name='Duplicado existente')
        with self.assertRaises(InventoryError):
            preview_inventory()
        self.assertEqual(Product.objects.count(), 2)

    def test_legacy_unicode_sku_is_normalized_without_changing_id(self):
        self.product.sku = 'ＶＡＬ-01'
        self.product.save()
        self.apply()
        self.product.refresh_from_db()
        self.assertEqual(self.product.sku, 'VAL-01')
        self.assertEqual(product_by_sku('ＶＡＬ-01')['sku'], 'VAL-01')

    def test_sheet_row_order_does_not_invalidate_the_same_logical_plan(self):
        self.rows.append({'sku': 'NEW', 'nombre': 'Nuevo'})
        first = preview_inventory().fingerprint
        self.rows.reverse()
        self.assertEqual(preview_inventory().fingerprint, first)

    def test_large_deactivation_requires_additional_explicit_acknowledgement(self):
        Product.objects.bulk_create([Product(sku=f'OLD-{i}', name=f'Antiguo {i}') for i in range(9)])
        plan = preview_inventory()
        self.assertTrue(plan.summary['requires_allow_large_drop'])
        with self.assertRaises(InventoryError):
            sync_inventory(confirm=plan.fingerprint)
        self.assertEqual(Product.objects.filter(active=True).count(), 10)
        sync_inventory(confirm=plan.fingerprint, allow_large_drop=True)
        self.assertEqual(Product.objects.count(), 10)
        self.assertEqual(Product.objects.filter(active=True).count(), 1)

    def test_empty_products_or_invalid_numbers_never_replace_catalog(self):
        for rows in [[], [{'sku': 'X', 'nombre': ''}], [{'sku': 'X', 'nombre': 'X', 'precio': 'NaN'}],
                     [{'sku': 'X', 'nombre': 'X', 'stock': '2.5'}], [{'sku': 'x' * 121, 'nombre': 'X'}]]:
            with self.subTest(rows=rows):
                self.rows = rows
                with self.assertRaises(InventoryError):
                    preview_inventory()
        self.assertEqual(Product.objects.get().pk, self.product.pk)
        self.assertFalse(InventorySyncRun.objects.exists())

    def test_catalog_changes_roll_back_if_apply_fails_midway(self):
        plan = preview_inventory()
        original = Product.save

        def fail_after_save(instance, *args, **kwargs):
            original(instance, *args, **kwargs)
            raise RuntimeError('database failed after first write')

        with patch('applications.chat.inventory_sync.Product.save', new=fail_after_save):
            with self.assertRaises(InventoryError):
                sync_inventory(confirm=plan.fingerprint)
        self.product.refresh_from_db()
        self.assertEqual(self.product.price, Decimal('25.00'))
        self.assertEqual(InventorySyncRun.objects.get().status, 'failed')

    def test_prices_remain_decimal_in_catalog_lookup(self):
        self.apply()
        product = product_by_sku('VAL-01')
        self.assertIsInstance(product['price'], Decimal)

    def test_excel_exported_columns_are_mapped_without_a_sku_column(self):
        """Hoja publicada desde el Excel real: ID producto, Tipo, Disponible, Precio anterior y URL."""
        self.rows = [
            {'producto': 'Gel Matificante 40ml', 'marca': 'Marca A', 'tipo': 'Cuidado facial', 'precio': '122.90',
             'precio_anterior': '140.00', 'disponible': 'Sí', 'url': 'https://tienda.test/gel',
             'url_imagen_principal': '', 'archivo_imagen': '', 'estado_imagen': 'Error', 'id_producto': 'abc123'},
            {'producto': 'Serum Agotado', 'marca': 'Marca B', 'tipo': '', 'precio': '80',
             'precio_anterior': '', 'disponible': 'No', 'url': '', 'id_producto': 'def456'},
        ]
        self.apply()
        gel = Product.objects.get(sku='abc123')
        self.assertEqual((gel.name, gel.brand, gel.category, gel.price), ('Gel Matificante 40ml', 'Marca A', 'Cuidado facial', Decimal('122.90')))
        self.assertIsNone(gel.stock)
        self.assertEqual(gel.description, 'Precio anterior: 140.00. URL: https://tienda.test/gel')
        agotado = Product.objects.get(sku='def456')
        self.assertEqual(agotado.stock, 0)
        self.assertEqual(agotado.description, '')

    def test_status_command_is_local_read_only(self):
        output = io.StringIO()
        call_command('inventory_status', stdout=output)
        self.assertEqual(json.loads(output.getvalue())['freshness'], 'unknown')
        self.download.assert_not_called()
        self.assertFalse(InventorySyncRun.objects.exists())

    def test_failed_attempt_does_not_erase_last_success_or_freshness(self):
        run = self.apply()
        success_at = inventory_status()['last_success_at']
        fingerprint = preview_inventory().fingerprint
        self.rows[0]['precio'] = 'invalid'
        with self.assertRaises(InventoryError):
            sync_inventory(confirm=fingerprint)
        state = inventory_status()
        self.assertEqual(state['last_success_at'], success_at)
        self.assertEqual(state['last_attempt']['status'], 'failed')
        InventorySyncRun.objects.filter(pk=run.pk).update(finished_at=timezone.now() - timedelta(hours=25))
        self.assertEqual(inventory_status()['freshness'], 'stale')

    def test_identical_clock_ticks_cannot_reorder_attempt_history(self):
        now = timezone.now()
        create = InventorySyncRun.objects.create

        def low_resolution_create(**kwargs):
            kwargs.setdefault('started_at', now)
            return create(**kwargs)

        with patch('applications.chat.inventory_sync.timezone.now', return_value=now), patch(
            'applications.chat.inventory_sync.InventorySyncRun.objects.create', side_effect=low_resolution_create,
        ):
            successful = self.apply()
            fingerprint = preview_inventory().fingerprint
            self.rows[0]['precio'] = 'invalid'
            with self.assertRaises(InventoryError):
                sync_inventory(confirm=fingerprint)
        failed = InventorySyncRun.objects.get(status='failed')
        self.assertGreater(failed.started_at, successful.started_at)
        self.assertGreaterEqual(failed.finished_at, failed.started_at)
        self.assertEqual(inventory_status()['last_attempt']['status'], 'failed')

    def test_alias_conflicts_and_invalid_active_flags_are_rejected(self):
        for extra in [{'codigo': 'OTHER'}, {'activo': 'unexpected'}]:
            with self.subTest(extra=extra):
                self.rows = [{'sku': 'VAL-01', 'nombre': 'Producto', **extra}]
                with self.assertRaises(InventoryError):
                    preview_inventory()


class InventoryCSVValidationTests(TestCase):
    def test_numeric_parsing_is_exact_and_does_not_truncate_stock(self):
        self.assertEqual(_decimal('0,10'), Decimal('0.10'))
        self.assertEqual(_integer('3.0'), 3)
        self.assertIsNone(_decimal(''))
        for value in ['NaN', 'Infinity', '-1', '1.005', '10000000000']:
            with self.subTest(value=value):
                with self.assertRaises(InventoryError):
                    _decimal(value)
        for value in ['1.5', 'NaN', '-1', '2147483648']:
            with self.assertRaises(InventoryError):
                _integer(value)

    @patch('applications.chat.inventory_source.urlopen')
    def test_header_validation_and_byte_limits(self, urlopen):
        def rows(body, **kwargs):
            urlopen.return_value.__enter__.return_value = io.BytesIO(body)
            return _download_rows('https://example.test', required_headers=(('sku',), ('name',)), **kwargs)
        self.assertEqual(rows(b'sku,name\nA,Product\n')[0]['sku'], 'A')
        for body in [b'<html>not csv</html>', b'sku,sku,name\nA,B,Product', b'sku,name\nA,Product,Extra', b'sku,name\nA,"unfinished']:
            with self.subTest(body=body):
                with self.assertRaises(InventoryError):
                    rows(body)
        with override_settings(INVENTORY_MAX_CSV_BYTES=5):
            with self.assertRaises(InventoryError):
                rows(b'sku,name\nA,Product\n')
        with override_settings(INVENTORY_MAX_ROWS=1):
            with self.assertRaises(InventoryError):
                rows(b'sku,name\nA,Product\nB,Other\n')
        self.assertEqual(rows(b'sku,name\n', allow_empty=True), [])


@override_settings(GOOGLE_SHEET_PRODUCTS_CSV_URL='products')
class ConcurrentInventorySyncTests(TransactionTestCase):
    def test_parallel_applies_revalidate_the_locked_catalog(self):
        Product.objects.create(sku='A', name='Before', price='1.00')
        rows = [{'sku': 'A', 'name': 'After', 'price': '2.00'}]
        barrier = Barrier(2)
        with patch('applications.chat.inventory_sync._download_rows', return_value=rows):
            plan = preview_inventory()

            def apply():
                close_old_connections()
                try:
                    barrier.wait(timeout=10)
                    try:
                        sync_inventory(confirm=plan.fingerprint)
                        return True
                    except InventoryError:
                        return False
                finally:
                    close_old_connections()

            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(lambda _: apply(), range(2)))
        self.assertEqual(sorted(results), [False, True])
        self.assertEqual(Product.objects.get().price, Decimal('2.00'))
        self.assertEqual(InventorySyncRun.objects.filter(status='applied').count(), 1)
