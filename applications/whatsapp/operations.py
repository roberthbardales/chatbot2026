from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import Avg, Count, DurationField, ExpressionWrapper, F, Min, Q
from django.utils import timezone

from applications.chat.models import AIUsageDay, UsageLease
from applications.chat.inventory_sync import inventory_status
from .health import worker_health
from .models import Outbox, OutboxRetryAudit, WhatsAppMessage
from .services import _outbox_eligible


class RetryNotAllowed(Exception):
    pass


def retry_block_reason(target, parts, now=None):
    now = now or timezone.now()
    if target.sent or target.meta_message_id or target.delivery_status or target.provider_sent_at or target.delivered_at or target.read_at or target.delivery_failed_at:
        return 'Esta parte ya fue aceptada por Meta o tiene confirmaciones remotas. No se puede reactivar.'
    if target.failed_at is None:
        return 'La parte no está marcada como fallo definitivo.'
    cutoff = now - timedelta(seconds=max(1, settings.WHATSAPP_OUTBOX_LOCK_SECONDS))
    if any(part.locked_at and part.locked_at > cutoff for part in parts):
        return 'Hay un intento activo en este grupo. Espera a que termine.'
    if any(part.delivery_status == 'failed' for part in parts):
        return 'El grupo tiene un fallo de entrega de Meta. Revisa la causa antes de preparar un mensaje nuevo.'
    if any(not part.sent and part.failed_at is not None and (
        part.meta_message_id or part.delivery_status or part.provider_sent_at or part.delivered_at or part.read_at or part.delivery_failed_at
    ) for part in parts):
        return 'Hay confirmaciones remotas en partes no resueltas del grupo; se requiere revisión manual.'
    if any(not part.sent and part.part_number < target.part_number for part in parts):
        return 'Resuelve o reintenta primero la parte anterior que no fue aceptada.'
    cooldown = max(0, settings.WHATSAPP_MANUAL_RETRY_COOLDOWN_SECONDS)
    if target.last_retry_at and target.last_retry_at + timedelta(seconds=cooldown) > now:
        return 'Espera antes de volver a reactivar esta parte.'
    return ''


def retry_failed_group(target_pk, actor, reason, confirmed=False):
    if not actor.is_authenticated or not actor.is_active or not (actor.is_superuser or actor.occupation in {'0', '2'}):
        raise RetryNotAllowed('No tienes permiso para reactivar envíos.')
    if confirmed is not True or not isinstance(reason, str) or not 5 <= len(reason.strip()) <= 500:
        raise RetryNotAllowed('Confirma el riesgo e indica un motivo de entre 5 y 500 caracteres.')
    reason = reason.strip()
    with transaction.atomic():
        target = Outbox.objects.filter(pk=target_pk).first()
        if target is None:
            raise Outbox.DoesNotExist
        parts = list(Outbox.objects.select_for_update().filter(group_id=target.group_id).order_by('pk'))
        target = next((part for part in parts if part.pk == target_pk), None)
        if target is None:
            raise Outbox.DoesNotExist
        now = timezone.now()
        blocked = retry_block_reason(target, parts, now)
        if blocked:
            raise RetryNotAllowed(blocked)
        retryable = [part for part in parts if not part.sent and part.failed_at is not None and not part.meta_message_id]
        if any(part.delivery_status or part.provider_sent_at or part.delivered_at or part.read_at or part.delivery_failed_at for part in retryable):
            raise RetryNotAllowed('Hay confirmaciones remotas en el grupo; se requiere revisión manual.')
        audit = OutboxRetryAudit.objects.create(
            actor=actor, actor_identifier=str(actor.pk), outbox=target, group_id=target.group_id, reason=reason,
            snapshot=[{
                'outbox_id': part.pk, 'part_number': part.part_number, 'attempts': part.attempts,
                'last_error': part.last_error, 'failed_at': part.failed_at.isoformat(),
                'manual_retries': part.manual_retries,
            } for part in retryable],
        )
        Outbox.objects.filter(pk__in=[part.pk for part in retryable]).update(
            failed_at=None, attempts=0, last_error='', locked_at=None, lock_token=None,
            next_attempt_at=None, last_retry_at=now, manual_retries=F('manual_retries') + 1,
        )
    return audit


def _seconds(value):
    return round(max(0, value.total_seconds()), 1) if value is not None else None


