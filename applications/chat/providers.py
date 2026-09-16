import json
import logging
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from django.conf import settings


logger = logging.getLogger(__name__)

OPENROUTER = 'openrouter'
PROVIDER_CHOICES = (
    (OPENROUTER, 'OpenRouter'),
)


class ProviderError(Exception):
    pass


def _openrouter(prompt, image_data_url=None):
    if not settings.OPENROUTER_API_KEY:
        raise ProviderError('OpenRouter API key is not configured')
    content = [{'type': 'text', 'text': prompt}]
    if image_data_url:
        content.append({'type': 'image_url', 'image_url': {'url': image_data_url}})
    payload = {
        'model': settings.OPENROUTER_MODEL,
        'messages': [
            {'role': 'system', 'content': settings.SYSTEM_PROMPT},
            {'role': 'user', 'content': content},
        ],
    }
    request = Request(
        'https://openrouter.ai/api/v1/chat/completions',
        data=json.dumps(payload).encode('utf-8'),
        headers={
            'Authorization': f'Bearer {settings.OPENROUTER_API_KEY}',
            'Content-Type': 'application/json',
            'HTTP-Referer': settings.OPENROUTER_HTTP_REFERER,
            'X-Title': settings.OPENROUTER_APP_NAME,
        },
        method='POST',
    )
    try:
        with urlopen(request, timeout=settings.OPENROUTER_TIMEOUT) as response:
            data = json.loads(response.read().decode('utf-8'))
    except HTTPError as exc:
        details = exc.read().decode('utf-8', errors='replace')
        logger.warning('OpenRouter request failed with HTTP %s: %s', exc.code, details)
        raise ProviderError('OpenRouter request failed') from exc
    except (URLError, TimeoutError, ValueError, OSError) as exc:
        logger.warning('OpenRouter request failed: %s', exc)
        raise ProviderError('OpenRouter request failed') from exc
    try:
        text = data['choices'][0]['message']['content'].strip()
    except (KeyError, IndexError, AttributeError, TypeError) as exc:
        content = None
        try:
            content = data['choices'][0]['message']['content']
        except (KeyError, IndexError, TypeError):
            pass
        if isinstance(content, list):
            text = ''.join(
                part.get('text', '') for part in content if isinstance(part, dict)
            ).strip()
            if text:
                return text
        raise ProviderError('OpenRouter returned an empty response') from exc
    if not text:
        raise ProviderError('OpenRouter returned an empty response')
    return text


_PROVIDERS = {
    OPENROUTER: _openrouter,
}


def generate_response(provider: str, prompt: str, image_data_url=None) -> str:
    if not prompt or not prompt.strip():
        raise ProviderError('Prompt is empty')

    try:
        handler = _PROVIDERS[provider]
    except KeyError:
        raise ProviderError(f'Unknown chat provider: {provider}') from None

    if provider == OPENROUTER:
        return handler(prompt, image_data_url=image_data_url)
    return handler(prompt)
