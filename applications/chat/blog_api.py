import html
import logging
import re
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
import json
from datetime import datetime
from django.core.cache import cache


logger = logging.getLogger(__name__)
BLOG_API_URL = 'https://bardalesweb.duckdns.org/api/entries/entries/'
ENTRIES_CACHE_KEY = 'chat:blog:entries:v1'
ENTRIES_CACHE_TIMEOUT = 300


class BlogAPIError(Exception):
    pass


def _request_json(url, timeout=5):
    try:
        request = Request(url, headers={'Accept': 'application/json', 'User-Agent': 'chatbot2026/1.0'})
        with urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode('utf-8'))
    except (HTTPError, URLError, TimeoutError, ValueError, OSError) as exc:
        logger.warning('Blog API request failed: %s', exc)
        raise BlogAPIError from exc


def list_entries():
    data = cache.get(ENTRIES_CACHE_KEY)
    if data is None:
        data = _request_json(BLOG_API_URL)
        cache.set(ENTRIES_CACHE_KEY, data, ENTRIES_CACHE_TIMEOUT)
    if not isinstance(data, list):
        raise BlogAPIError('Unexpected blog API response')
    return data


def get_all_entries():
    return list_entries()


def entry_date(entry):
    """Return an entry date suitable for reliable chronological sorting."""
    value = entry.get('created') if isinstance(entry, dict) else None
    if not value:
        return datetime.min
    try:
        return datetime.fromisoformat(str(value).replace('Z', '+00:00')).replace(tzinfo=None)
    except ValueError:
        return datetime.min


def latest_entries(year=None):
    entries = [entry for entry in list_entries() if isinstance(entry, dict)]
    if year is not None:
        entries = [entry for entry in entries if entry_date(entry).year == year]
    return sorted(entries, key=entry_date, reverse=True)


def get_entry(slug):
    data = _request_json(f'{BLOG_API_URL}{slug}/')
    if not isinstance(data, dict):
        raise BlogAPIError('Unexpected blog entry response')
    return data


def clean_content(content, max_chars=7000):
    text = re.sub(r'<(script|style)[^>]*>.*?</\1>', ' ', content or '', flags=re.I | re.S)
    text = re.sub(r'<[^>]+>', ' ', text)
    text = html.unescape(text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text[:max_chars]


def _metadata(entry):
    if not isinstance(entry, dict):
        return ''
    category = entry.get('category') or {}
    tags = entry.get('tag') or entry.get('tags') or []
    if not isinstance(tags, (list, tuple)):
        tags = []
    return ' '.join([
        str(entry.get('title', '')),
        str(entry.get('resume', '')),
        str(category.get('name', '') if isinstance(category, dict) else category),
        ' '.join(str(tag.get('name', '') if isinstance(tag, dict) else tag) for tag in tags),
    ]).lower()


def search_entries(query, limit=4):
    entries = list_entries()
    terms = {term for term in re.findall(r'[\wáéíóúñü]+', query.lower()) if len(term) > 2}
    scored = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        metadata = _metadata(entry)
        score = sum(2 if term in str(entry.get('title', '')).lower() else 1 for term in terms if term in metadata)
        if score:
            scored.append((score, entry))
    scored.sort(key=lambda item: (item[0], item[1].get('created', '' )), reverse=True)
    return [entry for _, entry in scored[:limit]]
