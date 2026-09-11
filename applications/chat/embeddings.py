import logging

from django.conf import settings


logger = logging.getLogger(__name__)


class EmbeddingError(Exception):
    pass


def embed_text(text, task_type):
    if not settings.GEMINI_API_KEY:
        raise EmbeddingError('Gemini API key is not configured')
    try:
        import google.generativeai as genai

        genai.configure(api_key=settings.GEMINI_API_KEY)
        arguments = {
            'model': settings.EMBEDDING_MODEL,
            'content': text,
            'task_type': task_type,
            'output_dimensionality': settings.EMBEDDING_DIMENSIONS,
        }
        if task_type == 'retrieval_document':
            arguments['title'] = 'DummyJSON product'
        result = genai.embed_content(**arguments)
        values = result.get('embedding') if isinstance(result, dict) else getattr(result, 'embedding', None)
        if isinstance(values, dict):
            values = values.get('values')
        if not values or len(values) != settings.EMBEDDING_DIMENSIONS:
            raise EmbeddingError('Unexpected embedding dimensions')
        return list(values)
    except EmbeddingError:
        raise
    except Exception as exc:
        logger.warning('Embedding request failed: %s', exc)
        raise EmbeddingError from exc
