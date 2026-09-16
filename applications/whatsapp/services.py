import logging
import uuid
from datetime import timedelta

from django.conf import settings
from django.db import models, transaction
from django.utils import timezone

from applications.chat.models import DUMMYJSON, GOOGLE_SHEETS
from applications.chat.providers import OPENROUTER, ProviderError
from applications.chat.services import answer_message, answer_sheet_message

from .meta_api import WhatsAppAPIError, download_media_data, send_text_message
from .models import Outbox, WhatsAppContact, WhatsAppMessage

logger = logging.getLogger(__name__)
PROCESSING_ERROR = 'No pude procesar tu mensaje en este momento. Por favor, inténtalo nuevamente en unos minutos.'


def _masked_phone(phone_number):
    return f'***{phone_number[-4:]}' if len(phone_number) > 4 else '***'


def enqueue_message(phone_number, display_name, message_id, text='', media_id='', media_type=''):
    text = (text or '')[:settings.WHATSAPP_MAX_MESSAGE_LENGTH]
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
            defaults={
                'contact': contact,
                'text': text,
                'media_id': media_id,
                'media_type': media_type,
                'status': WhatsAppMessage.PENDING,
            },
        )
        if created:
            contact.last_message_at = timezone.now()
            contact.save(update_fields=('last_message_at', 'updated_at'))
    if created:
        logger.info('WhatsApp message queued: phone=%s media=%s', _masked_phone(phone_number), bool(media_id))
    return created


def _create_assistant_message(contact, content):
    return WhatsAppMessage.objects.create(
        contact=contact,
        message_id=f'local-{uuid.uuid4()}',
        text=content,
        response=content,
        status=WhatsAppMessage.PROCESSED,
        is_from_customer=False,
        processed_at=timezone.now(),
    )


def process_pending_messages(force=False):
    cutoff = timezone.now() if force else timezone.now() - timedelta(seconds=settings.MESSAGE_DEBOUNCE_SECONDS)
    pending = WhatsAppMessage.objects.filter(
        status=WhatsAppMessage.PENDING,
        is_from_customer=True,
        created_at__lte=cutoff,
    ).select_related('contact').order_by('contact_id', 'created_at', 'id')
    processed_contacts = set()
    count = 0
    for message in pending:
        if message.contact_id in processed_contacts:
            continue
        processed_contacts.add(message.contact_id)
        with transaction.atomic():
            claimable = list(WhatsAppMessage.objects.select_for_update(skip_locked=True).filter(
                contact=message.contact,
                status=WhatsAppMessage.PENDING,
                is_from_customer=True,
                created_at__lte=cutoff,
            ).order_by('created_at', 'id'))
            if claimable:
                WhatsAppMessage.objects.filter(pk__in=[item.pk for item in claimable]).update(
                    status=WhatsAppMessage.PROCESSING,
                )
        messages = claimable
        if not messages:
            continue
        latest = messages[-1]
        media_message = next((item for item in reversed(messages) if item.media_id), None)
        text = '\n'.join(item.text for item in messages if item.text).strip() or 'El usuario envió una imagen.'
        image_data_url = None
        if media_message:
            try:
                image_data_url = download_media_data(media_message.media_id)
            except WhatsAppAPIError:
                logger.exception('Could not download WhatsApp media for %s', _masked_phone(message.contact.phone_number))
                _send_processing_error(message.contact.phone_number)
                WhatsAppMessage.objects.filter(pk__in=[item.pk for item in messages]).update(
                    status=WhatsAppMessage.FAILED,
                    processed_at=timezone.now(),
                )
                count += 1
                continue
        if message.contact.mode == WhatsAppContact.HUMAN:
            WhatsAppMessage.objects.filter(pk__in=[item.pk for item in messages]).update(
                status=WhatsAppMessage.PROCESSED,
                processed_at=timezone.now(),
            )
            count += 1
            continue
        try:
            from types import SimpleNamespace
            conversation = SimpleNamespace(
                source=settings.WHATSAPP_DEFAULT_SOURCE,
                provider=settings.WHATSAPP_DEFAULT_PROVIDER or OPENROUTER,
                messages=message.contact.messages,
            )
            if conversation.source not in {DUMMYJSON, GOOGLE_SHEETS}:
                raise ValueError('Invalid WhatsApp source configuration')
            if conversation.source == GOOGLE_SHEETS:
                response = answer_sheet_message(conversation, text, image_data_url=image_data_url)
            else:
                response = answer_message(conversation, text)
        except ProviderError:
            logger.exception('OpenRouter failed for %s', _masked_phone(message.contact.phone_number))
            _send_processing_error(message.contact.phone_number)
            WhatsAppMessage.objects.filter(pk__in=[item.pk for item in messages]).update(
                status=WhatsAppMessage.FAILED,
                processed_at=timezone.now(),
            )
            count += 1
            continue
        except Exception:
            logger.exception('Unexpected WhatsApp processing error for %s', _masked_phone(message.contact.phone_number))
            _send_processing_error(message.contact.phone_number)
            WhatsAppMessage.objects.filter(pk__in=[item.pk for item in messages]).update(
                status=WhatsAppMessage.FAILED,
                processed_at=timezone.now(),
            )
            count += 1
            continue
        with transaction.atomic():
            WhatsAppMessage.objects.filter(pk__in=[item.pk for item in messages]).update(
                status=WhatsAppMessage.PROCESSED,
                response=response,
                processed_at=timezone.now(),
            )
            assistant = _create_assistant_message(message.contact, response)
            Outbox.objects.create(contact=message.contact, message=assistant, content=response)
            message.contact.last_message_at = timezone.now()
            message.contact.save(update_fields=('last_message_at', 'updated_at'))
        count += 1
    return count


def _send_processing_error(phone_number):
    try:
        send_text_message(phone_number, PROCESSING_ERROR)
    except WhatsAppAPIError:
        logger.exception('Could not send processing error to %s', _masked_phone(phone_number))


def process_outbox():
    count = 0
    stale_lock = timezone.now() - timedelta(minutes=10)
    candidates = Outbox.objects.filter(sent=False).filter(
        models.Q(locked_at__isnull=True) | models.Q(locked_at__lt=stale_lock)
    ).order_by('created_at', 'id')[:20]
    for candidate in candidates:
        with transaction.atomic():
            item = Outbox.objects.select_for_update(skip_locked=True).filter(
                pk=candidate.pk,
                sent=False,
            ).first()
            if not item or (item.locked_at and item.locked_at >= stale_lock):
                continue
            item.locked_at = timezone.now()
            item.save(update_fields=('locked_at',))
        item.contact = WhatsAppContact.objects.get(pk=item.contact_id)
        try:
            send_text_message(item.contact.phone_number, item.content)
        except WhatsAppAPIError as exc:
            item.attempts += 1
            item.last_error = str(exc)[:1000]
            item.locked_at = None
            item.save(update_fields=('attempts', 'last_error', 'locked_at'))
            logger.exception('Could not send queued WhatsApp message')
            continue
        item.sent = True
        item.sent_at = timezone.now()
        item.attempts += 1
        item.locked_at = None
        item.save(update_fields=('sent', 'sent_at', 'attempts', 'locked_at'))
        count += 1
    return count
