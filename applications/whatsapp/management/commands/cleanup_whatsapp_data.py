from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from applications.whatsapp.models import DeliveryEvent, WorkerHeartbeat


class Command(BaseCommand):
    help = ('Elimina latidos de workers detenidos y eventos de entrega antiguos. '
            'No toca mensajes, Outbox ni auditorías. Usa --dry-run para solo contar.')

    def add_arguments(self, parser):
        parser.add_argument('--heartbeat-days', type=int, default=7)
        parser.add_argument('--event-days', type=int, default=90)
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **options):
        if options['heartbeat_days'] < 1 or options['event_days'] < 1:
            raise CommandError('La retención debe ser de al menos un día.')
        now = timezone.now()
        # A worker whose last heartbeat is older than the cutoff is long gone (running ones
        # refresh it every few seconds), whether or not it stopped cleanly. The newest row
        # is always kept so the health check still has something to report.
        heartbeat_cutoff = now - timedelta(days=options['heartbeat_days'])
        newest = WorkerHeartbeat.objects.order_by('-heartbeat_at').values_list('pk', flat=True).first()
        heartbeats = WorkerHeartbeat.objects.filter(heartbeat_at__lt=heartbeat_cutoff).exclude(pk=newest)
        # Events whose Outbox was never matched are kept just as long: the callback
        # may legitimately arrive long before or after the send is persisted.
        events = DeliveryEvent.objects.filter(received_at__lt=now - timedelta(days=options['event_days']))
        if options['dry_run']:
            self.stdout.write(f'Simulación: latidos={heartbeats.count()}, eventos de entrega={events.count()}.')
            return
        removed_heartbeats, _ = heartbeats.delete()
        removed_events, _ = events.delete()
        self.stdout.write(f'Limpieza: latidos={removed_heartbeats}, eventos de entrega={removed_events}.')
