"""Datos del panel de WhatsApp: estado del sistema, contactos y mensajes en formato JSON."""
from django.conf import settings
from django.db.models import Count, Q
from django.utils import timezone

from applications.core.inventory_sync import inventory_status
from applications.core.models import AIUsageDay

from .health import worker_health
from .models import Outbox, WhatsAppContact, WhatsAppMessage

CONVERSATION_PAGE = 50
MESSAGE_LIMIT = 200
_DELIVERY_RANK = {'sent': 1, 'delivered': 2, 'read': 3}
_DELIVERY_BY_RANK = {rank: name for name, rank in _DELIVERY_RANK.items()}


def can_manage(user):
    """Superusuario, administrador ('0') o empleado ('2')."""
    return bool(user.is_authenticated and (user.is_superuser or user.occupation in {'0', '2'}))


def system_status():
    outbox_open = Outbox.objects.filter(sent=False, failed_at__isnull=True)
    day = AIUsageDay.objects.filter(day=timezone.now().date()).first()
    messages = WhatsAppMessage.objects.filter(is_from_customer=True)
    return {
        'worker': worker_health(),
        'queues': {
            'incoming_pending': messages.filter(status=WhatsAppMessage.PENDING).count(),
            'incoming_processing': messages.filter(status=WhatsAppMessage.PROCESSING).count(),
            'incoming_failed': messages.filter(status=WhatsAppMessage.FAILED).count(),
            'outbox_pending': outbox_open.count(),
            'outbox_failed': Outbox.objects.filter(failed_at__isnull=False).count(),
        },
        'catalog': inventory_status(),
        'ai': {
            'requests_today': day.requests if day else 0,
            'request_limit': settings.AI_DAILY_REQUEST_LIMIT,
            'reserved_tokens_today': day.reserved_tokens if day else 0,
            'token_limit': settings.AI_DAILY_TOKEN_LIMIT,
        },
        'human_contacts': WhatsAppContact.objects.filter(mode=WhatsAppContact.HUMAN).count(),
    }


def contact_payload(contact):
    return {
        'id': contact.id,
        'phone': contact.phone_number,
        'name': contact.display_name,
        'mode': contact.mode,
        'last_message_at': contact.last_message_at.isoformat() if contact.last_message_at else None,
        'waiting': getattr(contact, 'waiting', 0),
    }


def conversations_page(query='', offset=0):
    contacts = WhatsAppContact.objects.all()
    if query:
        contacts = contacts.filter(Q(display_name__icontains=query) | Q(phone_number__icontains=query))
    contacts = contacts.annotate(waiting=Count(
        'messages', filter=Q(messages__is_from_customer=True,
                             messages__status__in=(WhatsAppMessage.PENDING, WhatsAppMessage.PROCESSING)),
    )).order_by('-last_message_at', '-updated_at', '-id')
    page = list(contacts[offset:offset + CONVERSATION_PAGE + 1])
    return {
        'conversations': [contact_payload(contact) for contact in page[:CONVERSATION_PAGE]],
        'has_more': len(page) > CONVERSATION_PAGE,
        'next_offset': offset + CONVERSATION_PAGE if len(page) > CONVERSATION_PAGE else None,
    }


def delivery_summary(message):
    """Estado de entrega de un mensaje del bot o de un operador, a partir de las partes de su Outbox."""
    entries = list(message.outbox_entries.all())
    if not entries:
        return None
    failed = [entry for entry in entries if entry.failed_at or entry.delivery_status == 'failed']
    now = timezone.now()
    if failed:
        state, error = 'failed', failed[0].delivery_error or failed[0].last_error
    elif all(entry.sent for entry in entries):
        rank = min(_DELIVERY_RANK.get(entry.delivery_status, 1) for entry in entries)
        state, error = _DELIVERY_BY_RANK[rank], ''
    elif any(entry.sent for entry in entries):
        state, error = 'sending', ''
    elif any(entry.attempts and entry.next_attempt_at and entry.next_attempt_at > now for entry in entries):
        state, error = 'retrying', next((entry.last_error for entry in entries if entry.last_error), '')
    else:
        state, error = 'queued', ''
    return {'state': state, 'parts': len(entries), 'accepted': sum(entry.sent for entry in entries),
            'error': error[:300]}


def message_payload(message):
    if message.is_from_customer:
        sender = 'customer'
    else:
        sender = 'operator' if message.is_manual else 'bot'
    return {
        'id': message.id,
        'sender': sender,
        'text': message.text,
        'has_image': bool(message.media_id),
        'status': message.status,
        'retry_after_at': message.retry_after_at.isoformat() if message.retry_after_at else None,
        'created_at': message.created_at.isoformat(),
        'delivery': delivery_summary(message) if not message.is_from_customer else None,
    }


def conversation_payload(contact):
    messages = list(contact.messages.order_by('-created_at', '-id').prefetch_related('outbox_entries')[:MESSAGE_LIMIT])
    messages.reverse()
    return {
        'conversation': contact_payload(contact),
        'messages': [message_payload(message) for message in messages],
        # Solo envíos del bot: los mensajes del propio operador no son lo que se «enviará igual».
        'pending_outbox': contact.outbox.filter(sent=False, failed_at__isnull=True,
                                                message__is_manual=False).count(),
    }
