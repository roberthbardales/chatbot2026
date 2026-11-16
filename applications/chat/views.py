from uuid import UUID

from django.http import Http404, JsonResponse
from django.utils import timezone
from django.db import transaction
from django.views import View
from django.views.generic import TemplateView
from django.conf import settings

from .models import Conversation, Message
from .providers import OPENROUTER, PROVIDER_CHOICES
from .services import answer_message
from .limits import LimitExceeded, admit_web_request, principal_slot


def _anonymous_conversation_ids(request):
    return set(request.session.get('anonymous_chat_conversations', []))


def _remember_anonymous_conversation(request, conversation):
    conversation_ids = list(_anonymous_conversation_ids(request))
    conversation_ids.append(str(conversation.id))
    request.session['anonymous_chat_conversations'] = conversation_ids[-20:]


def _get_accessible_conversation(request, conversation_id):
    try:
        conversation_uuid = UUID(conversation_id)
    except (ValueError, TypeError, AttributeError):
        raise Http404('La conversación indicada no es válida.') from None
    if request.user.is_authenticated:
        conversations = Conversation.objects.filter(user=request.user)
    else:
        if str(conversation_uuid) not in _anonymous_conversation_ids(request):
            raise Http404('La conversación indicada no es válida.')
        conversations = Conversation.objects.filter(user__isnull=True)
    conversation = conversations.filter(pk=conversation_uuid).first()
    if conversation is None:
        raise Http404('La conversación indicada no es válida.')
    return conversation


class ChatView(TemplateView):
    template_name = 'chat/chat.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        conversation_id = self.request.GET.get('conversation')
        conversation = None
        if conversation_id:
            conversation = _get_accessible_conversation(self.request, conversation_id)
        context.update({
            'conversation': conversation,
            'chat_messages': conversation.messages.all() if conversation else [],
            'provider_choices': PROVIDER_CHOICES,
            'default_provider': conversation.provider if conversation else OPENROUTER,
        })
        return context


class SendMessageView(View):
    def post(self, request, *args, **kwargs):
        text = request.POST.get('message', '').strip()
        if not text or len(text) > settings.CHAT_MAX_MESSAGE_LENGTH:
            return self._send_message(request, text)
        try:
            principal = admit_web_request(request)
            with principal_slot(principal):
                return self._send_message(request, text)
        except LimitExceeded as exc:
            response = JsonResponse({'error': str(exc), 'retry_after': exc.retry_after, 'code': exc.code}, status=429)
            response['Retry-After'] = str(exc.retry_after)
            response['Cache-Control'] = 'no-store'
            return response

    def _send_message(self, request, text):
        if not text:
            return JsonResponse({'error': 'Escribe un mensaje antes de enviarlo.'}, status=400)
        if len(text) > settings.CHAT_MAX_MESSAGE_LENGTH:
            return JsonResponse({'error': 'El mensaje es demasiado largo.'}, status=400)
        conversation_id = request.POST.get('conversation')
        if conversation_id:
            try:
                conversation = _get_accessible_conversation(request, conversation_id)
            except Http404:
                return JsonResponse({'error': 'La conversación indicada no es válida.'}, status=404)
        else:
            provider = request.POST.get('provider', OPENROUTER)
            valid_providers = {value for value, _ in PROVIDER_CHOICES}
            if provider not in valid_providers:
                return JsonResponse({'error': 'El modelo seleccionado no es válido.'}, status=400)
            conversation = Conversation(
                user=request.user if request.user.is_authenticated else None,
                provider=provider,
            )
        try:
            # Reserve usage outside the message transaction: a failed call still
            # consumes budget, and external network calls must not hold DB locks.
            response = answer_message(conversation, text)
            with transaction.atomic():
                if not conversation_id:
                    conversation.save(force_insert=True)
                Message.objects.create(conversation=conversation, role=Message.USER, content=text)
                Message.objects.create(conversation=conversation, role=Message.ASSISTANT, content=response,
                                       provider=conversation.provider,
                                       inventory_context=getattr(response, 'inventory_context', {}))
                Conversation.objects.filter(pk=conversation.pk).update(updated_at=timezone.now())
        except LimitExceeded:
            raise
        except Exception:
            return JsonResponse({'error': 'No se pudo procesar el mensaje. Inténtalo nuevamente más tarde.'}, status=503)
        if not conversation_id and not request.user.is_authenticated:
            _remember_anonymous_conversation(request, conversation)
        return JsonResponse({'conversation': str(conversation.id), 'response': response})
