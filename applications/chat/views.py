from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.core.exceptions import ValidationError
from django.db import transaction
from django.views import View
from django.views.generic import TemplateView
from django.conf import settings

from .models import BLOG, DUMMYJSON, GOOGLE_SHEETS, SOURCE_CHOICES, Conversation, Message
from .providers import OPENROUTER, PROVIDER_CHOICES
from .services import answer_message


def _anonymous_conversation_ids(request):
    return set(request.session.get('anonymous_chat_conversations', []))


def _remember_anonymous_conversation(request, conversation):
    conversation_ids = list(_anonymous_conversation_ids(request))
    conversation_ids.append(str(conversation.id))
    request.session['anonymous_chat_conversations'] = conversation_ids[-20:]


class ChatView(TemplateView):
    template_name = 'chat/chat.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        conversation_id = self.request.GET.get('conversation')
        conversation = None
        if conversation_id:
            if self.request.user.is_authenticated:
                conversation = get_object_or_404(
                    Conversation, id=conversation_id, user=self.request.user
                )
            elif conversation_id in _anonymous_conversation_ids(self.request):
                conversation = get_object_or_404(
                    Conversation, id=conversation_id, user__isnull=True
                )
        context.update({
            'conversation': conversation,
            'chat_messages': conversation.messages.all() if conversation else [],
            'provider_choices': PROVIDER_CHOICES,
            'default_provider': conversation.provider if conversation else OPENROUTER,
            'source_choices': (
                ((BLOG, 'Artículos del blog'), (DUMMYJSON, 'Productos DummyJSON'), (GOOGLE_SHEETS, 'Inventario'))
                if settings.BLOG_SOURCE_ENABLED
                else ((DUMMYJSON, 'Productos DummyJSON'), (GOOGLE_SHEETS, 'Inventario'))
            ),
            'default_source': (
                conversation.source if conversation and conversation.source in {DUMMYJSON, GOOGLE_SHEETS}
                else GOOGLE_SHEETS
            ),
        })
        return context


class SendMessageView(View):
    def post(self, request, *args, **kwargs):
        text = request.POST.get('message', '').strip()
        return self._send_message(request, text)

    def _send_message(self, request, text):
        if not text:
            return JsonResponse({'error': 'Escribe un mensaje antes de enviarlo.'}, status=400)
        if len(text) > settings.CHAT_MAX_MESSAGE_LENGTH:
            return JsonResponse({'error': 'El mensaje es demasiado largo.'}, status=400)
        conversation_id = request.POST.get('conversation')
        if conversation_id:
            try:
                if request.user.is_authenticated:
                    conversation = get_object_or_404(Conversation, id=conversation_id, user=request.user)
                elif conversation_id in _anonymous_conversation_ids(request):
                    conversation = get_object_or_404(Conversation, id=conversation_id, user__isnull=True)
                else:
                    return JsonResponse({'error': 'La conversación indicada no es válida.'}, status=404)
            except (ValueError, TypeError, ValidationError):
                return JsonResponse({'error': 'La conversación indicada no es válida.'}, status=404)
        else:
            provider = request.POST.get('provider', OPENROUTER)
            source = request.POST.get('source', GOOGLE_SHEETS)
            valid_providers = {value for value, _ in PROVIDER_CHOICES}
            valid_sources = {DUMMYJSON, GOOGLE_SHEETS}
            if settings.BLOG_SOURCE_ENABLED:
                valid_sources.add(BLOG)
            if provider not in valid_providers:
                return JsonResponse({'error': 'El modelo seleccionado no es válido.'}, status=400)
            if source not in valid_sources:
                return JsonResponse({'error': 'La fuente seleccionada no es válida.'}, status=400)
            conversation = Conversation.objects.create(
                user=request.user if request.user.is_authenticated else None,
                provider=provider,
                source=source,
            )
            if not request.user.is_authenticated:
                _remember_anonymous_conversation(request, conversation)
        if conversation_id:
            source = request.POST.get('source', conversation.source)
            valid_sources = {DUMMYJSON, GOOGLE_SHEETS}
            if settings.BLOG_SOURCE_ENABLED:
                valid_sources.add(BLOG)
            if source not in valid_sources:
                return JsonResponse({'error': 'La fuente seleccionada no es válida.'}, status=400)
            if source != conversation.source:
                conversation.source = source
                conversation.save(update_fields=['source'])
        try:
            with transaction.atomic():
                Message.objects.create(conversation=conversation, role=Message.USER, content=text)
                response = answer_message(conversation, text)
                Message.objects.create(conversation=conversation, role=Message.ASSISTANT, content=response, provider=conversation.provider)
                Conversation.objects.filter(pk=conversation.pk).update(updated_at=timezone.now())
        except Exception:
            return JsonResponse({'error': 'No se pudo procesar el mensaje. Inténtalo nuevamente más tarde.'}, status=503)
        return JsonResponse({'conversation': str(conversation.id), 'response': response})
