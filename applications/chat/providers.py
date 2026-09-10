import logging

from django.conf import settings


logger = logging.getLogger(__name__)

GEMINI = 'gemini'
GROQ = 'groq'
PROVIDER_CHOICES = (
    (GEMINI, 'Gemini 2.5 Flash-Lite'),
    (GROQ, 'Groq GPT-OSS 20B'),
)


class ProviderError(Exception):
    pass


def _gemini(prompt: str) -> str:
    if not settings.GEMINI_API_KEY:
        raise ProviderError('Gemini API key is not configured')
    try:
        import google.generativeai as genai
        genai.configure(api_key=settings.GEMINI_API_KEY)
        model = genai.GenerativeModel('gemini-2.5-flash-lite')
        response = model.generate_content(prompt, request_options={'timeout': 20})
        text = getattr(response, 'text', '').strip()
        if not text:
            raise ProviderError('Gemini returned an empty response')
        return text
    except ProviderError:
        raise
    except Exception as exc:
        logger.exception('Gemini request failed')
        raise ProviderError(str(exc)) from exc


def _groq(prompt: str) -> str:
    if not settings.GROQ_API_KEY:
        raise ProviderError('Groq API key is not configured')
    try:
        from groq import Groq
        client = Groq(api_key=settings.GROQ_API_KEY, timeout=20)
        response = client.chat.completions.create(
            model='openai/gpt-oss-20b',
            messages=[{'role': 'user', 'content': prompt}],
            timeout=20,
        )
        text = (response.choices[0].message.content or '').strip()
        if not text:
            raise ProviderError('Groq returned an empty response')
        return text
    except ProviderError:
        raise
    except Exception as exc:
        logger.exception('Groq request failed')
        raise ProviderError(str(exc)) from exc


_PROVIDERS = {
    GEMINI: _gemini,
    GROQ: _groq,
}


def generate_response(provider: str, prompt: str) -> str:
    if not prompt or not prompt.strip():
        raise ProviderError('Prompt is empty')

    try:
        handler = _PROVIDERS[provider]
    except KeyError:
        raise ProviderError(f'Unknown chat provider: {provider}') from None

    return handler(prompt)