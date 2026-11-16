import json
import logging

from django.core.management.base import BaseCommand, CommandError

from applications.whatsapp.operations import operations_snapshot

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Resumen y alertas operativas de solo lectura; no procesa colas, reintenta ni envía mensajes.'

    def add_arguments(self, parser):
        parser.add_argument('--warnings-as-errors', action='store_true')

    def handle(self, *args, **options):
        snapshot = operations_snapshot()
        # Do not emit audit reasons/identities in monitoring output.
        snapshot.pop('recent_retries', None)
        self.stdout.write(json.dumps(snapshot, ensure_ascii=False))
        failed = False
        for alert in snapshot['alerts']:
            log = logger.error if alert['severity'] == 'critical' else logger.warning
            log('WhatsApp operational alert: code=%s severity=%s', alert['code'], alert['severity'])
            failed = failed or alert['severity'] == 'critical' or options['warnings_as_errors']
        if failed:
            raise CommandError('Hay alertas operativas; consulta el resumen.')
