import json
import logging
import base64
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from django.conf import settings


logger = logging.getLogger(__name__)
MAX_TEXT_LENGTH = 4096


class WhatsAppAPIError(Exception):
    def __init__(self, message, *, http_status=None, code=None, is_transient=False, configuration=False):
        super().__init__(message)
        self.http_status = http_status
        self.code = code
        self.is_transient = is_transient
        self.configuration = configuration or code in {0, 3, 10, 33, 190, 131005, 131037, 131042, 131045, 133010} or (
            isinstance(code, int) and 200 <= code <= 299
        )

    @property
    def retryable(self):
        # Meta: classify by code, not by HTTP 400 or message text.
        # https://developers.facebook.com/docs/whatsapp/cloud-api/support/error-codes/
        if self.configuration:
            return False
        if self.is_transient:
            return True
        return self.code not in {100, 368, 130403, 130497, 131008, 131009, 131021,
                                 131026, 131031, 131047, 131050, 131051}

    def diagnostic(self):
        category = 'configuration' if self.configuration else 'temporary_or_unknown' if self.retryable else 'permanent'
        return f'[{category} http={self.http_status} code={self.code}] {self}'


def _send_http_error(status, details):
    try:
        payload = json.loads(details)
    except ValueError:
        payload = {}
    error = payload.get('error', {}) if isinstance(payload, dict) else {}
    if not isinstance(error, dict):
        error = {}
    code = error.get('code')
    if not isinstance(code, int):
        code = None
    return WhatsAppAPIError(details, http_status=status, code=code,
                            is_transient=error.get('is_transient') is True)


def send_text_message(phone_number, text):
    if len(text) > MAX_TEXT_LENGTH:
        raise WhatsAppAPIError('WhatsApp text exceeds 4096 characters; split it before sending', code=131009)
    if not settings.WHATSAPP_ACCESS_TOKEN or not settings.WHATSAPP_PHONE_NUMBER_ID:
        raise WhatsAppAPIError('WhatsApp credentials are not configured', configuration=True)
    url = (
        f'https://graph.facebook.com/{settings.WHATSAPP_API_VERSION}/'
        f'{settings.WHATSAPP_PHONE_NUMBER_ID}/messages'
    )
    payload = {
        'messaging_product': 'whatsapp',
        'to': phone_number,
        'type': 'text',
        'text': {'body': text},
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
    logger.info(
        'Sending WhatsApp message: api_version=%s phone_number_id=%s recipient=%s',
        settings.WHATSAPP_API_VERSION,
        settings.WHATSAPP_PHONE_NUMBER_ID,
        f'***{phone_number[-4:]}' if len(phone_number) > 4 else '***',
    )
    try:
        with urlopen(request, timeout=15) as response:
            response_body = response.read().decode('utf-8')
            logger.info('WhatsApp API response: status=%s body_length=%s', response.status, len(response_body))
            result = json.loads(response_body)
    except HTTPError as exc:
        details = exc.read().decode('utf-8', errors='replace')
        error = _send_http_error(exc.code, details)
        # The raw body may echo the recipient's number; it is kept only in Outbox.last_error.
        logger.warning('WhatsApp API request failed: http=%s code=%s transient=%s recipient=%s',
                       exc.code, error.code, error.is_transient,
                       f'***{phone_number[-4:]}' if len(phone_number) > 4 else '***')
        raise error from exc
    except (URLError, TimeoutError, ValueError, OSError) as exc:
        logger.warning('WhatsApp API request failed: %s', exc)
        raise WhatsAppAPIError(str(exc)) from exc
    messages = result.get('messages') if isinstance(result, dict) else None
    message_id = messages[0].get('id') if isinstance(messages, list) and messages and isinstance(messages[0], dict) else None
    if not isinstance(message_id, str) or not 1 <= len(message_id) <= 255:
        raise WhatsAppAPIError('Unexpected WhatsApp API response')
    return result


def download_media_data(media_id):
    if not settings.WHATSAPP_ACCESS_TOKEN or not media_id:
        raise WhatsAppAPIError('WhatsApp media credentials are not configured')
    if not isinstance(media_id, str) or not media_id.isalnum():
        raise WhatsAppAPIError('WhatsApp media id is invalid')
    request = Request(
        f'https://graph.facebook.com/{settings.WHATSAPP_API_VERSION}/{media_id}',
        headers={'Authorization': f'Bearer {settings.WHATSAPP_ACCESS_TOKEN}'},
        method='GET',
    )
    try:
        with urlopen(request, timeout=15) as response:
            metadata = json.loads(response.read().decode('utf-8'))
        if not isinstance(metadata, dict):
            raise WhatsAppAPIError('WhatsApp media metadata is invalid')
        media_url = metadata.get('url')
        mime_type = metadata.get('mime_type', 'image/jpeg')
        if not media_url:
            raise WhatsAppAPIError('WhatsApp media URL is missing')
        if not isinstance(media_url, str) or not media_url.startswith('https://'):
            raise WhatsAppAPIError('WhatsApp media URL is invalid')
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
