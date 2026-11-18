import hashlib
import hmac
import json
import logging
from functools import wraps

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from . import panel
from .models import WhatsAppContact
from .services import create_manual_message, enqueue_message, record_delivery_status


logger = logging.getLogger(__name__)


def _valid_signature(request):
    signature = request.headers.get('X-Hub-Signature-256', '')
    if not signature.startswith('sha256=') or not settings.WHATSAPP_APP_SECRET:
        return False
    expected = hmac.new(
        settings.WHATSAPP_APP_SECRET.encode('utf-8'),
        request.body,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(signature[7:].encode('utf-8'), expected.encode('utf-8'))


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
            if not isinstance(entry, dict) or not isinstance(entry.get('changes', []), list):
                continue
            for change in entry.get('changes', []):
                value = change.get('value', {}) if isinstance(change, dict) else None
                if not isinstance(value, dict):
                    continue
                # Meta usa datos ficticios al probar manualmente el campo messages.
                metadata = value.get('metadata', {})
                if not isinstance(metadata, dict):
                    continue
                if (
                    metadata.get('phone_number_id')
                    and metadata.get('phone_number_id') != settings.WHATSAPP_PHONE_NUMBER_ID
                ):
                    continue
                statuses = value.get('statuses', [])
                if isinstance(statuses, list):
                    for item in statuses:
                        if isinstance(item, dict):
                            record_delivery_status(item.get('id'), item.get('status'), item.get('errors'))
                raw_contacts = value.get('contacts', [])
                raw_messages = value.get('messages', [])
                if not isinstance(raw_contacts, list) or not isinstance(raw_messages, list):
                    continue
                contacts = {
                    item.get('wa_id'): (item.get('profile') or {}).get('name', '')
                    for item in raw_contacts
                    if isinstance(item, dict) and isinstance(item.get('profile') or {}, dict)
                }
                for message in raw_messages:
                    if not isinstance(message, dict):
                        continue
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


# --- Panel de operadores ---------------------------------------------------------------------
# Solo superusuario, administrador o empleado: iniciar sesión no basta.

MAX_MANUAL_LENGTH = 4000


def manager_required(view):
    @wraps(view)
    @login_required
    @never_cache
    def wrapper(request, *args, **kwargs):
        if not panel.can_manage(request.user):
            raise PermissionDenied
        return view(request, *args, **kwargs)
    return wrapper


def _json_body(request):
    try:
        data = json.loads(request.body.decode('utf-8') or '{}')
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


@manager_required
def dashboard(request):
    return render(request, 'whatsapp/dashboard.html')


@manager_required
def system_status(request):
    return JsonResponse(panel.system_status())


@manager_required
def conversations(request):
    try:
        offset = int(request.GET.get('offset', '0'))
        if not 0 <= offset <= 1_000_000:
            raise ValueError
    except ValueError:
        return JsonResponse({'error': 'Paginación inválida.'}, status=400)
    return JsonResponse(panel.conversations_page(request.GET.get('q', '').strip()[:120], offset))


@manager_required
def conversation_messages(request, contact_id):
    contact = get_object_or_404(WhatsAppContact, pk=contact_id)
    return JsonResponse(panel.conversation_payload(contact))


@manager_required
@require_http_methods(['POST'])
def change_mode(request, contact_id):
    contact = get_object_or_404(WhatsAppContact, pk=contact_id)
    data = _json_body(request)
    mode = data.get('mode') if data is not None else None
    if not isinstance(mode, str) or mode not in {WhatsAppContact.AI, WhatsAppContact.HUMAN}:
        return JsonResponse({'error': 'Modo inválido.'}, status=400)
    contact.mode = mode
    contact.save(update_fields=('mode', 'updated_at'))
    logger.warning('WhatsApp mode changed: actor_id=%s contact_id=%s mode=%s', request.user.pk, contact.pk, mode)
    return JsonResponse({'mode': contact.mode})


@manager_required
@require_http_methods(['POST'])
def send_manual_message(request, contact_id):
    contact = get_object_or_404(WhatsAppContact, pk=contact_id)
    data = _json_body(request)
    text = data.get('content') if data is not None else None
    if not isinstance(text, str) or not text.strip():
        return JsonResponse({'error': 'Escribe el mensaje.'}, status=400)
    text = text.strip()
    if len(text) > MAX_MANUAL_LENGTH:
        return JsonResponse({'error': f'El mensaje no puede superar {MAX_MANUAL_LENGTH} caracteres.'}, status=400)
    message = create_manual_message(contact, text)
    return JsonResponse({'queued': True, 'message_id': message.id})
