from django.core.management.base import BaseCommand, CommandError

class Command(BaseCommand):
    help = 'Desactivado: el RAG de DummyJSON está deshabilitado.'

    def handle(self, *args, **options):
        raise CommandError('La indexación de DummyJSON está deshabilitada porque el RAG está desactivado.')
