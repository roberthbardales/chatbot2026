from collections import Counter

from .blog_api import entry_date


def valid_entries(entries):
    return [entry for entry in entries if isinstance(entry, dict)]


def latest_entry(entries, year=None):
    items = valid_entries(entries)
    if year is not None:
        items = [entry for entry in items if entry_date(entry).year == year]
    return max(items, key=entry_date, default=None)


def oldest_entry(entries):
    return min(valid_entries(entries), key=entry_date, default=None)


def entries_by_year(entries, year):
    return sorted(
        [entry for entry in valid_entries(entries) if entry_date(entry).year == year],
        key=entry_date,
        reverse=True,
    )


def author_counts(entries):
    return Counter(
        (entry.get('user') or {}).get('full_name', 'Autor no identificado')
        if isinstance(entry.get('user'), dict) else str(entry.get('user') or 'Autor no identificado')
        for entry in valid_entries(entries)
    )


def most_active_author(entries):
    counts = author_counts(entries)
    return counts.most_common(1)[0] if counts else None


def category_counts(entries):
    return Counter(
        (entry.get('category') or {}).get('name', 'Sin categoría')
        if isinstance(entry.get('category'), dict) else str(entry.get('category') or 'Sin categoría')
        for entry in valid_entries(entries)
    )


def most_liked_entries(entries, limit=5):
    return sorted(valid_entries(entries), key=lambda entry: entry.get('likes_count') or 0, reverse=True)[:limit]
