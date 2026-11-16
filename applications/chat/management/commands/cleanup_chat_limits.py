from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from applications.chat.models import AIUsageDay, UsageBucket, UsageLease


class Command(BaseCommand):
    help = 'Limpia únicamente registros antiguos de límites de chat y reservas de IA; no toca mensajes ni inventario.'

    def add_arguments(self, parser):
        parser.add_argument('--retention-days', type=int, default=7)
        parser.add_argument('--usage-days', type=int, default=90)

    def handle(self, *args, **options):
        if options['retention_days'] < 1 or options['usage_days'] < 1:
            raise CommandError('La retención debe ser de al menos un día.')
        now = timezone.now()
        leases, _ = UsageLease.objects.filter(expires_at__lte=now).delete()
        active_keys = ['lock:' + scope for scope in UsageLease.objects.filter(expires_at__gt=now).values_list('scope', flat=True)]
        cutoff = min(now - timedelta(days=options['retention_days']),
                     now - timedelta(seconds=max(1, settings.CHAT_RATE_WINDOW_SECONDS)))
        buckets, _ = UsageBucket.objects.filter(window_started_at__lt=cutoff).exclude(key__in=active_keys).delete()
        usage, _ = AIUsageDay.objects.filter(day__lt=now.date() - timedelta(days=options['usage_days'])).delete()
        self.stdout.write(f'Limpieza: reservas vencidas={leases}, contadores antiguos={buckets}, días antiguos={usage}.')
