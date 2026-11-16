from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from applications.chat.models import AIUsageDay, UsageLease


class Command(BaseCommand):
    help = 'Muestra el cupo local de IA del día UTC; no llama a proveedores ni procesa colas.'

    def handle(self, *args, **options):
        now = timezone.now()
        usage = AIUsageDay.objects.filter(day=now.date()).first()
        self.stdout.write(f'Día UTC: {now.date()}')
        self.stdout.write(f'Solicitudes reservadas: {usage.requests if usage else 0} / {settings.AI_DAILY_REQUEST_LIMIT} (0 = sin límite)')
        self.stdout.write(f'Tokens reservados: {usage.reserved_tokens if usage else 0} / {settings.AI_DAILY_TOKEN_LIMIT} (0 = sin límite)')
        self.stdout.write(f'Consultas IA activas: {UsageLease.objects.filter(scope="ai:global", expires_at__gt=now).count()}')
        self.stdout.write('Son reservas conservadoras, no facturación real de OpenRouter.')
