import csv
import hashlib
import io
import re
import unicodedata
from decimal import Decimal, InvalidOperation
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

from django.conf import settings
from django.db import transaction

from .models import Combo, Product


class InventoryError(Exception):
    pass


def _header(value):
    value = unicodedata.normalize('NFKD', value or '')
    value = ''.join(char for char in value if not unicodedata.combining(char))
    return re.sub(r'[^a-z0-9]+', '_', value.lower()).strip('_')


def _row(row):
    return {_header(key): (value or '').strip() for key, value in row.items() if key}


def _value(row, *keys):
    for key in keys:
        if row.get(key):
            return row[key]
    return ''


def _active(value):
    return str(value).lower().strip() not in {'0', 'false', 'no', 'inactivo', 'inactive'}


def _decimal(value):
    if not value:
        return None
    try:
        return Decimal(str(value).replace(',', '.'))
    except (InvalidOperation, TypeError):
        return None


def _integer(value):
    try:
        return int(float(str(value).replace(',', '.'))) if value else None
    except (TypeError, ValueError):
        return None


def _download_rows(url):
    if not url:
        raise InventoryError('Google Sheets URL is not configured')
    try:
        with urlopen(url, timeout=20) as response:
            content = response.read().decode('utf-8-sig')
    except (HTTPError, URLError, TimeoutError, OSError, UnicodeError) as exc:
        raise InventoryError('No se pudo descargar Google Sheets') from exc
    rows = list(csv.DictReader(io.StringIO(content)))
    if not rows:
        raise InventoryError('Google Sheets no contiene filas')
    return [_row(row) for row in rows]


def _product_values(row):
    sku = _value(row, 'sku', 'codigo', 'codigo_producto')
    name = _value(row, 'name', 'nombre', 'producto', 'title')
    if not sku or not name:
        return None
    values = {
        'sku': sku[:120],
        'name': name[:255],
        'category': _value(row, 'category', 'categoria')[:120],
        'brand': _value(row, 'brand', 'marca')[:120],
        'model': _value(row, 'model', 'modelo')[:120],
        'specs': _value(row, 'specs', 'especificaciones'),
        'price': _decimal(_value(row, 'price', 'precio')),
        'stock': _integer(_value(row, 'stock', 'inventario', 'existencias')),
        'unit': _value(row, 'unit', 'unidad')[:80],
        'keywords': _value(row, 'keywords', 'palabras_clave', 'palabras_clave_busqueda'),
        'description': _value(row, 'description', 'descripcion'),
        'active': _active(_value(row, 'active', 'activo') or '1'),
    }
    content = '|'.join(str(values[key]) for key in values)
    values['content_hash'] = hashlib.sha256(content.encode('utf-8')).hexdigest()
    return values


def _combo_values(row):
    name = _value(row, 'name', 'nombre', 'combo')
    if not name:
        return None
    skus = _value(row, 'product_skus', 'skus', 'productos', 'sku_productos')
    values = {
        'name': name[:255],
        'description': _value(row, 'description', 'descripcion'),
        'product_skus': skus,
        'price': _decimal(_value(row, 'combo_price', 'price', 'precio')),
        'active': _active(_value(row, 'active', 'activo') or '1'),
    }
    content = '|'.join(str(values[key]) for key in values)
    values['content_hash'] = hashlib.sha256(content.encode('utf-8')).hexdigest()
    return values


def sync_inventory():
    raw_product_rows = _download_rows(settings.GOOGLE_SHEET_PRODUCTS_CSV_URL)
    product_rows = [_product_values(row) for row in raw_product_rows]
    invalid_products = [row for row, values in zip(raw_product_rows, product_rows) if any(row.values()) and values is None]
    product_rows = [row for row in product_rows if row]
    if not product_rows:
        raise InventoryError('La hoja de productos no contiene filas válidas')
    if invalid_products:
        raise InventoryError('La hoja de productos contiene filas sin SKU o nombre')
    product_skus = [row['sku'].lower() for row in product_rows]
    if len(product_skus) != len(set(product_skus)):
        raise InventoryError('La hoja de productos contiene SKUs duplicados')
    combo_rows = []
    if settings.GOOGLE_SHEET_COMBOS_CSV_URL:
        raw_combo_rows = _download_rows(settings.GOOGLE_SHEET_COMBOS_CSV_URL)
        parsed_combos = [_combo_values(row) for row in raw_combo_rows]
        if any(any(row.values()) and values is None for row, values in zip(raw_combo_rows, parsed_combos)):
            raise InventoryError('La hoja de combos contiene filas sin nombre')
        combo_rows = parsed_combos
        combo_rows = [row for row in combo_rows if row]
    with transaction.atomic():
        Product.objects.all().delete()
        Combo.objects.all().delete()
        Product.objects.bulk_create([Product(**row) for row in product_rows])
        Combo.objects.bulk_create([Combo(**row) for row in combo_rows])
    return len(product_rows), len(combo_rows)


def _normalize(value):
    value = unicodedata.normalize('NFKD', value or '')
    value = ''.join(char for char in value if not unicodedata.combining(char))
    return re.sub(r'[^a-z0-9]+', ' ', value.lower()).strip()


def _as_dict(product):
    return {
        'id': product.sku,
        'sku': product.sku,
        'title': product.name,
        'name': product.name,
        'category': product.category,
        'brand': product.brand,
        'model': product.model,
        'specs': product.specs,
        'price': float(product.price) if product.price is not None else None,
        'stock': product.stock,
        'unit': product.unit,
        'keywords': product.keywords,
        'description': product.description,
    }


def _score(query, product):
    query_terms = set(_normalize(query).split())
    haystack = _normalize(' '.join((product.name, product.sku, product.brand, product.model, product.keywords, product.category)))
    haystack_terms = set(haystack.split())
    return len(query_terms & haystack_terms) / len(query_terms) if query_terms else 0


def search_products(query, limit=6):
    products = [product for product in Product.objects.filter(active=True) if _score(query, product) > 0]
    return [_as_dict(product) for product in sorted(products, key=lambda item: _score(query, item), reverse=True)[:limit]]


def product_by_sku(sku):
    product = Product.objects.filter(sku__iexact=sku, active=True).first()
    return _as_dict(product) if product else None


def category_products(category, limit=6):
    products = Product.objects.filter(active=True, category__icontains=category)[:limit]
    return [_as_dict(product) for product in products]


def combos_for_products(products):
    skus = {product.get('sku') for product in products}
    result = []
    for combo in Combo.objects.filter(active=True):
        combo_skus = {sku.strip() for sku in combo.product_skus.split(',') if sku.strip()}
        if skus & combo_skus:
            result.append({
                'name': combo.name,
                'description': combo.description,
                'price': float(combo.price) if combo.price is not None else None,
                'product_skus': combo_skus,
            })
    return result
