from decimal import Decimal, InvalidOperation

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from applications.chat.dummyjson_api import DummyJSONError, list_products
from applications.chat.embeddings import EmbeddingError, embed_text
from applications.chat.models import ProductDocument
from applications.chat.vector_search import product_content, product_content_hash


class Command(BaseCommand):
    help = 'Sincroniza productos de DummyJSON y genera sus embeddings.'

    def handle(self, *args, **options):
        try:
            products = list_products()
        except DummyJSONError as exc:
            raise CommandError('No se pudieron descargar los productos de DummyJSON.') from exc

        created = updated = unchanged = 0
        for product in products:
            product_id = product.get('id')
            if not product_id:
                continue
            content = product_content(product)
            content_hash = product_content_hash(content)
            existing = ProductDocument.objects.filter(dummyjson_id=product_id).first()
            if existing and existing.content_hash == content_hash:
                unchanged += 1
                continue
            try:
                embedding = embed_text(content, 'retrieval_document')
            except EmbeddingError as exc:
                raise CommandError(f'No se pudo generar el embedding del producto {product_id}.') from exc
            try:
                price = Decimal(str(product['price'])) if product.get('price') is not None else None
            except (InvalidOperation, TypeError):
                price = None
            values = {
                'title': product.get('title', '')[:255],
                'description': product.get('description', ''),
                'category': product.get('category', '')[:120],
                'brand': product.get('brand', '')[:120],
                'sku': product.get('sku', '')[:120],
                'price': price,
                'stock': product.get('stock'),
                'content': content,
                'content_hash': content_hash,
                'embedding': embedding,
            }
            with transaction.atomic():
                if existing:
                    for field, value in values.items():
                        setattr(existing, field, value)
                    existing.save()
                    updated += 1
                else:
                    ProductDocument.objects.create(dummyjson_id=product_id, **values)
                    created += 1
        self.stdout.write(self.style.SUCCESS(
            f'Productos: {len(products)} | creados: {created} | actualizados: {updated} | sin cambios: {unchanged}'
        ))
