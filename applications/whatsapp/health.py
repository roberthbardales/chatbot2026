from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from .models import WorkerHeartbeat


def worker_health():
    now = timezone.now()
    alive_cutoff = now - timedelta(seconds=max(settings.WHATSAPP_WORKER_HEALTH_SECONDS,
                                             settings.WHATSAPP_WORKER_HEARTBEAT_SECONDS * 3))
    progress_cutoff = now - timedelta(seconds=max(settings.WHATSAPP_WORKER_STALL_SECONDS,
                                                settings.OPENROUTER_TIMEOUT * 3 + 60))
    active = WorkerHeartbeat.objects.filter(stopped_at__isnull=True, heartbeat_at__gte=alive_cutoff)
    healthy = active.filter(last_progress_at__gte=progress_cutoff, last_error='').order_by('-heartbeat_at').first()
    latest = healthy or WorkerHeartbeat.objects.order_by('-heartbeat_at').first()
    if healthy:
        state = 'running'
    elif latest is None:
        state = 'not_started'
    elif latest.stopped_at:
        state = 'stopped'
    elif latest.heartbeat_at < alive_cutoff:
        state = 'unresponsive'
    elif latest.last_progress_at < progress_cutoff:
        state = 'stalled'
    else:
        state = 'degraded'
    return {
        'healthy': healthy is not None,
        'state': state,
        'active_workers': active.count(),
        'heartbeat_at': latest.heartbeat_at.isoformat() if latest else None,
        'last_success_at': latest.last_success_at.isoformat() if latest and latest.last_success_at else None,
        'phase': latest.phase if latest else None,
        'last_error': latest.last_error if latest else '',
    }
