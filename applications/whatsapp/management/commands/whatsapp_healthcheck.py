import json
import logging

from django.core.management.base import BaseCommand, CommandError

from applications.whatsapp.health import worker_health

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Comprueba el worker sin procesar colas, enviar mensajes ni sincronizar inventario.'

    def handle(self, *args, **options):
        health = worker_health()
        self.stdout.write(json.dumps(health, ensure_ascii=False))
        if not health['healthy']:
            logger.error('WhatsApp worker unhealthy: state=%s phase=%s', health['state'], health['phase'])
            raise CommandError('Worker no saludable: ' + health['state'])
