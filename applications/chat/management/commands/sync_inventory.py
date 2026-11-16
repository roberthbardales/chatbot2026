import json

from django.core.management.base import BaseCommand, CommandError

from applications.chat.inventory import InventoryError, preview_inventory, sync_inventory


class Command(BaseCommand):
    help = 'Vista previa de inventario por defecto. Aplicar requiere --apply --confirm HUELLA; no borra filas.'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true')
        parser.add_argument('--confirm', default=None)
        parser.add_argument('--allow-large-drop', action='store_true')
        parser.add_argument('--json', action='store_true')

    def handle(self, *args, **options):
        if not options['apply'] and (options['confirm'] or options['allow_large_drop']):
            raise CommandError('--confirm y --allow-large-drop requieren --apply.')
        try:
            if options['apply']:
                run = sync_inventory(confirm=options['confirm'], allow_large_drop=options['allow_large_drop'])
                summary = run.summary
                fingerprint = run.fingerprint
                status = 'applied'
            else:
                plan = preview_inventory()
                summary, fingerprint, status = plan.summary, plan.fingerprint, 'preview'
        except InventoryError as exc:
            raise CommandError(str(exc)) from exc
        if options['json']:
            self.stdout.write(json.dumps({'status': status, 'fingerprint': fingerprint, **summary}, ensure_ascii=False))
            return
        self.stdout.write('APLICADO' if status == 'applied' else 'VISTA PREVIA: no se ha modificado el catálogo.')
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
                self.stdout.write(f"  {change['key']}: {values or 'actualización de huella'}")
        for warning in summary['warnings']:
            self.stdout.write(self.style.WARNING(warning))
        self.stdout.write('Huella de revisión: ' + fingerprint)
        if status == 'preview':
            self.stdout.write('Usa --json para revisar todos los cambios sin truncar.')
            self.stdout.write('Para aplicar: python manage.py sync_inventory --apply --confirm ' + fingerprint +
                              (' --allow-large-drop' if summary['requires_allow_large_drop'] else ''))