def operations_snapshot():
    now = timezone.now()
    since = now - timedelta(hours=24)
    health = worker_health()
    inventory = inventory_status()
    incoming = WhatsAppMessage.objects.filter(is_from_customer=True)
    pending = incoming.filter(status=WhatsAppMessage.PENDING)
    incoming_due = pending.filter(created_at__lte=now - timedelta(seconds=max(0, settings.MESSAGE_DEBOUNCE_SECONDS))).filter(
        Q(retry_after_at__isnull=True) | Q(retry_after_at__lte=now))
    outgoing = Outbox.objects.all()
    outgoing_due = _outbox_eligible(now, max(1, settings.WHATSAPP_OUTBOX_MAX_ATTEMPTS))
    inbound = incoming.aggregate(
        pending=Count('pk', filter=Q(status=WhatsAppMessage.PENDING)),
        processing=Count('pk', filter=Q(status=WhatsAppMessage.PROCESSING)),
        failed_24h=Count('pk', filter=Q(status=WhatsAppMessage.FAILED, processed_at__gte=since)),
        processed_24h=Count('pk', filter=Q(status=WhatsAppMessage.PROCESSED, processed_at__gte=since)),
    )
    outbound = outgoing.aggregate(
        pending=Count('pk', filter=Q(sent=False, failed_at__isnull=True)),
        accepted_24h=Count('pk', filter=Q(sent=True, sent_at__gte=since)),
        local_failed=Count('pk', filter=Q(sent=False, failed_at__isnull=False)),
        delivery_failed=Count('pk', filter=Q(delivery_status='failed')),
        delivered_24h=Count('pk', filter=Q(delivered_at__gte=since) | Q(read_at__gte=since)),
        read_24h=Count('pk', filter=Q(read_at__gte=since)),
        configuration_failed=Count('pk', filter=Q(sent=False, failed_at__isnull=False, last_error__startswith='[configuration ')),
    )
    inbound['due'] = incoming_due.count()
    outbound['due'] = outgoing_due.count()
    inbound_oldest = incoming_due.aggregate(oldest=Min('created_at'))['oldest']
    outbound_oldest = outgoing_due.aggregate(oldest=Min('created_at'))['oldest']
    inbound['oldest_due_seconds'] = _seconds(now - inbound_oldest) if inbound_oldest else None
    outbound['oldest_due_seconds'] = _seconds(now - outbound_oldest) if outbound_oldest else None
    processing_latency = incoming.filter(status=WhatsAppMessage.PROCESSED, processed_at__gte=since).aggregate(
        mean=Avg(ExpressionWrapper(F('processed_at') - F('created_at'), output_field=DurationField())))['mean']
    send_latency = outgoing.filter(sent=True, sent_at__gte=since).aggregate(
        mean=Avg(ExpressionWrapper(F('sent_at') - F('created_at'), output_field=DurationField())))['mean']
    usage = AIUsageDay.objects.filter(day=now.date()).first()
    ai = {
        'day_utc': now.date().isoformat(), 'requests': usage.requests if usage else 0,
        'reserved_tokens': usage.reserved_tokens if usage else 0,
        'request_limit': settings.AI_DAILY_REQUEST_LIMIT, 'token_limit': settings.AI_DAILY_TOKEN_LIMIT,
        'active_calls': UsageLease.objects.filter(scope='ai:global', expires_at__gt=now).count(),
    }
    alerts = []
    def alert(code, severity, message):
        alerts.append({'code': code, 'severity': severity, 'message': message})
    if not health['healthy']:
        alert('worker_unhealthy', 'critical', 'El worker no está saludable: ' + health['state'])
    if inventory['products']['active'] == 0:
        alert('inventory_empty', 'warning', 'No hay productos activos en el catálogo.')
    elif inventory['freshness'] != 'current':
        alert('inventory_stale', 'warning', 'El catálogo no tiene sincronización reciente confirmada: ' + inventory['freshness'])
    if inventory['last_attempt'] and inventory['last_attempt']['status'] == 'failed':
        alert('inventory_sync_failed', 'warning', 'Falló el último intento de sincronización; revisa su estado sin reemplazar el catálogo como diagnóstico.')
    if not settings.WHATSAPP_ACCESS_TOKEN or not settings.WHATSAPP_PHONE_NUMBER_ID or not settings.WHATSAPP_APP_SECRET:
        alert('credentials_missing', 'critical', 'Faltan credenciales de envío o el secreto del webhook de Meta.')
    if outbound['configuration_failed']:
        alert('configuration_failures', 'critical', 'Hay envíos fallidos por credenciales, permisos o configuración. Revisa la causa antes de reintentar.')
    for name, queue in [('incoming', inbound), ('outgoing', outbound)]:
        if queue['due'] >= max(1, settings.WHATSAPP_QUEUE_ALERT_COUNT) or (
            queue['oldest_due_seconds'] is not None and queue['oldest_due_seconds'] >= max(1, settings.WHATSAPP_QUEUE_ALERT_SECONDS)
        ):
            alert(name + '_backlog', 'warning', 'Hay cola elegible acumulada: ' + name)
    if inbound['failed_24h'] or outbound['local_failed'] or outbound['delivery_failed']:
        alert('message_failures', 'warning', 'Hay fallos de procesamiento o envío que requieren revisión.')
    threshold = min(100, max(1, settings.AI_BUDGET_ALERT_PERCENT)) / 100
    if any(limit > 0 and value >= limit * threshold for value, limit in [
        (ai['requests'], ai['request_limit']), (ai['reserved_tokens'], ai['token_limit']),
    ]):
        alert('ai_budget', 'warning', 'El consumo reservado de IA está próximo al cupo o lo ha alcanzado; no representa facturación real.')
    audits = OutboxRetryAudit.objects.order_by('-created_at', '-pk')[:10]
    return {
        'generated_at': now.isoformat(), 'worker': health, 'incoming': inbound, 'outgoing': outbound, 'ai': ai, 'inventory': inventory,
        'metrics_24h': {'average_processing_seconds': _seconds(processing_latency), 'average_acceptance_seconds': _seconds(send_latency)},
        'alerts': alerts,
        'recent_retries': [{'id': audit.pk, 'actor_id': audit.actor_identifier, 'created_at': audit.created_at.isoformat(),
                            'reason': audit.reason, 'outbox_id': audit.outbox_id, 'parts': len(audit.snapshot)} for audit in audits],
    }
