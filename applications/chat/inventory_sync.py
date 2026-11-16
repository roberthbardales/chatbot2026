"""Vista previa y aplicación del catálogo sin borrar identidades estables."""
import hashlib
import json
import unicodedata
from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.core.serializers.json import DjangoJSONEncoder
from django.db import transaction
from django.db.models import Count, Q
from django.utils import timezone

from .inventory_source import InventoryError, _download_rows, _product_values
from .models import InventorySyncLock, InventorySyncRun, Product

PRODUCT_FIELDS = ('sku', 'name', 'category', 'brand', 'model', 'specs', 'price', 'stock', 'available',
                  'unit', 'keywords', 'description', 'active', 'content_hash')


def _key(value):
    return unicodedata.normalize('NFKC', value).strip().casefold()


def _json(value):
    return json.dumps(value, cls=DjangoJSONEncoder, sort_keys=True, ensure_ascii=False, separators=(',', ':'))


def _digest(value):
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _hashed(row):
    row = {key: value for key, value in row.items() if key != 'content_hash'}
    return {**row, 'content_hash': _digest(row)}


def _index(items, field, key):
    result = {}
    for item in items:
        value = item[field] if isinstance(item, dict) else getattr(item, field)
        normalized = key(value)
        if not normalized or normalized in result:
            raise InventoryError(f'Identificador vacío o duplicado tras normalizar: {field} {value}')
        result[normalized] = item
    return result


def _load_sources():
    products = _download_rows(settings.GOOGLE_SHEET_PRODUCTS_CSV_URL, required_headers=(
        ('sku', 'codigo', 'codigo_producto', 'id_producto'), ('name', 'nombre', 'producto', 'title')))
    parsed_products = []
    for index, row in enumerate(products, 2):
        if not any(row.values()):
            continue
        try:
            values = _product_values(row)
            if values is None:
                raise InventoryError('Falta SKU o nombre')
            parsed_products.append(values)
        except InventoryError as exc:
            raise InventoryError(f'Productos, fila {index}: {exc}') from exc
    if not parsed_products:
        raise InventoryError('La hoja de productos no contiene filas válidas')
    _index(parsed_products, 'sku', _key)
    return parsed_products


def _diff(rows, existing, field, key, fields):
    old = _index(existing, field, key)
    incoming = _index(rows, field, key)
    summary = {'create': [], 'update': [], 'deactivate': [], 'unchanged': 0,
               'active_before': sum(item.active for item in existing), 'active_after': sum(row['active'] for row in rows),
               'incoming': len(rows)}
    for normalized, row in incoming.items():
        previous = old.get(normalized)
        if previous is None:
            summary['create'].append(row[field])
            continue
        changes = {name: {'before': getattr(previous, name), 'after': row[name]}
                   for name in fields if getattr(previous, name) != row[name]}
        if changes:
            summary['update'].append({'key': row[field], 'changes': changes})
        else:
            summary['unchanged'] += 1
    summary['deactivate'] = [getattr(item, field) for normalized, item in old.items() if normalized not in incoming and item.active]
    # Include explicit active -> inactive changes in the drop guard, without double-counting.
    deactivated = sum(item.active and (normalized not in incoming or not incoming[normalized]['active']) for normalized, item in old.items())
    summary['deactivated_total'] = deactivated
    return summary, deactivated


@dataclass
class InventoryPlan:
    products: list
    summary: dict


def _build_plan(product_rows, products):
    old_products = _index(products, 'sku', _key)
    canonical = []
    for source in sorted(product_rows, key=lambda row: _key(row['sku'])):
        row = dict(source)
        previous = old_products.get(_key(row['sku']))
        if previous:
            row['sku'] = unicodedata.normalize('NFKC', previous.sku).strip()
        canonical.append(_hashed(row))
    product_summary, removed_products = _diff(canonical, products, 'sku', _key, PRODUCT_FIELDS)
    warnings = []
    before = product_summary['active_before']
    if before >= max(1, settings.INVENTORY_MIN_CATALOG_FOR_DROP_GUARD) and removed_products * 100 >= before * min(100, max(1, settings.INVENTORY_MAX_DEACTIVATE_PERCENT)):
        warnings.append(f'Baja masiva de productos: {removed_products}/{before} activos quedarían inactivos.')
    summary = {'products': product_summary, 'warnings': warnings,
               'requires_allow_large_drop': bool(warnings)}
    # Freeze Decimal values as exact JSON strings for display and audit, not floats.
    summary = json.loads(_json(summary))
    return InventoryPlan(canonical, summary)


