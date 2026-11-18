"""Database-backed admission controls shared by web processes and workers."""
import ipaddress
import logging
import math
from contextlib import contextmanager
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.utils.crypto import salted_hmac

from .models import AIUsageDay, UsageBucket, UsageLease

logger = logging.getLogger(__name__)


class LimitExceeded(Exception):
    def __init__(self, message, retry_after=60, code='rate_limit'):
        super().__init__(message)
        self.retry_after = max(1, int(math.ceil(retry_after)))
        self.code = code


def identity(kind, value):
    return kind + ':' + salted_hmac('chat-admission', str(value)).hexdigest()


def _bucket(key, now):
    UsageBucket.objects.get_or_create(key=key, defaults={'window_started_at': now})
    bucket = UsageBucket.objects.select_for_update().get(key=key)
    if key.startswith('lock:'):
        bucket.window_started_at = now
        bucket.save(update_fields=('window_started_at',))
    return bucket


def enforce_rates(rules, window=None):
    now = timezone.now()
    window = max(1, window or settings.CHAT_RATE_WINDOW_SECONDS)
    # Sorted locks avoid deadlocks when identities overlap across requests.
    with transaction.atomic():
        for key, maximum in sorted(rules):
            if maximum <= 0:
                continue
            bucket = _bucket('rate:' + key, now)
            end = bucket.window_started_at + timedelta(seconds=window)
            if end <= now:
                bucket.window_started_at = now
                bucket.count = 0
                end = now + timedelta(seconds=window)
            if bucket.count >= maximum:
                raise LimitExceeded('Has alcanzado el límite de consultas. Espera antes de volver a intentarlo.',
                                    (end - now).total_seconds())
            bucket.count += 1
            bucket.save(update_fields=('window_started_at', 'count'))


def _parse_ip(value):
    try:
        parsed = ipaddress.ip_address(value)
    except ValueError:
        return None
    if isinstance(parsed, ipaddress.IPv6Address) and parsed.ipv4_mapped:
        parsed = parsed.ipv4_mapped
    return parsed


def _forwarded_address(request):
    """IP que añadió el último proxy de confianza, o None.

    Con TRUSTED_PROXY_COUNT=n se toma la n-ésima entrada empezando por la derecha de
    X-Forwarded-For: son las que añadieron nuestros proxies; lo que está a la izquierda
    lo puede falsificar el cliente y se ignora.
    """
    trusted = max(0, settings.TRUSTED_PROXY_COUNT)
    if not trusted:
        return None
    entries = [item.strip() for item in request.META.get('HTTP_X_FORWARDED_FOR', '').split(',') if item.strip()]
    if len(entries) < trusted:
        return None
    return _parse_ip(entries[-trusted])


def client_address(request):
    """Dirección del cliente (IPv6 agrupada por /64).

    Por defecto usa solo REMOTE_ADDR e ignora X-Forwarded-For, que controla el cliente.
    Detrás de proxies propios, define TRUSTED_PROXY_COUNT (ver docs/usage-limits.md).
    """
    parsed = _forwarded_address(request) or _parse_ip(request.META.get('REMOTE_ADDR', ''))
    if parsed is None:
        return 'unknown'
    if parsed.version == 6:
        return str(ipaddress.ip_network(f'{parsed}/64', strict=False).network_address)
    return str(parsed)


@contextmanager
def principal_slot(principal):
    scope = 'request:' + principal
    now = timezone.now()
    with transaction.atomic():
        _bucket('lock:' + scope, now)
        UsageLease.objects.filter(scope=scope, expires_at__lte=now).delete()
        expiries = list(UsageLease.objects.filter(scope=scope, expires_at__gt=now).values_list('expires_at', flat=True))
        if len(expiries) >= max(1, settings.CHAT_MAX_CONCURRENT_PER_PRINCIPAL):
            raise LimitExceeded('Ya hay una consulta en curso. Espera a que termine.',
                                (min(expiries) - now).total_seconds(), 'concurrency_limit')
        lease = UsageLease.objects.create(scope=scope, expires_at=now + timedelta(
            seconds=max(settings.CHAT_REQUEST_LEASE_SECONDS, settings.OPENROUTER_TIMEOUT * 3 + 60)))
    try:
        yield
    finally:
        UsageLease.objects.filter(pk=lease.pk).delete()


@contextmanager
def ai_admission(prompt, image_data_url=None):
    if len(prompt) + len(settings.SYSTEM_PROMPT) > settings.AI_MAX_PROMPT_CHARACTERS:
        raise LimitExceeded('La consulta contiene demasiado contexto. Reduce su tamaño.', 60, 'context_limit')
    # Conservative text reservation, not billed tokens or a dollar estimate.
    reserved = len(prompt.encode('utf-8')) + len(settings.SYSTEM_PROMPT.encode('utf-8')) + 64
    reserved += max(1, settings.AI_MAX_OUTPUT_TOKENS)
    if image_data_url:
        reserved += max(1, settings.AI_IMAGE_TOKEN_RESERVE)
    now = timezone.now()
    next_day = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    with transaction.atomic():
        _bucket('lock:ai:global', now)
        UsageLease.objects.filter(scope='ai:global', expires_at__lte=now).delete()
        expiries = list(UsageLease.objects.filter(scope='ai:global', expires_at__gt=now).values_list('expires_at', flat=True))
        if len(expiries) >= max(1, settings.AI_MAX_CONCURRENT_REQUESTS):
            raise LimitExceeded('El servicio de IA está ocupado. Inténtalo más tarde.',
                                (min(expiries) - now).total_seconds(), 'ai_concurrency_limit')
        usage, _ = AIUsageDay.objects.get_or_create(day=now.date())
        usage = AIUsageDay.objects.select_for_update().get(pk=usage.pk)
        if (settings.AI_DAILY_REQUEST_LIMIT > 0 and usage.requests >= settings.AI_DAILY_REQUEST_LIMIT) or (
            settings.AI_DAILY_TOKEN_LIMIT > 0 and usage.reserved_tokens + reserved > settings.AI_DAILY_TOKEN_LIMIT
        ):
            logger.warning('AI daily budget blocked: day=%s requests=%s reserved_tokens=%s', usage.day, usage.requests, usage.reserved_tokens)
            raise LimitExceeded('El servicio de IA alcanzó su cupo diario. Inténtalo más tarde.',
                                (next_day - now).total_seconds(), 'daily_budget')
        usage.requests += 1
        usage.reserved_tokens += reserved
        usage.save(update_fields=('requests', 'reserved_tokens'))
        lease = UsageLease.objects.create(scope='ai:global', expires_at=now + timedelta(seconds=max(60, settings.OPENROUTER_TIMEOUT + 30)))
    try:
        yield
    finally:
        UsageLease.objects.filter(pk=lease.pk).delete()
