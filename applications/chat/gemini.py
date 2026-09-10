def generate_response(prompt):
    from .providers import generate_response as generate_with_provider
    return generate_with_provider('gemini', prompt)


class GeminiError(Exception):
    """Compatibility exception for callers that still import this module."""
