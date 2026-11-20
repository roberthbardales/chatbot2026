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


def _post_openrouter(payload):
    """Envía la petición a OpenRouter y devuelve el JSON de la respuesta."""
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
    return data


def _openrouter(prompt, image_data_url=None):
    if not settings.OPENROUTER_API_KEY:
        raise ProviderError('OpenRouter API key is not configured')
    content = [{'type': 'text', 'text': prompt}]
    if image_data_url:
        content.append({'type': 'image_url', 'image_url': {'url': image_data_url}})
    data = _post_openrouter({
        'model': settings.OPENROUTER_MODEL,
        'max_tokens': max(1, settings.AI_MAX_OUTPUT_TOKENS),
        'messages': [
            {'role': 'system', 'content': settings.SYSTEM_PROMPT},
            {'role': 'user', 'content': content},
        ],
    })
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


def _parse_tool_reply(data):
    try:
        message = data['choices'][0]['message']
    except (KeyError, IndexError, TypeError) as exc:
        raise ProviderError('OpenRouter returned an empty response') from exc
    if not isinstance(message, dict):
        raise ProviderError('OpenRouter returned an empty response')
    content = message.get('content')
    if isinstance(content, list):
        content = ''.join(part.get('text', '') for part in content if isinstance(part, dict))
    text = content.strip() if isinstance(content, str) else ''
    raw_calls = message.get('tool_calls') or []
    if not isinstance(raw_calls, list):
        raise ProviderError('OpenRouter returned invalid tool calls')
    calls = []
    for call in raw_calls:
        function = call.get('function') if isinstance(call, dict) else None
        if not isinstance(function, dict) or not isinstance(function.get('name'), str):
            raise ProviderError('OpenRouter returned invalid tool calls')
        try:
            arguments = json.loads(function.get('arguments') or '{}')
        except (TypeError, ValueError):
            arguments = None
        calls.append({
            'id': str(call.get('id') or ''),
            'name': function['name'],
            # None cuando los argumentos no son un objeto JSON válido: quien llama decide qué hacer.
            'arguments': arguments if isinstance(arguments, dict) else None,
        })
    if not text and not calls:
        raise ProviderError('OpenRouter returned an empty response')
    return {
        'content': text,
        'tool_calls': calls,
        # Mensaje del asistente tal cual, para devolverlo en la siguiente vuelta junto con los resultados.
        'message': {'role': 'assistant', 'content': text or None, **({'tool_calls': raw_calls} if raw_calls else {})},
    }


def chat_with_tools(provider, messages, tools, system):
    """Una vuelta de conversación con herramientas.

    `messages` no incluye el mensaje de sistema. Devuelve {'content', 'tool_calls', 'message'}.
    Pasa por el mismo control de cupo y concurrencia que `generate_response`."""
    if provider != OPENROUTER:
        raise ProviderError(f'Unknown chat provider: {provider}')
    if not messages or not system or not system.strip():
        raise ProviderError('Prompt is empty')
    if not settings.OPENROUTER_API_KEY:
        raise ProviderError('OpenRouter API key is not configured')
    payload = {
        'model': settings.OPENROUTER_MODEL,
        'max_tokens': max(1, settings.AI_MAX_OUTPUT_TOKENS),
        'temperature': settings.AI_AGENT_TEMPERATURE,
        'messages': [{'role': 'system', 'content': system}] + list(messages),
    }
    if tools:
        payload['tools'] = tools
    from .limits import ai_admission
    with ai_admission(json.dumps([system, messages, tools], ensure_ascii=False, default=str)):
        data = _post_openrouter(payload)
    return _parse_tool_reply(data)


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
        if not settings.OPENROUTER_API_KEY:
            raise ProviderError('OpenRouter API key is not configured')
        from .limits import ai_admission
        with ai_admission(prompt, image_data_url):
            return handler(prompt, image_data_url=image_data_url)
    return handler(prompt)
