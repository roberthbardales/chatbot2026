from types import SimpleNamespace

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from applications.chat.models import DUMMYJSON
from applications.chat.providers import GROQ
from applications.chat.services import answer_message

from .meta_api import send_text_message
from .models import WhatsAppContact, WhatsAppMessage


def process_text_message(phone_number, display_name, message_id, text):
    if len(text) > settings.WHATSAPP_MAX_MESSAGE_LENGTH:
        text = text[:settings.WHATSAPP_MAX_MESSAGE_LENGTH]
    with transaction.atomic():
        contact, _ = WhatsAppContact.objects.get_or_create(
            phone_number=phone_number,
            defaults={'display_name': display_name},
        )
        if display_name and contact.display_name != display_name:
            contact.display_name = display_name
            contact.save(update_fields=('display_name', 'updated_at'))
        message, created = WhatsAppMessage.objects.get_or_create(
            message_id=message_id,
            defaults={'contact': contact, 'text': text},
        )
    if not created:
        return False

    conversation = SimpleNamespace(
        source=DUMMYJSON,
        provider=settings.WHATSAPP_DEFAULT_PROVIDER or GROQ,
    )
    try:
        response = answer_message(conversation, text)
        send_text_message(phone_number, response)
    except Exception:
        WhatsAppMessage.objects.filter(pk=message.pk).update(status=WhatsAppMessage.FAILED)
        raise
    WhatsAppMessage.objects.filter(pk=message.pk).update(
        response=response,
        status=WhatsAppMessage.PROCESSED,
        processed_at=timezone.now(),
    )
    return True
