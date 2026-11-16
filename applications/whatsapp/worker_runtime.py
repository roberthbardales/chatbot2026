import logging
from threading import Event, Thread

from django.conf import settings
from django.db import close_old_connections, connections
from django.utils import timezone

from .models import WorkerHeartbeat

logger = logging.getLogger(__name__)


class Heartbeat:
    """Liveness is separate from main-loop progress, so a hung loop is detectable."""
    def __init__(self, worker_id):
        self.worker_id = worker_id
        self.stop = Event()
        self.thread = Thread(target=self._run, name='whatsapp-heartbeat', daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def _run(self):
        try:
            while not self.stop.wait(max(1, settings.WHATSAPP_WORKER_HEARTBEAT_SECONDS)):
                close_old_connections()
                try:
                    WorkerHeartbeat.objects.filter(pk=self.worker_id, stopped_at__isnull=True).update(heartbeat_at=timezone.now())
                except Exception:
                    logger.exception('WhatsApp heartbeat update failed')
                finally:
                    close_old_connections()
        finally:
            connections.close_all()

    def __exit__(self, *args):
        self.stop.set()
        self.thread.join(timeout=2)
