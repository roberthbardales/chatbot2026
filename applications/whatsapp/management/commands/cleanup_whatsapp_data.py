from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from applications.whatsapp.models import WorkerHeartbeat


class Command(BaseCommand):
    help = ('Elimina latidos de workers antiguos. '
            'No toca mensajes ni Outbox. Usa --dry-run para solo contar.')

    def add_arguments(self, parser):
        parser.add_argument('--heartbeat-days', type=int, default=7)
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **options):
        if options['heartbeat_days'] < 1:
            raise CommandError('La retención debe ser de al menos un día.')
        now = timezone.now()
        # A worker whose last heartbeat is older than the cutoff is long gone (running ones
        # refresh it every few seconds), whether or not it stopped cleanly. The newest row
        # is always kept so the health check still has something to report.
        heartbeat_cutoff = now - timedelta(days=options['heartbeat_days'])
        newest = WorkerHeartbeat.objects.order_by('-heartbeat_at').values_list('pk', flat=True).first()
        heartbeats = WorkerHeartbeat.objects.filter(heartbeat_at__lt=heartbeat_cutoff).exclude(pk=newest)
        if options['dry_run']:
            self.stdout.write(f'Simulación: latidos={heartbeats.count()}.')
            return
        removed_heartbeats, _ = heartbeats.delete()
        self.stdout.write(f'Limpieza: latidos={removed_heartbeats}.')
