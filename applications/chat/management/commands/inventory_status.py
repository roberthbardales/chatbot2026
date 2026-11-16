import json

from django.core.management.base import BaseCommand

from applications.chat.inventory_sync import inventory_status


class Command(BaseCommand):
    help = 'Estado de sincronización y conteos locales; no descarga CSV ni modifica el catálogo.'

    def handle(self, *args, **options):
        self.stdout.write(json.dumps(inventory_status(), ensure_ascii=False))
