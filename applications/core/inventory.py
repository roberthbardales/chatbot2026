"""Consulta de productos en el catálogo local sincronizado."""
import re
import unicodedata

from .models import Product


def normalize_text(value):
    """Minúsculas, sin tildes ni apóstrofes; solo letras y dígitos separados por espacios."""
    value = (value or '').replace("'", '').replace('’', '')
    value = unicodedata.normalize('NFKD', value)
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
# Palabra que escribe el cliente -> término del catálogo. Se aplica por igual a la consulta y a
# los textos de los productos, así que basta con que ambos lados converjan al mismo término.
_SEARCH_SYNONYMS = {
    # Protector solar
    'bloqueador': 'protector', 'sunblock': 'protector', 'sunscreen': 'protector',
    # Limpieza y cuidado facial
    'cleanser': 'limpiador', 'cleansing': 'limpiador', 'limpiadores': 'limpiador',
    'cream': 'crema', 'cremas': 'crema', 'moisturizer': 'hidratante',
    'toner': 'tonico', 'mist': 'bruma',
    'mask': 'mascarilla', 'mascarillas': 'mascarilla',
    'serums': 'serum', 'ampoule': 'ampolla', 'ampollas': 'ampolla',
    'patch': 'parches', 'patches': 'parches',
    'scrub': 'exfoliante', 'peeling': 'exfoliante',
    'eye': 'ojos', 'lip': 'labios', 'labial': 'labios', 'labiales': 'labios',
    'champu': 'shampoo',
    # Maquillaje
    'powder': 'polvo', 'blush': 'rubor', 'concealer': 'corrector', 'eyeliner': 'delineador',
    # El catálogo usa más la palabra en inglés
    'aceite': 'oil', 'espuma': 'foam', 'esencia': 'essence',
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
    """Términos de búsqueda normalizados: sin tildes, sin palabras vacías, con unidades y sinónimos unificados."""
    value = unicodedata.normalize('NFKD', value.lower())
    value = ''.join(char for char in value if not unicodedata.combining(char))
    # Contenido neto: "500 ml", "500ml" y "500 mililitros" son el mismo término (también dentro de los nombres).
    value = re.sub(r'(?<![\w/])(\d+(?:[.,]\d+)?)\s*(' + '|'.join(sorted(_CONTENT_UNITS, key=len, reverse=True)) + r')\b',
                   lambda match: match.group(1) + _CONTENT_UNITS[match.group(2)], value)
    # Marcas escritas con iniciales con puntos ("e.l.f.") son una sola palabra.
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


# Términos de búsqueda de cada producto, en memoria por proceso. La clave incluye `content_hash`,
# que cambia cuando cambia cualquier campo del producto, así que `sync_inventory` los renueva solo.
_PRODUCT_TERMS = {}


def _product_terms(product):
    cached = _PRODUCT_TERMS.get(product.pk)
    if cached and cached[0] == product.content_hash:
        return cached[1]
    terms = search_terms(' '.join((product.name, product.sku, product.brand, product.model,
                                   product.keywords, product.category, product.specs)))
    _PRODUCT_TERMS[product.pk] = (product.content_hash, terms)
    return terms


def _forget_inactive_terms(active):
    current = {product.pk for product in active}
    for pk in [pk for pk in _PRODUCT_TERMS if pk not in current]:
        _PRODUCT_TERMS.pop(pk, None)


def search_products(query, limit=6):
    active = list(Product.objects.filter(active=True).order_by('name', 'sku'))
    lowered = query.lower()
    # Primero la comparación barata de texto; la expresión regular solo para los candidatos.
    references = [product for product in active if product.sku.lower() in lowered and re.search(
        r'(?<![\w./-])' + re.escape(product.sku) + r'(?![\w./-])', query, re.I,
    )]
    if references:
        return [_as_dict(product) for product in references[:limit]]
    query_terms = search_terms(query)
    if not query_terms:
        return []
    _forget_inactive_terms(active)
    # Todos los términos de la consulta deben estar en el producto; se conserva el orden por nombre.
    matches = [product for product in active if query_terms <= _product_terms(product)]
    return [_as_dict(product) for product in matches[:limit]]


def product_by_sku(sku):
    product = Product.objects.filter(sku__iexact=unicodedata.normalize('NFKC', sku).strip(), active=True).first()
    return _as_dict(product) if product else None


def category_products(category, limit=6):
    """Productos activos cuya categoría contiene el texto, sin distinguir tildes ni mayúsculas."""
    wanted = normalize_text(category)
    if not wanted:
        return []
    products = []
    for product in Product.objects.filter(active=True).exclude(category='').order_by('name', 'sku'):
        if wanted in normalize_text(product.category):
            products.append(_as_dict(product))
            if len(products) >= limit:
                break
    return products
