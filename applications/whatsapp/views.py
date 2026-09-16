import hashlib
import hmac
import json
import logging
import uuid
from json import JSONDecodeError

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.contrib.auth import REDIRECT_FIELD_NAME
from django.contrib.auth.decorators import user_passes_test
from django.db import transaction
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from .models import Outbox, WhatsAppContact, WhatsAppMessage
from .services import enqueue_message


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
        if (
            request.GET.get('hub.mode') == 'subscribe'
            and request.GET.get('hub.verify_token') == settings.WHATSAPP_VERIFY_TOKEN
        ):
            return HttpResponse(request.GET.get('hub.challenge', ''), content_type='text/plain')
        return HttpResponse('Forbidden', status=403)

    if not _valid_signature(request):
        return JsonResponse({'error': 'Invalid signature'}, status=403)
    try:
        payload = json.loads(request.body.decode('utf-8'))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

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
def dashboard(request):
    return render(request, 'whatsapp/dashboard.html')


@login_required
@whatsapp_manager_required
def connection_status(request):
    return JsonResponse({
        'provider': 'meta_cloud_api',
        'configured': bool(settings.WHATSAPP_ACCESS_TOKEN and settings.WHATSAPP_PHONE_NUMBER_ID),
        'qr': None,
        'note': 'Meta Cloud API no utiliza QR local.',
    })


@login_required
@whatsapp_manager_required
def conversations(request):
    return JsonResponse({'conversations': [
        {
            'id': contact.id,
            'phone': contact.phone_number,
            'name': contact.display_name,
            'mode': contact.mode,
            'last_message_at': contact.last_message_at.isoformat() if contact.last_message_at else None,
        }
        for contact in WhatsAppContact.objects.order_by('-last_message_at', '-updated_at')[:100]
    ]})


@login_required
@whatsapp_manager_required
def conversation_messages(request, contact_id):
    contact = get_object_or_404(WhatsAppContact, pk=contact_id)
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
                'created_at': message.created_at.isoformat(),
            }
            for message in contact.messages.order_by('created_at', 'id')[:500]
        ],
    })


@login_required
@whatsapp_manager_required
@require_http_methods(['DELETE'])
def delete_conversation(request, contact_id):
    contact = get_object_or_404(WhatsAppContact, pk=contact_id)
    contact.delete()
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
    mode = data.get('mode')
    if mode not in {WhatsAppContact.AI, WhatsAppContact.HUMAN}:
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
    text = (data.get('content') or '').strip()
    if not text:
        return JsonResponse({'error': 'El contenido es obligatorio.'}, status=400)
    with transaction.atomic():
        message = WhatsAppMessage.objects.create(
            contact=contact,
            message_id=f'local-{uuid.uuid4()}',
            text=text,
            response=text,
            status=WhatsAppMessage.PROCESSED,
            is_from_customer=False,
        )
        Outbox.objects.create(contact=contact, message=message, content=text)
        contact.last_message_at = timezone.now()
        contact.save(update_fields=('last_message_at', 'updated_at'))
    return JsonResponse({'queued': True, 'message_id': message.id})
