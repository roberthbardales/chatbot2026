import json
import logging
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


logger = logging.getLogger(__name__)
DUMMYJSON_URL = 'https://dummyjson.com/products'
PAGE_SIZE = 30


class DummyJSONError(Exception):
    pass


def _request_json(url, timeout=5):
    try:
        request = Request(url, headers={'Accept': 'application/json', 'User-Agent': 'chatbot2026/1.0'})
        with urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode('utf-8'))
    except (HTTPError, URLError, TimeoutError, ValueError, OSError) as exc:
        logger.warning('DummyJSON request failed: %s', exc)
        raise DummyJSONError from exc


def _products(data):
    if not isinstance(data, dict) or not isinstance(data.get('products'), list):
        raise DummyJSONError('Unexpected products response')
    return [product for product in data['products'] if isinstance(product, dict)]


def search_products(query, limit=6):
    data = _request_json(f'{DUMMYJSON_URL}/search?q={quote(query)}&limit={limit}')
    return _products(data)[:limit]


def category_products(category, limit=6):
    data = _request_json(f'{DUMMYJSON_URL}/category/{quote(category)}?limit={limit}')
    return _products(data)[:limit]


def product_by_id(product_id):
    data = _request_json(f'{DUMMYJSON_URL}/{quote(str(product_id))}')
    if not isinstance(data, dict) or 'id' not in data:
        raise DummyJSONError('Unexpected product response')
    return data


def product_by_sku(sku):
    normalized = sku.strip().lower()
    skip = 0
    while True:
        data = _request_json(f'{DUMMYJSON_URL}?limit={PAGE_SIZE}&skip={skip}')
        products = _products(data)
        for product in products:
            if str(product.get('sku', '')).strip().lower() == normalized:
                return product
        total = data.get('total', 0)
        skip += PAGE_SIZE
        if not products or skip >= total:
            return None
