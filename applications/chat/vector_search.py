import hashlib

from django.conf import settings
from pgvector.django import CosineDistance

from .embeddings import embed_text
from .models import ProductDocument


def product_content(product):
    tags = product.get('tags') or []
    return '\n'.join([
        f"Producto: {product.get('title', '')}",
        f"Descripción: {product.get('description', '')}",
        f"Categoría: {product.get('category', '')}",
        f"Marca: {product.get('brand', '')}",
        f"Etiquetas: {', '.join(str(tag) for tag in tags)}",
        f"Garantía: {product.get('warrantyInformation', '')}",
        f"Envío: {product.get('shippingInformation', '')}",
    ]).strip()


def product_content_hash(content):
    return hashlib.sha256(content.encode('utf-8')).hexdigest()


def search_similar_products(query, limit=6):
    query_embedding = embed_text(query, 'retrieval_query')
    documents = ProductDocument.objects.annotate(
        distance=CosineDistance('embedding', query_embedding),
    ).order_by('distance')[:limit]
    return [
        (document, max(0.0, 1.0 - float(document.distance)))
        for document in documents
        if document.distance is not None and 1.0 - float(document.distance) >= settings.VECTOR_SEARCH_THRESHOLD
    ]
