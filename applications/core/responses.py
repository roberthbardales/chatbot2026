"""Formato y validación de las respuestas del catálogo: los valores salen siempre del inventario."""
import json
import random
import re
import unicodedata


class InventoryAnswer(str):
    """A text response plus trusted context, persisted only with its assistant message."""
    def __new__(cls, text, context=None):
        answer = super().__new__(cls, text)
        answer.inventory_context = context or {}
        return answer


_SHEET_FIELDS = {
    'precio': ('price', 'Precio'),
    'stock': ('stock', 'Stock'),
    'marca': ('brand', 'Marca'),
    'modelo': ('model', 'Modelo'),
    'categoria': ('category', 'Categoría'),
    'especificaciones': ('specs', 'Especificaciones'),
    'descripcion': ('description', 'Descripción'),
}


# La IA ve el inventario con claves en inglés (brand, category…) y a veces las devuelve tal cual en vez
# de los nombres en español. Se aceptan como equivalentes; el conjunto sigue siendo cerrado.
_FIELD_ALIASES = {
    'price': 'precio', 'brand': 'marca', 'model': 'modelo', 'category': 'categoria',
    'specs': 'especificaciones', 'specifications': 'especificaciones', 'description': 'descripcion',
}


def _canonical_field(field):
    if not isinstance(field, str):
        raise ValueError('Invalid inventory field')
    name = unicodedata.normalize('NFKD', field.strip().lower())
    name = ''.join(char for char in name if not unicodedata.combining(char))
    name = _FIELD_ALIASES.get(name, name)
    if name not in _SHEET_FIELDS:
        raise ValueError('Invalid inventory field')
    return name


def _sheet_selection(raw, products):
    data = json.loads(raw)
    if not isinstance(data, dict) or set(data) != {'productos'}:
        raise ValueError('Invalid inventory selection')
    entries = data['productos']
    if not isinstance(entries, list) or not entries or len(entries) > len(products):
        raise ValueError('Invalid inventory products')
    available = {product['sku']: product for product in products}
    selected = []
    seen = set()
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {'sku', 'campos'}:
            raise ValueError('Invalid inventory entry')
        sku, fields = entry['sku'], entry['campos']
        if not isinstance(sku, str) or sku not in available or sku in seen:
            raise ValueError('Unknown or duplicate SKU')
        if not isinstance(fields, list) or not fields:
            raise ValueError('Invalid inventory fields')
        fields = [_canonical_field(field) for field in fields]
        seen.add(sku)
        selected.append((available[sku], list(dict.fromkeys(fields))))
    return selected


_OPENERS = ('¡Claro! ', '¡Mira! ', 'Listo 😉 ', '¡Va! ', '')
_CLOSERS = (
    '¿Te ayudo con algo más? 😊',
    '¿Buscas otra cosa? 🙌',
    'Si quieres, te busco otro producto 😉',
    'Cualquier otra cosa, me dices 👍',
)
_CLOSER_PROBABILITY = 0.4

# Cada campo se redacta de forma natural; el valor siempre sale del inventario.
_FIELD_PHRASES = {
    'precio': ('está a {value}', 'no tengo el precio en el inventario'),
    'stock': ('hay {value}', 'no tengo el stock en el inventario'),
    'marca': ('es de la marca {value}', 'no tengo la marca en el inventario'),
    'modelo': ('es el modelo {value}', 'no tengo el modelo en el inventario'),
    'categoria': ('es de la categoría {value}', 'no tengo la categoría en el inventario'),
    'especificaciones': ('sus especificaciones: {value}', 'no tengo las especificaciones en el inventario'),
    'descripcion': ('su descripción: {value}', 'no tengo la descripción en el inventario'),
}


# Identificadores internos (hash de 32+ caracteres hexadecimales): el cliente no puede
# usarlos, así que no se muestran. Los SKU legibles (p. ej. VAL-01) se siguen mostrando.
_MACHINE_ID = re.compile(r'[0-9a-fA-F]{32,}')


def display_sku(sku):
    sku = str(sku or '')
    return '' if _MACHINE_ID.fullmatch(sku) else sku


def _join_natural(parts):
    if len(parts) <= 1:
        return ''.join(parts)
    return ', '.join(parts[:-1]) + ' y ' + parts[-1]


def _render_sheet_selection(selection, from_image):
    lines = ['Estas son las posibles coincidencias; confírmame cuál es o el tamaño 👇'] if from_image else []
    for index, (product, fields) in enumerate(selection):
        parts = []
        for field in fields:
            key, _label = _SHEET_FIELDS[field]
            present, missing = _FIELD_PHRASES[field]
            value = product.get(key)
            if value is None or value == '':
                if field == 'stock' and product.get('available') is True:
                    # La hoja dice «Disponible» pero no trae cantidad: no se inventa un número.
                    parts.append('está disponible; consulta la cantidad exacta')
                elif field == 'stock' and product.get('available') is False:
                    parts.append('está agotado')
                else:
                    parts.append(missing)
                continue
            if field == 'stock' and value == 0:
                parts.append('está agotado')
                continue
            if field == 'stock' and product.get('unit'):
                unit = product['unit']
                if value == 1 and unit.lower() == 'unidades':
                    unit = 'unidad'
                value = f'{value} {unit}'
            parts.append(present.format(value=value))
        opener = random.choice(_OPENERS) if index == 0 and not from_image else ''
        sku = display_sku(product['sku'])
        label = f"{product['name']} (SKU {sku})" if sku else product['name']
        lines.append(f"{opener}{label}: {_join_natural(parts)}.")
    if random.random() < _CLOSER_PROBABILITY:
        lines.append(random.choice(_CLOSERS))
    return '\n'.join(lines)
