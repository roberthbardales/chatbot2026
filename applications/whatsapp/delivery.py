"""Authenticated status events, including those arriving before send persistence."""
import hashlib
import json
from datetime import datetime, timedelta, timezone as datetime_timezone

from django.conf import settings
from django.db import transaction
from django.db.models import Exists, OuterRef, Q, Value
from django.db.models.functions import Concat
from django.utils import timezone

from .models import DeliveryEvent, Outbox


def record_delivery_status(status, phone_number_id):
    if not phone_number_id or phone_number_id != settings.WHATSAPP_PHONE_NUMBER_ID or not isinstance(status, dict):
        return False
    message_id = status.get('id')
    recipient = status.get('recipient_id')
    state = status.get('status')
    if not isinstance(state, str) or state not in {'sent', 'delivered', 'read', 'failed'}:
        return False
    if not isinstance(message_id, str) or not 1 <= len(message_id) <= 255:
        return False
    if not isinstance(recipient, str) or not 1 <= len(recipient) <= 32:
        return False
    try:
        stamp = status.get('timestamp')
        if not isinstance(stamp, (str, int)) or isinstance(stamp, bool):
            return False
        occurred = datetime.fromtimestamp(int(stamp), tz=datetime_timezone.utc)
        if occurred.year < 2000 or occurred > timezone.now() + timedelta(days=1):
            return False
    except (ValueError, OverflowError, OSError, TypeError):
        return False
    error = ''
    errors = status.get('errors')
    if state == 'failed' and isinstance(errors, list) and errors and isinstance(errors[0], dict):
        entry = errors[0]
        data = entry.get('error_data')
        detail = data.get('details') if isinstance(data, dict) else None
        error = f"Meta code={entry.get('code', 'unknown')}: {detail or entry.get('message') or entry.get('title') or 'Delivery failed'}"[:1000]
    if state == 'failed' and not error:
        error = 'Meta reported delivery failure'
    fingerprint = hashlib.sha256(json.dumps(
        [message_id, recipient, phone_number_id, state, occurred.isoformat()],
        ensure_ascii=False,
    ).encode()).hexdigest()
    with transaction.atomic():
        event, created = DeliveryEvent.objects.get_or_create(fingerprint=fingerprint, defaults={
            'meta_message_id': message_id, 'recipient_id': recipient,
            'phone_number_id': phone_number_id, 'status': state,
            'occurred_at': occurred, 'error': error,
        })
        item = Outbox.objects.filter(meta_message_id=message_id).first()
        if item:
            apply_delivery_events(item.pk)
    return created


def apply_delivery_events(outbox_pk):
    with transaction.atomic():
        item = Outbox.objects.select_for_update().filter(pk=outbox_pk).first()
        if item is None or not item.meta_message_id:
            return
        events = list(DeliveryEvent.objects.filter(
            meta_message_id=item.meta_message_id, recipient_id=item.contact.phone_number.lstrip('+'),
            phone_number_id=settings.WHATSAPP_PHONE_NUMBER_ID, applied_at__isnull=True,
        ).order_by('occurred_at', 'pk'))
        if not events:
            return
        for event in events:
            field = {'sent': 'provider_sent_at', 'delivered': 'delivered_at',
                     'read': 'read_at', 'failed': 'delivery_failed_at'}[event.status]
            previous = getattr(item, field)
            if event.status == 'failed':
                if previous is None or event.occurred_at >= previous:
                    item.delivery_failed_at = event.occurred_at
                    item.delivery_error = event.error
            elif previous is None or event.occurred_at < previous:
                setattr(item, field, event.occurred_at)
        # Successful delivery/read evidence wins over contradictory failures.
        # A sent/accepted callback cannot erase a known delivery failure.
        item.delivery_status = ('read' if item.read_at else 'delivered' if item.delivered_at else
                                'failed' if item.delivery_failed_at else 'sent' if item.provider_sent_at else 'accepted')
        item.save(update_fields=('delivery_status', 'provider_sent_at', 'delivered_at', 'read_at',
                                 'delivery_failed_at', 'delivery_error'))
        DeliveryEvent.objects.filter(pk__in=[event.pk for event in events]).update(outbox=item, applied_at=timezone.now())


def reconcile_delivery_events(limit=200):
    # Bounded reconciliation closes the callback-before-send-commit race.
    matching = Outbox.objects.filter(meta_message_id=OuterRef('meta_message_id')).filter(
        Q(contact__phone_number=OuterRef('recipient_id')) |
        Q(contact__phone_number=Concat(Value('+'), OuterRef('recipient_id')))
    )
    pending_ids = list(DeliveryEvent.objects.annotate(matched=Exists(matching)).filter(
        applied_at__isnull=True, phone_number_id=settings.WHATSAPP_PHONE_NUMBER_ID, matched=True,
    ).order_by('meta_message_id').values_list('meta_message_id', flat=True).distinct()[:limit])
    for pk in Outbox.objects.filter(meta_message_id__in=pending_ids).values_list('pk', flat=True):
        apply_delivery_events(pk)
