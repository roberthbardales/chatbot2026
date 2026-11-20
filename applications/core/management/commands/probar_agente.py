from types import SimpleNamespace

from django.core.management.base import BaseCommand

from applications.core.agent import AgentError, answer_with_agent


class Command(BaseCommand):
    help = ('Chat de prueba del agente en la terminal, con la IA real (OpenRouter) y el catálogo local. '
            'No guarda nada ni envía WhatsApps; cada mensaje usa de 2 a 6 llamadas a la IA.')

    def handle(self, *args, **options):
        conversation = SimpleNamespace(provider='openrouter')
        history = []
        self.stdout.write('Chat del agente. Escribe tu pregunta y pulsa Enter.\n'
                          '  nuevo  -> conversación nueva\n'
                          '  salir  -> termina (o Enter sin escribir nada)\n')
        while True:
            try:
                text = input('Tú:  ').strip()
            except (EOFError, KeyboardInterrupt):
                self.stdout.write('')
                break
            if text.lower() in ('', 'salir', 'exit'):
                break
            if text.lower() == 'nuevo':
                history = []
                self.stdout.write('Bot: (conversación nueva)\n')
                continue
            try:
                answer = str(answer_with_agent(conversation, text, list(history)))
            except AgentError as error:
                self.stdout.write(f'Bot: [el agente falló: {error}]')
                if getattr(error, 'text', ''):
                    self.stdout.write('     Texto rechazado: ' + error.text.replace('\n', '\n     '))
                self.stdout.write('')
                continue
            history += [{'role': 'user', 'content': text}, {'role': 'assistant', 'content': answer}]
            self.stdout.write('Bot: ' + answer.replace('\n', '\n     ') + '\n')
