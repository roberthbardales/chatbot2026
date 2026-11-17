"""Lectura y validación de la hoja de productos (CSV publicado desde Google Sheets)."""
import csv
import hashlib
import io
import re
import unicodedata
from decimal import Decimal, InvalidOperation
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

from django.conf import settings


class InventoryError(Exception):
    pass


def _header(value):
    value = unicodedata.normalize('NFKD', value or '')
    value = ''.join(char for char in value if not unicodedata.combining(char))
    return re.sub(r'[^a-z0-9]+', '_', value.lower()).strip('_')


def _row(row):
    if None in row:
        raise InventoryError('El CSV contiene columnas adicionales sin cabecera')
    normalized = {}
    for key, value in row.items():
        if not key:
            continue
        header = _header(key)
        if header in normalized:
            raise InventoryError('El CSV contiene cabeceras duplicadas tras normalizar')
        normalized[header] = (value or '').strip()
    return normalized


def _value(row, *keys):
    values = [row[key] for key in keys if row.get(key)]
    if len(set(values)) > 1:
        raise InventoryError('Hay columnas alias con valores contradictorios: ' + ', '.join(keys))
    return values[0] if values else ''


def _active(value):
    value = str(value).lower().strip()
    if value in {'1', 'true', 'si', 'sí', 'yes', 'activo', 'active'}:
        return True
    if value in {'0', 'false', 'no', 'inactivo', 'inactive'}:
        return False
    raise InventoryError('El indicador activo debe ser verdadero o falso')


def _decimal(value):
    if value == '' or value is None:
        return None
    try:
        amount = Decimal(str(value).replace(',', '.'))
        if not amount.is_finite() or not Decimal('0') <= amount <= Decimal('9999999999.99'):
            raise InvalidOperation
        rounded = amount.quantize(Decimal('0.01'))
        if rounded != amount:
            raise InvalidOperation
        return rounded
    except (InvalidOperation, TypeError, ValueError):
        raise InventoryError('Precio inválido: debe ser no negativo y tener como máximo dos decimales') from None


def _integer(value):
    if value == '' or value is None:
        return None
    try:
        amount = Decimal(str(value).replace(',', '.'))
        if not amount.is_finite() or not Decimal('0') <= amount <= Decimal('2147483647') or amount != amount.to_integral_value():
            raise InvalidOperation
        return int(amount)
    except (InvalidOperation, TypeError, ValueError):
        raise InventoryError('Stock inválido: debe ser un entero no negativo') from None


def _download_rows(url, *, allow_empty=False, required_headers=()):
    if not url:
        raise InventoryError('Google Sheets URL is not configured')
    try:
        with urlopen(url, timeout=20) as response:
            raw = response.read(settings.INVENTORY_MAX_CSV_BYTES + 1)
            if len(raw) > settings.INVENTORY_MAX_CSV_BYTES:
                raise InventoryError('El CSV supera el límite de tamaño configurado')
            content = raw.decode('utf-8-sig')
    except (HTTPError, URLError, TimeoutError, OSError, UnicodeError) as exc:
        raise InventoryError('No se pudo descargar Google Sheets') from exc
    reader = csv.DictReader(io.StringIO(content), strict=True)
    rows = []
    try:
        headers = [_header(key) for key in (reader.fieldnames or [])]
        if not headers or any(not header for header in headers) or len(headers) != len(set(headers)) or any(not any(alias in headers for alias in group) for group in required_headers):
            raise InventoryError('Cabeceras CSV ausentes, duplicadas o incompletas')
        for row in reader:
            if len(rows) >= settings.INVENTORY_MAX_ROWS:
                raise InventoryError('El CSV supera el límite de filas configurado')
            rows.append(_row(row))
    except csv.Error as exc:
        raise InventoryError('CSV inválido') from exc
    if not rows and not allow_empty:
        raise InventoryError('Google Sheets no contiene filas')
    return rows


def _text(value, maximum, label):
    if len(value) > maximum:
        raise InventoryError(f'{label} supera {maximum} caracteres')
    return value


def _available_stock(row):
    """Stock explícito o, si la hoja solo indica «Disponible», «No» equivale a stock 0."""
    stock = _value(row, 'stock', 'inventario', 'existencias')
    if stock:
        return stock
    available = unicodedata.normalize('NFKD', row.get('disponible', '')).encode('ascii', 'ignore').decode().strip().lower()
    return '0' if available == 'no' else ''


def _availability(row, stock):
    """Disponibilidad con una sola fuente de verdad: la cantidad manda; si no hay, «Disponible»."""
    if stock is not None:
        return stock > 0
    raw = unicodedata.normalize('NFKD', row.get('disponible', '')).encode('ascii', 'ignore').decode().strip().lower()
    if raw in {'', 'n/a'}:
        return None
    if raw in {'si', 's', 'yes', 'true', '1', 'disponible'}:
        return True
    if raw in {'no', 'false', '0', 'agotado'}:
        return False
    raise InventoryError('El indicador Disponible debe ser Sí o No')


def _product_description(row):
    description = _value(row, 'description', 'descripcion')
    if description:
        return description
    extra = []
    if row.get('precio_anterior'):
        extra.append(f"Precio anterior: {row['precio_anterior']}")
    if row.get('url'):
        extra.append(f"URL: {row['url']}")
    return '. '.join(extra)


def _product_values(row):
    sku = _value(row, 'sku', 'codigo', 'codigo_producto', 'id_producto')
    name = _value(row, 'name', 'nombre', 'producto', 'title')
    if not sku or not name:
        return None
    stock = _available_stock(row)
    stock_value = _integer(stock)
    values = {
        'sku': _text(unicodedata.normalize('NFKC', sku).strip(), 120, 'SKU'),
        'name': _text(name, 255, 'Nombre'),
        'category': _text(_value(row, 'category', 'categoria', 'tipo'), 120, 'Categoría'),
        'brand': _text(_value(row, 'brand', 'marca'), 120, 'Marca'),
        'model': _text(_value(row, 'model', 'modelo'), 120, 'Modelo'),
        'specs': _value(row, 'specs', 'especificaciones'),
        'price': _decimal(_value(row, 'price', 'precio')),
        'stock': stock_value,
        'available': _availability(row, stock_value),
        'unit': _text(_value(row, 'unit', 'unidad') or ('unidades' if _value(row, 'stock', 'inventario', 'existencias') else ''), 80, 'Unidad'),
        'keywords': _value(row, 'keywords', 'palabras_clave', 'palabras_clave_busqueda'),
        'description': _product_description(row),
        'active': _active(_value(row, 'active', 'activo') or '1'),
    }
    content = '|'.join(str(values[key]) for key in values)
    values['content_hash'] = hashlib.sha256(content.encode('utf-8')).hexdigest()
    return values
