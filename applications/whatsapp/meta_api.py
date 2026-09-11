import json
import logging
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from django.conf import settings


logger = logging.getLogger(__name__)


class WhatsAppAPIError(Exception):
    pass


def send_text_message(phone_number, text):
    if not settings.WHATSAPP_ACCESS_TOKEN or not settings.WHATSAPP_PHONE_NUMBER_ID:
        raise WhatsAppAPIError('WhatsApp credentials are not configured')
    url = (
        f'https://graph.facebook.com/{settings.WHATSAPP_API_VERSION}/'
        f'{settings.WHATSAPP_PHONE_NUMBER_ID}/messages'
    )
    payload = {
        'messaging_product': 'whatsapp',
        'to': phone_number,
        'type': 'text',
        'text': {'body': text[:4096]},
    }
    request = Request(
        url,
        data=json.dumps(payload).encode('utf-8'),
        headers={
            'Authorization': f'Bearer {settings.WHATSAPP_ACCESS_TOKEN}',
            'Content-Type': 'application/json',
        },
        method='POST',
    )
    try:
        with urlopen(request, timeout=15) as response:
            result = json.loads(response.read().decode('utf-8'))
    except (HTTPError, URLError, TimeoutError, ValueError, OSError) as exc:
        logger.warning('WhatsApp API request failed: %s', exc)
        raise WhatsAppAPIError from exc
    if not isinstance(result, dict) or not result.get('messages'):
        raise WhatsAppAPIError('Unexpected WhatsApp API response')
    return result
