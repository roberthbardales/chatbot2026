import hashlib
import hmac
import json
import logging
import uuid
import base64
import binascii
import io
import warnings
from json import JSONDecodeError

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.contrib.auth import REDIRECT_FIELD_NAME
from django.contrib.auth.decorators import user_passes_test
from django.db import transaction
from django.db.models import Q
from django.core import signing
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.urls import reverse
from PIL import Image, UnidentifiedImageError
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django.views.decorators.cache import never_cache

from .models import Outbox, WhatsAppContact, WhatsAppMessage
from .operations import RetryNotAllowed, operations_snapshot, retry_block_reason, retry_failed_group
from .services import enqueue_message, enqueue_outbox
from .health import worker_health
from .delivery import record_delivery_status
from .meta_api import WhatsAppAPIError, download_media_data


logger = logging.getLogger(__name__)


def _can_manage_whatsapp(user):
    return user.is_superuser or user.occupation in {'0', '2'}


whatsapp_manager_required = user_passes_test(
    _can_manage_whatsapp,
    login_url=None,
    redirect_field_name=REDIRECT_FIELD_NAME,
)


def _valid_signature(request):
    signature = request.headers.get('X-Hub-Signature-256', '')
    if not signature.startswith('sha256=') or not settings.WHATSAPP_APP_SECRET:
        return False
    expected = hmac.new(
        settings.WHATSAPP_APP_SECRET.encode('utf-8'),
        request.body,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(signature[7:], expected)


@csrf_exempt
@require_http_methods(['GET', 'POST'])
def webhook(request):
    if request.method == 'GET':
        expected = settings.WHATSAPP_VERIFY_TOKEN
        # An unset token must never verify, not even against an empty parameter.
        if (
            expected
            and request.GET.get('hub.mode') == 'subscribe'
            and hmac.compare_digest(request.GET.get('hub.verify_token', '').encode('utf-8'), expected.encode('utf-8'))
        ):
            return HttpResponse(request.GET.get('hub.challenge', ''), content_type='text/plain')
        return HttpResponse('Forbidden', status=403)

    if not _valid_signature(request):
        return JsonResponse({'error': 'Invalid signature'}, status=403)
    try:
        payload = json.loads(request.body.decode('utf-8'))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return JsonResponse({'error': 'Invalid JSON'}, status=400)
    if not isinstance(payload, dict) or not isinstance(payload.get('entry', []), list):
        return JsonResponse({'error': 'Invalid payload'}, status=400)

    try:
        for entry in payload.get('entry', []):
            for change in entry.get('changes', []):
                value = change.get('value', {})
                # Meta usa datos ficticios al probar manualmente el campo messages.
                metadata = value.get('metadata', {})
                if (
                    metadata.get('phone_number_id')
                    and metadata.get('phone_number_id') != settings.WHATSAPP_PHONE_NUMBER_ID
                ):
                    continue
                for status in value.get('statuses', []):
                    record_delivery_status(status, metadata.get('phone_number_id'))
                contacts = {
                    item.get('wa_id'): item.get('profile', {}).get('name', '')
                    for item in value.get('contacts', [])
                }
                for message in value.get('messages', []):
                    message_type = message.get('type')
                    if message_type not in {'text', 'image'}:
                        continue
                    phone_number = message.get('from')
                    message_id = message.get('id')
                    text = (message.get('text') or {}).get('body', '').strip()
                    image = message.get('image') or {}
                    if message_type == 'image':
                        text = (image.get('caption') or '').strip()
                    media_id = image.get('id', '')
                    media_type = image.get('mime_type', '')
                    if not phone_number or not message_id or not text and not media_id:
                        continue
                    enqueue_message(
                        phone_number,
                        contacts.get(phone_number, ''),
                        message_id,
                        text,
                        media_id,
                        media_type,
                    )
    except Exception:
        logger.exception('WhatsApp message queueing failed')
        return JsonResponse({'error': 'Message queueing failed'}, status=500)
    return JsonResponse({'status': 'ok'})


@login_required
@whatsapp_manager_required
@never_cache
def dashboard(request):
    return render(request, 'whatsapp/dashboard.html')


@login_required
@whatsapp_manager_required
@never_cache
def connection_status(request):
    return JsonResponse({
        'provider': 'meta_cloud_api',
        'configured': bool(settings.WHATSAPP_ACCESS_TOKEN and settings.WHATSAPP_PHONE_NUMBER_ID),
        'qr': None,
        'note': 'Meta Cloud API no utiliza QR local.',
        'worker': worker_health(),
    })


@login_required
@whatsapp_manager_required
@never_cache
@require_http_methods(['GET'])
def operations_summary(request):
    return JsonResponse(operations_snapshot())


@login_required
@whatsapp_manager_required
@never_cache
@require_http_methods(['GET'])
def outbox_failures(request):
    try:
        offset = int(request.GET.get('offset', '0'))
        if not 0 <= offset <= 1_000_000:
            raise ValueError
    except ValueError:
        return JsonResponse({'error': 'Paginación inválida.'}, status=400)
    page = list(Outbox.objects.filter(Q(sent=False, failed_at__isnull=False) | Q(delivery_status='failed'))
                .select_related('contact').order_by('-created_at', '-pk')[offset:offset + 51])
    groups = {}
    for part in Outbox.objects.filter(group_id__in={item.group_id for item in page[:50]}).order_by('part_number'):
        groups.setdefault(part.group_id, []).append(part)
    failures = []
    for item in page[:50]:
        blocked = retry_block_reason(item, groups[item.group_id])
        failures.append({
            'id': item.pk, 'contact_id': item.contact_id, 'phone': item.contact.phone_number,
            'name': item.contact.display_name, 'content_preview': item.content[:160],
            'mode': item.contact.mode,
            'part_number': item.part_number, 'group_id': str(item.group_id), 'attempts': item.attempts,
            'manual_retries': item.manual_retries,
            'last_retry_at': item.last_retry_at.isoformat() if item.last_retry_at else None,
            'error': item.delivery_error if item.delivery_status == 'failed' else item.last_error,
            'kind': 'delivery' if item.delivery_status == 'failed' else 'local',
            'can_retry': not blocked, 'retry_blocked_reason': blocked,
        })
    return JsonResponse({'failures': failures, 'has_more': len(page) > 50,
                         'next_offset': offset + 50 if len(page) > 50 else None})


@login_required
@whatsapp_manager_required
@never_cache
@require_http_methods(['POST'])
def retry_outbox(request, outbox_id):
    try:
        data = json.loads(request.body.decode('utf-8'))
    except (UnicodeDecodeError, JSONDecodeError):
        return JsonResponse({'error': 'JSON inválido.'}, status=400)
    if not isinstance(data, dict) or data.get('confirm_risk') is not True or not isinstance(data.get('reason'), str) or not 5 <= len(data['reason'].strip()) <= 500:
        return JsonResponse({'error': 'Confirma el riesgo e indica un motivo de entre 5 y 500 caracteres.'}, status=400)
    try:
        audit = retry_failed_group(outbox_id, request.user, data['reason'], confirmed=True)
    except Outbox.DoesNotExist:
        return JsonResponse({'error': 'El envío no existe.'}, status=404)
    except RetryNotAllowed as exc:
        return JsonResponse({'error': str(exc)}, status=409)
    return JsonResponse({'queued': True, 'audit_id': audit.pk, 'parts': len(audit.snapshot)})


@login_required
@whatsapp_manager_required
@never_cache
def conversations(request):
    try:
        offset = int(request.GET.get('offset', '0'))
        if not 0 <= offset <= 1_000_000:
            raise ValueError
    except ValueError:
        return JsonResponse({'error': 'Paginación inválida.'}, status=400)
    query = request.GET.get('q', '').strip()[:120]
    contacts = WhatsAppContact.objects.all()
    if query:
        contacts = contacts.filter(Q(display_name__icontains=query) | Q(phone_number__icontains=query))
    page = list(contacts.order_by('-last_message_at', '-updated_at', '-id')[offset:offset + 101])
    return JsonResponse({'conversations': [
        {
            'id': contact.id,
            'phone': contact.phone_number,
            'name': contact.display_name,
            'mode': contact.mode,
            'last_message_at': contact.last_message_at.isoformat() if contact.last_message_at else None,
        }
        for contact in page[:100]
    ], 'has_more': len(page) > 100, 'next_offset': offset + 100 if len(page) > 100 else None})


@login_required
@whatsapp_manager_required
@never_cache
def conversation_messages(request, contact_id):
    contact = get_object_or_404(WhatsAppContact, pk=contact_id)
    messages = contact.messages.all()
    try:
        limit = int(request.GET.get('limit', '500'))
        if not 1 <= limit <= 500:
            raise ValueError
        if request.GET.get('before'):
            cursor = signing.loads(request.GET['before'], salt='whatsapp-history')
            if not isinstance(cursor, dict) or cursor['contact'] != contact.pk:
                raise ValueError
            date = parse_datetime(cursor['date'])
            if date is None or timezone.is_naive(date) or type(cursor['id']) is not int:
                raise ValueError
            messages = messages.filter(Q(created_at__lt=date) | Q(created_at=date, id__lt=cursor['id']))
    except (ValueError, TypeError, KeyError, signing.BadSignature):
        return JsonResponse({'error': 'Paginación inválida.'}, status=400)
    page = list(messages.order_by('-created_at', '-id').prefetch_related('outbox_entries')[:limit + 1])
    latest_messages = page[:limit]
    has_more = len(page) > limit
    oldest = latest_messages[-1] if latest_messages else None
    before = signing.dumps({'contact': contact.pk, 'date': oldest.created_at.isoformat(), 'id': oldest.pk},
                           salt='whatsapp-history') if has_more else None
    return JsonResponse({
        'conversation': {
            'id': contact.id,
            'phone': contact.phone_number,
            'name': contact.display_name,
            'mode': contact.mode,
        },
        'messages': [
            {
                'id': message.id,
                'role': 'user' if message.is_from_customer else 'assistant',
                'content': message.text,
                'media_type': message.media_type,
                'status': message.status,
                'retry_after_at': message.retry_after_at.isoformat() if message.retry_after_at else None,
                'created_at': message.created_at.isoformat(),
                'sending': _sending_progress(message),
                'delivery_parts': _delivery_parts(message),
                'media_url': reverse('message-media', args=[contact.pk, message.pk]) if message.media_id else None,
            }
            for message in reversed(latest_messages)
        ],
        'has_more': has_more,
        'next_before': before,
    })


@login_required
@whatsapp_manager_required
@require_http_methods(['GET'])
def message_media(request, contact_id, message_pk):
    message = get_object_or_404(WhatsAppMessage, pk=message_pk, contact_id=contact_id)
    if not message.media_id:
        return HttpResponse(status=404)
    try:
        data_url = download_media_data(message.media_id)
        header, encoded = data_url.split(',', 1)
        if header not in {'data:image/jpeg;base64', 'data:image/png;base64', 'data:image/webp;base64'}:
            raise ValueError('Unsupported image')
        if len(encoded) > 4 * ((settings.WHATSAPP_MAX_IMAGE_BYTES + 2) // 3):
            raise ValueError('Image too large')
        raw = base64.b64decode(encoded, validate=True)
        if len(raw) > settings.WHATSAPP_MAX_IMAGE_BYTES:
            raise ValueError('Image too large')
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(raw)) as image:
                if image.format not in {'JPEG', 'PNG', 'WEBP'} or image.width * image.height > 20_000_000:
                    raise ValueError('Unsupported image')
                image.thumbnail((2000, 2000))
                output = io.BytesIO()
                image.convert('RGB').save(output, format='JPEG', quality=85)
        response = HttpResponse(output.getvalue(), content_type='image/jpeg')
        response['Content-Disposition'] = 'inline; filename="imagen.jpg"'
        response['X-Content-Type-Options'] = 'nosniff'
        response['Content-Security-Policy'] = "default-src 'none'; sandbox"
    except (WhatsAppAPIError, ValueError, binascii.Error, OSError, UnidentifiedImageError,
            Image.DecompressionBombError, Image.DecompressionBombWarning):
        response = JsonResponse({'error': 'No se pudo cargar la imagen. Puede haber caducado en Meta.'}, status=502)
    response['Cache-Control'] = 'private, no-store'
    return response


@login_required
@whatsapp_manager_required
@require_http_methods(['DELETE'])
def delete_conversation(request, contact_id):
    try:
        data = json.loads(request.body.decode('utf-8') or '{}')
    except (UnicodeDecodeError, JSONDecodeError):
        return JsonResponse({'error': 'JSON inválido.'}, status=400)
    if not isinstance(data, dict) or data.get('confirm') is not True:
        return JsonResponse({'error': 'Confirma el borrado enviando {"confirm": true}. Es irreversible.'}, status=400)
    with transaction.atomic():
        contact = get_object_or_404(WhatsAppContact.objects.select_for_update(), pk=contact_id)
        # Deleting cascades to the queues: never discard work the worker still owns.
        busy = WhatsAppMessage.objects.filter(
            contact=contact, is_from_customer=True,
            status__in=(WhatsAppMessage.PENDING, WhatsAppMessage.PROCESSING),
        ).exists() or Outbox.objects.filter(contact=contact, sent=False, failed_at__isnull=True).exists()
        if busy:
            return JsonResponse({'error': 'La conversación tiene mensajes pendientes de procesar o enviar. '
                                          'Espera a que terminen o resuélvelos antes de borrar.'}, status=409)
        phone, message_count = contact.phone_number, contact.messages.count()
        contact.delete()
    logger.warning('WhatsApp conversation deleted: actor_id=%s contact_id=%s phone=%s messages=%s',
                   request.user.pk, contact_id, f'***{phone[-4:]}' if len(phone) > 4 else '***', message_count)
    return JsonResponse({'deleted': True})


@login_required
@whatsapp_manager_required
@require_http_methods(['POST'])
def change_mode(request, contact_id):
    contact = get_object_or_404(WhatsAppContact, pk=contact_id)
    try:
        data = json.loads(request.body.decode('utf-8') or '{}')
    except (UnicodeDecodeError, JSONDecodeError):
        return JsonResponse({'error': 'JSON inválido.'}, status=400)
    mode = data.get('mode') if isinstance(data, dict) else None
    if not isinstance(mode, str) or mode not in {WhatsAppContact.AI, WhatsAppContact.HUMAN}:
        return JsonResponse({'error': 'Modo inválido.'}, status=400)
    contact.mode = mode
    contact.save(update_fields=('mode', 'updated_at'))
    return JsonResponse({'mode': contact.mode})


@login_required
@whatsapp_manager_required
@require_http_methods(['POST'])
def send_manual_message(request, contact_id):
    contact = get_object_or_404(WhatsAppContact, pk=contact_id)
    try:
        data = json.loads(request.body.decode('utf-8') or '{}')
    except (UnicodeDecodeError, JSONDecodeError):
        return JsonResponse({'error': 'JSON inválido.'}, status=400)
    text = data.get('content') if isinstance(data, dict) else None
    if not isinstance(text, str):
        return JsonResponse({'error': 'El contenido debe ser texto.'}, status=400)
    text = text.strip()
    if not text:
        return JsonResponse({'error': 'El contenido es obligatorio.'}, status=400)
    if len(text) > 50000:
        return JsonResponse({'error': 'El contenido no puede superar 50000 caracteres.'}, status=400)
    with transaction.atomic():
        message = WhatsAppMessage.objects.create(
            contact=contact,
            message_id=f'local-{uuid.uuid4()}',
            text=text,
            response=text,
            status=WhatsAppMessage.PROCESSED,
            is_from_customer=False,
        )
        enqueue_outbox(contact, message, text)
        contact.last_message_at = timezone.now()
        contact.save(update_fields=('last_message_at', 'updated_at'))
    return JsonResponse({'queued': True, 'message_id': message.id})


def _sending_progress(message):
    entries = list(message.outbox_entries.all())
    if not entries:
        return None
    return {
        'total_parts': len(entries),
        'accepted_parts': sum(item.sent for item in entries),
        'failed_parts': sum(item.failed_at is not None or item.delivery_status == 'failed' for item in entries),
        'sent_parts': sum(item.delivery_status in {'sent', 'delivered', 'read'} for item in entries),
        'delivered_parts': sum(item.delivery_status in {'delivered', 'read'} for item in entries),
        'read_parts': sum(item.delivery_status == 'read' for item in entries),
    }


def _delivery_parts(message):
    return [{
        'part_number': item.part_number,
        'status': (item.delivery_status or 'accepted') if item.sent else 'failed' if item.failed_at else 'processing' if item.locked_at else 'pending',
        'attempts': item.attempts,
        'last_error': item.delivery_error if item.delivery_status == 'failed' else item.last_error,
        'next_attempt_at': item.next_attempt_at.isoformat() if item.next_attempt_at else None,
        'meta_message_id': item.meta_message_id,
        'sent_at': item.provider_sent_at.isoformat() if item.provider_sent_at else None,
        'delivered_at': item.delivered_at.isoformat() if item.delivered_at else None,
        'read_at': item.read_at.isoformat() if item.read_at else None,
        'delivery_failed_at': item.delivery_failed_at.isoformat() if item.delivery_failed_at else None,
    } for item in sorted(message.outbox_entries.all(), key=lambda item: (item.part_number, item.pk))]
