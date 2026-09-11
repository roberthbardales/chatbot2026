import hashlib
import hmac
import json
import logging

from django.conf import settings
from django.http import HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from .services import process_text_message


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
                    if message.get('type') != 'text':
                        continue
                    phone_number = message.get('from')
                    message_id = message.get('id')
                    text = (message.get('text') or {}).get('body', '').strip()
                    if not phone_number or not message_id or not text:
                        continue
                    process_text_message(
                        phone_number,
                        contacts.get(phone_number, ''),
                        message_id,
                        text,
                    )
    except Exception:
        logger.exception('WhatsApp message processing failed')
        return JsonResponse({'error': 'Message processing failed'}, status=500)
    return JsonResponse({'status': 'ok'})
