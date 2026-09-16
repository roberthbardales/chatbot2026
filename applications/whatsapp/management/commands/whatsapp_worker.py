import time
import logging
from datetime import timedelta

from django.utils import timezone
from django.conf import settings

from django.core.management import call_command
from django.core.management.base import BaseCommand

from applications.chat.inventory import InventoryError, sync_inventory
from applications.whatsapp.services import process_outbox, process_pending_messages

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Procesa mensajes de WhatsApp y la cola de salida mediante Meta Cloud API.'

    def add_arguments(self, parser):
        parser.add_argument('--once', action='store_true', help='Procesa una iteración y termina.')

    def handle(self, *args, **options):
        self.stdout.write('WhatsApp worker iniciado.')
        last_inventory_sync = None
        last_dummyjson_sync = None
        while True:
            now = timezone.now()
            if last_inventory_sync is None or now - last_inventory_sync >= timedelta(minutes=settings.INVENTORY_SYNC_MINUTES):
                try:
                    products, combos = sync_inventory()
                    self.stdout.write(f'Inventario sincronizado: productos={products}, combos={combos}')
                    last_inventory_sync = now
                except InventoryError:
                    logger.exception('Inventory synchronization failed')
            if last_dummyjson_sync is None or now - last_dummyjson_sync >= timedelta(minutes=settings.DUMMYJSON_SYNC_MINUTES):
                try:
                    call_command('index_dummyjson', verbosity=0)
                    self.stdout.write('DummyJSON sincronizado e indexado.')
                    last_dummyjson_sync = now
                except Exception:
                    logger.exception('DummyJSON synchronization failed')
                    last_dummyjson_sync = now
            try:
                incoming = process_pending_messages()
                outgoing = process_outbox()
            except Exception:
                logger.exception('WhatsApp worker iteration failed')
                incoming = outgoing = 0
            if incoming or outgoing:
                self.stdout.write(f'Procesados: entrada={incoming}, salida={outgoing}')
            if options['once']:
                return
            time.sleep(2)