def preview_inventory():
    return _build_plan(_load_sources(), list(Product.objects.all()))


def _upsert(model, rows, existing, field, key, fields):
    old = _index(existing, field, key)
    incoming_keys = set()
    for row in rows:
        normalized = key(row[field])
        incoming_keys.add(normalized)
        item = old.get(normalized)
        if item is None:
            model.objects.create(**row)
        else:
            changed = [name for name in fields if getattr(item, name) != row[name]]
            if changed:
                for name in changed:
                    setattr(item, name, row[name])
                item.save(update_fields=changed + ['updated_at'])
    for normalized, item in old.items():
        if normalized not in incoming_keys and item.active:
            item.active = False
            item.content_hash = _hashed({name: getattr(item, name) for name in fields})['content_hash']
            item.save(update_fields=('active', 'content_hash', 'updated_at'))


def apply_inventory(*, allow_large_drop=False):
    # Windows/PostgreSQL clocks may give consecutive attempts the same timestamp.
    # Allocate a monotonic audit timestamp under the same shared catalog lock;
    # UUID ordering must never decide which attempt is reported as the latest.
    with transaction.atomic():
        InventorySyncLock.objects.get_or_create(pk=1)
        InventorySyncLock.objects.select_for_update().get(pk=1)
        previous = InventorySyncRun.objects.order_by('-started_at', '-pk').first()
        started_at = timezone.now()
        if previous and started_at <= previous.started_at:
            started_at = previous.started_at + timedelta(microseconds=1)
        run = InventorySyncRun.objects.create(started_at=started_at)
    try:
        rows = _load_sources()
        with transaction.atomic():
            InventorySyncLock.objects.get_or_create(pk=1)
            InventorySyncLock.objects.select_for_update().get(pk=1)
            products = list(Product.objects.select_for_update().order_by('pk'))
            plan = _build_plan(rows, products)
            if plan.summary['requires_allow_large_drop'] and allow_large_drop is not True:
                raise InventoryError('Baja masiva bloqueada. Revisa la vista previa; requiere además --allow-large-drop.')
            _upsert(Product, plan.products, products, 'sku', _key, PRODUCT_FIELDS)
            run.status = 'applied'
            run.finished_at = max(timezone.now(), run.started_at)
            run.summary = plan.summary
            run.save(update_fields=('status', 'finished_at', 'summary'))
        return run
    except Exception as exc:
        InventorySyncRun.objects.filter(pk=run.pk).update(status='failed', finished_at=max(timezone.now(), run.started_at), error=str(exc)[:1000])
        if isinstance(exc, InventoryError):
            raise
        raise InventoryError('No se pudo aplicar el catálogo; no se confirmaron cambios parciales.') from exc


def inventory_status():
    latest = InventorySyncRun.objects.order_by('-started_at', '-pk').first()
    success = InventorySyncRun.objects.filter(status='applied').order_by('-finished_at', '-pk').first()
    age_hours = (timezone.now() - success.finished_at).total_seconds() / 3600 if success and success.finished_at else None
    return {
        'products': Product.objects.aggregate(total=Count('pk'), active=Count('pk', filter=Q(active=True))),
        'last_success_at': success.finished_at.isoformat() if success and success.finished_at else None,
        'freshness': 'unknown' if age_hours is None else 'stale' if age_hours >= max(1, settings.INVENTORY_STALE_HOURS) else 'current',
        'last_attempt': {'id': str(latest.pk), 'status': latest.status, 'started_at': latest.started_at.isoformat(),
                         'finished_at': latest.finished_at.isoformat() if latest.finished_at else None,
                         'error': latest.error} if latest else None,
    }
