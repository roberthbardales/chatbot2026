"""Consulta de productos en el catálogo local sincronizado."""
import re
import unicodedata
from fractions import Fraction

from .inventory_source import InventoryError  # noqa: F401  (reexportado para los comandos)
from .models import Product


def preview_inventory():
    from .inventory_sync import preview_inventory as preview
    return preview()


def sync_inventory(*, allow_large_drop=False):
    from .inventory_sync import apply_inventory
    return apply_inventory(allow_large_drop=allow_large_drop)


def _normalize(value):
    value = unicodedata.normalize('NFKD', value or '')
    value = ''.join(char for char in value if not unicodedata.combining(char))
    return re.sub(r'[^a-z0-9]+', ' ', value.lower()).strip()


_SEARCH_STOP_WORDS = set('''a al algo buscar busca busco buscarme necesito necesitamos quiero quisiera
    dame dime mostrar muestra muestrame ver tienes tienen tiene hay de del la las el los un una unos unas
    para por con y o e en este esta estos estas ese esa eso esos esas mismo misma producto productos
    precio precios cuesta cuestan cuanto cuanta cuantos cuantas costo stock existencia existencias
    unidad unidades disponible disponibles disponibilidad informacion detalle detalles especificaciones
    medida medidas su sus me puedes puede favor hola buenas gracias que cual cuales es son se si
    indicame indica confirmo correcto correcta primera primero segunda segundo tercera tercero
    opcion opciones numero sku codigo referencia marca marcas modelo modelos categoria categorias
    descripcion caracteristicas material materiales esto ahora tambien otro otra otros otras'''.split())
_SEARCH_SYNONYMS = {
    'valvulas': 'valvula', 'valve': 'valvula', 'valves': 'valvula',
    'tuberia': 'tubo', 'tuberias': 'tubo', 'tubos': 'tubo',
    'codos': 'codo', 'elbow': 'codo', 'elbows': 'codo',
    'acoples': 'acople', 'acoplamiento': 'acople', 'acoplamientos': 'acople',
    'adaptadores': 'adaptador', 'conectores': 'conector',
    'inox': 'inoxidable', 'bronze': 'bronce',
}


_CONTENT_UNITS = {
    'ml': 'ml', 'mililitro': 'ml', 'mililitros': 'ml', 'cc': 'ml',
    'g': 'g', 'gr': 'g', 'grs': 'g', 'gramo': 'g', 'gramos': 'g',
    'kg': 'kg', 'kilo': 'kg', 'kilos': 'kg', 'kilogramo': 'kg', 'kilogramos': 'kg',
    'mg': 'mg', 'miligramos': 'mg',
    'l': 'l', 'lt': 'l', 'lts': 'l', 'litro': 'l', 'litros': 'l',
    'oz': 'oz', 'onza': 'oz', 'onzas': 'oz',
}


def search_terms(value):
    """Conservative lexical aliases; preserve dimensions, units and connection types."""
    value = value.lower().replace('⁄', '/')
    for glyph, fraction in {'½': '1/2', '¼': '1/4', '¾': '3/4', '⅜': '3/8', '⅝': '5/8', '⅛': '1/8'}.items():
        value = re.sub(r'(\d)?' + glyph, lambda match: (match.group(1) + ' ' if match.group(1) else '') + fraction, value)
    value = unicodedata.normalize('NFKD', value)
    value = ''.join(char for char in value if not unicodedata.combining(char))
    for phrase, replacement in {'media pulgada': '1/2 pulgada', 'medio pulgada': '1/2 pulgada',
                                'un cuarto de pulgada': '1/4 pulgada', 'tres cuartos de pulgada': '3/4 pulgada',
                                'llave de paso': 'valvula'}.items():
        value = value.replace(phrase, replacement)

    def dimension(match):
        number = match.group('number').replace(',', '.')
        unit = match.groupdict().get('unit') or 'pulgada'
        try:
            parts = number.split()
            amount = sum((Fraction(part) for part in parts), Fraction(0))
        except (ValueError, ZeroDivisionError):
            return match.group(0)
        unit = 'mm' if unit.startswith(('mm', 'milimetro')) else 'inch'
        return f' dim_{unit}_{amount.numerator}_{amount.denominator} '

    number = r'\d+(?:\s+\d+/\d+|/\d+|[.,]\d+)?'
    value = re.sub(r'(?<![\w/])(?P<number>' + number + r')\s*(?P<unit>milimetros?\b|mm\b|pulgadas?\b|inches\b|inch\b|in\b|["″])', dimension, value)
    # A bare fraction is the usual inch notation in this industrial catalog.
    value = re.sub(r'(?<![\w/])(?P<number>\d+(?:\s+\d+/\d+|/\d+))(?![\w/])', dimension, value)
    # Net content: "500 ml", "500ml" and "500 mililitros" are the same term (also inside product names).
    value = re.sub(r'(?<![\w/])(\d+(?:[.,]\d+)?)\s*(' + '|'.join(sorted(_CONTENT_UNITS, key=len, reverse=True)) + r')\b',
                   lambda match: match.group(1) + _CONTENT_UNITS[match.group(2)], value)
    # Brands written with dotted initials ("e.l.f.") are one word.
    value = re.sub(r'(?<![a-z0-9])[a-z](?:\.[a-z])+\.?(?![a-z0-9])', lambda match: match.group(0).replace('.', ''), value)
    tokens = re.findall(r'[a-z0-9_]+', value)
    return {_SEARCH_SYNONYMS.get(token, token) for token in tokens if token not in _SEARCH_STOP_WORDS}


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
        'price': product.price,
        'stock': product.stock,
        'available': product.available,
        'unit': product.unit,
        'keywords': product.keywords,
        'description': product.description,
    }


def _score(query, product):
    query_tokens = search_terms(query)
    haystack_tokens = search_terms(' '.join((product.name, product.sku, product.brand, product.model,
                                           product.keywords, product.category, product.specs)))
    return len(query_tokens) if query_tokens and query_tokens.issubset(haystack_tokens) else 0


def search_products(query, limit=6):
    active = list(Product.objects.filter(active=True).order_by('name', 'sku'))
    references = [product for product in active if re.search(
        r'(?<![\w./-])' + re.escape(product.sku) + r'(?![\w./-])', query, re.I,
    )]
    if references:
        return [_as_dict(product) for product in references[:limit]]
    scored = [(_score(query, product), product) for product in active]
    return [_as_dict(product) for score, product in sorted(scored, key=lambda item: item[0], reverse=True) if score > 0][:limit]


def product_by_sku(sku):
    product = Product.objects.filter(sku__iexact=unicodedata.normalize('NFKC', sku).strip(), active=True).first()
    return _as_dict(product) if product else None


def category_products(category, limit=6):
    products = Product.objects.filter(active=True, category__icontains=category)[:limit]
    return [_as_dict(product) for product in products]
