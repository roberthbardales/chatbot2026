import hashlib
import hmac
import json
import logging

from django.conf import settings
from django.http import HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from .services import enqueue_message, record_delivery_status


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
