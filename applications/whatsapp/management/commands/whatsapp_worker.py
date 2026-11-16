import logging
import time

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import close_old_connections
from django.utils import timezone

from applications.whatsapp.models import WorkerHeartbeat
from applications.whatsapp.services import process_outbox, process_pending_messages
from applications.whatsapp.worker_runtime import Heartbeat

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Procesa y envía WhatsApps reales pendientes. No sincroniza el inventario; --once NO es una simulación.'

    def add_arguments(self, parser):
        parser.add_argument('--once', action='store_true', help='Procesa una iteración real y termina.')
        parser.add_argument('--poll-seconds', type=int, default=None)

    def handle(self, *args, **options):
        interval = options['poll_seconds'] if options['poll_seconds'] is not None else settings.WHATSAPP_WORKER_POLL_SECONDS
        if interval < 1:
            raise CommandError('El intervalo debe ser de al menos un segundo.')
        now = timezone.now()
        worker = WorkerHeartbeat.objects.create(heartbeat_at=now, last_progress_at=now)
        self.stdout.write('WhatsApp worker iniciado; procesa colas reales, sin sincronizar inventario.')

        def progress(phase):
            WorkerHeartbeat.objects.filter(pk=worker.pk).update(
                heartbeat_at=timezone.now(), last_progress_at=timezone.now(), phase=phase,
            )

        try:
            with Heartbeat(worker.pk):
                while True:
                    close_old_connections()
                    errors = []
                    incoming = outgoing = 0

                    def send_outbox():
                        # An outbox error must not stop incoming processing, nor vice versa.
                        nonlocal outgoing
                        try:
                            progress('outgoing')
                            outgoing += process_outbox(progress_callback=lambda: progress('outgoing'))
                        except Exception as exc:
                            label = 'outgoing:' + type(exc).__name__
                            if label not in errors:
                                errors.append(label)
                            logger.exception('WhatsApp outgoing worker phase failed')

                    try:
                        progress('incoming')
                        incoming = process_pending_messages(
                            max_contacts=max(1, settings.WHATSAPP_WORKER_MAX_CONTACTS),
                            progress_callback=lambda: progress('incoming'),
                            after_contact=send_outbox,
                        )
                    except Exception as exc:
                        errors.append('incoming:' + type(exc).__name__)
                        logger.exception('WhatsApp incoming worker phase failed')
                    send_outbox()
                    now = timezone.now()
                    values = {'heartbeat_at': now, 'last_progress_at': now,
                              'phase': 'error' if errors else 'idle', 'last_error': '; '.join(errors)}
                    if not errors:
                        values['last_success_at'] = now
                    WorkerHeartbeat.objects.filter(pk=worker.pk).update(**values)
                    if incoming or outgoing:
                        self.stdout.write(f'Procesados: entrada={incoming}, salida={outgoing}')
                    if options['once']:
                        if errors:
                            raise CommandError('La iteración tuvo errores; consulta los logs.')
                        return
                    time.sleep(interval)
        except KeyboardInterrupt:
            self.stdout.write('WhatsApp worker detenido.')
        finally:
            WorkerHeartbeat.objects.filter(pk=worker.pk).update(stopped_at=timezone.now(), phase='stopped')
