import json

from django.core.management.base import BaseCommand, CommandError

from applications.core.inventory import InventoryError, preview_inventory, sync_inventory
from applications.core.inventory_sync import inventory_status


class Command(BaseCommand):
    help = ('Descarga la hoja de Google, la valida y carga los productos y su stock en la base de datos '
            '(una transacción; no borra filas y bloquea las bajas masivas). Con --dry-run solo muestra los cambios.')

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help='Vista previa: muestra los cambios sin escribir.')
        parser.add_argument('--allow-large-drop', action='store_true',
                            help='Permite desactivar una parte grande del catálogo.')
        parser.add_argument('--json', action='store_true')

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        if dry_run and options['allow_large_drop']:
            raise CommandError('--allow-large-drop no se usa con --dry-run.')
        try:
            if dry_run:
                summary = preview_inventory().summary
                status = 'preview'
            else:
                summary = sync_inventory(allow_large_drop=options['allow_large_drop']).summary
                status = 'applied'
        except InventoryError as exc:
            raise CommandError(str(exc)) from exc
        if options['json']:
            self.stdout.write(json.dumps({'status': status, **summary}, ensure_ascii=False))
            return
        self.stdout.write('VISTA PREVIA: no se ha modificado el catálogo.' if dry_run else 'APLICADO')
        for label, key in [('Productos', 'products')]:
            section = summary[key]
            self.stdout.write(f"{label}: altas={len(section['create'])}, cambios={len(section['update'])}, bajas lógicas={section['deactivated_total']}, sin cambios={section['unchanged']}")
            if section['create']:
                self.stdout.write('  Altas (hasta 10): ' + ', '.join(section['create'][:10]))
            if section['deactivate']:
                self.stdout.write('  Quedarían inactivos (hasta 10): ' + ', '.join(section['deactivate'][:10]))
            for change in section['update'][:10]:
                values = ', '.join(f"{field}: {str(value['before'])[:60]} -> {str(value['after'])[:60]}"
                                   for field, value in change['changes'].items() if field != 'content_hash')
                self.stdout.write(f"  {change['key']}: {values or 'sin cambios visibles'}")
        for warning in summary['warnings']:
            self.stdout.write(self.style.WARNING(warning))
        if dry_run:
            self.stdout.write('Usa --json para ver todos los cambios sin truncar.')
            self.stdout.write('Para cargarlos: python manage.py sync_inventory' +
                              (' --allow-large-drop' if summary['requires_allow_large_drop'] else ''))
        else:
            products = inventory_status()['products']
            self.stdout.write(f"Catálogo: {products['total']} productos ({products['active']} activos).")
