import json
import logging
import base64
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
    logger.warning(
        'Sending WhatsApp message: api_version=%s phone_number_id=%s recipient=%s',
        settings.WHATSAPP_API_VERSION,
        settings.WHATSAPP_PHONE_NUMBER_ID,
        f'***{phone_number[-4:]}' if len(phone_number) > 4 else '***',
    )
    try:
        with urlopen(request, timeout=15) as response:
            response_body = response.read().decode('utf-8')
            logger.warning('WhatsApp API response: status=%s body_length=%s', response.status, len(response_body))
            result = json.loads(response_body)
    except HTTPError as exc:
        details = exc.read().decode('utf-8', errors='replace')
        logger.warning('WhatsApp API request failed with HTTP %s: %s', exc.code, details)
        raise WhatsAppAPIError(details) from exc
    except (URLError, TimeoutError, ValueError, OSError) as exc:
        logger.warning('WhatsApp API request failed: %s', exc)
        raise WhatsAppAPIError(str(exc)) from exc
    if not isinstance(result, dict) or not result.get('messages'):
        raise WhatsAppAPIError('Unexpected WhatsApp API response')
    return result


def download_media_data(media_id):
    if not settings.WHATSAPP_ACCESS_TOKEN or not media_id:
        raise WhatsAppAPIError('WhatsApp media credentials are not configured')
    request = Request(
        f'https://graph.facebook.com/{settings.WHATSAPP_API_VERSION}/{media_id}',
        headers={'Authorization': f'Bearer {settings.WHATSAPP_ACCESS_TOKEN}'},
        method='GET',
    )
    try:
        with urlopen(request, timeout=15) as response:
            metadata = json.loads(response.read().decode('utf-8'))
        media_url = metadata.get('url')
        mime_type = metadata.get('mime_type', 'image/jpeg')
        if not media_url:
            raise WhatsAppAPIError('WhatsApp media URL is missing')
        media_request = Request(
            media_url,
            headers={'Authorization': f'Bearer {settings.WHATSAPP_ACCESS_TOKEN}'},
            method='GET',
        )
        with urlopen(media_request, timeout=20) as response:
            data = response.read(settings.WHATSAPP_MAX_IMAGE_BYTES + 1)
            if len(data) > settings.WHATSAPP_MAX_IMAGE_BYTES:
                raise WhatsAppAPIError('WhatsApp image exceeds the configured size limit')
            encoded = base64.b64encode(data).decode('ascii')
    except HTTPError as exc:
        logger.warning('WhatsApp media request failed with HTTP %s', exc.code)
        raise WhatsAppAPIError('WhatsApp media download failed') from exc
    except (URLError, TimeoutError, ValueError, OSError) as exc:
        raise WhatsAppAPIError('WhatsApp media download failed') from exc
    return f'data:{mime_type};base64,{encoded}'
