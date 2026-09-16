from django.core.management.base import BaseCommand, CommandError

from applications.chat.inventory import InventoryError, sync_inventory


class Command(BaseCommand):
    help = 'Sincroniza productos y combos desde Google Sheets.'

    def handle(self, *args, **options):
        try:
            products, combos = sync_inventory()
        except InventoryError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(self.style.SUCCESS(
            f'Inventario sincronizado: {products} productos y {combos} combos.'
        ))
