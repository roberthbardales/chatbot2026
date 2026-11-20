import logging
import uuid
from datetime import timedelta
from types import SimpleNamespace

from django.conf import settings
from django.db import models, transaction
from django.db.models import Exists, OuterRef
from django.utils import timezone

from applications.core.agent import AgentError, answer_with_agent
from applications.core.providers import OPENROUTER, ProviderError
from applications.core.responses import InventoryAnswer
from applications.core.services import IMAGE_PLACEHOLDER, answer_sheet_message, image_keywords, latest_inventory_context
from applications.core.limits import LimitExceeded, enforce_rates, identity, principal_slot

from .meta_api import MAX_TEXT_LENGTH, WhatsAppAPIError, download_media_data, send_text_message
from .models import Outbox, WhatsAppContact, WhatsAppMessage

logger = logging.getLogger(__name__)
PROCESSING_ERROR = 'No pude procesar tu mensaje en este momento. Por favor, inténtalo nuevamente en unos minutos.'


def _masked_phone(phone_number):
    return f'***{phone_number[-4:]}' if len(phone_number) > 4 else '***'


def enqueue_message(phone_number, display_name, message_id, text='', media_id='', media_type=''):
    text = text or ''
    if len(text) > settings.WHATSAPP_MAX_MESSAGE_LENGTH:
        logger.warning('WhatsApp message truncated: phone=%s length=%s limit=%s',
                       _masked_phone(phone_number), len(text), settings.WHATSAPP_MAX_MESSAGE_LENGTH)
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
        inventory_context=getattr(content, 'inventory_context', {}),
    )


def split_whatsapp_text(text):
    if len(text) <= MAX_TEXT_LENGTH:
        return [text]
    total = 2
    while True:
        capacity = MAX_TEXT_LENGTH - len(f'Parte {total}/{total}\n')
        chunks = []
        start = 0
        while start < len(text):
            end = min(start + capacity, len(text))
            if end < len(text):
                # Keep separators in the payload so reconstructing the text is lossless.
                for separator in ('\n\n', '\n', ' '):
                    position = text.rfind(separator, start, end)
                    if position >= start + capacity // 2:
                        end = position + len(separator)
                        break
            chunks.append(text[start:end])
            start = end
        if len(chunks) == total:
            return [f'Parte {index}/{total}\n{chunk}' for index, chunk in enumerate(chunks, 1)]
        total = len(chunks)


def enqueue_outbox(contact, message, content):
    with transaction.atomic():
        group_id = uuid.uuid4()
        return [Outbox.objects.create(
            contact=contact, message=message, content=part,
            group_id=group_id, part_number=index,
        ) for index, part in enumerate(split_whatsapp_text(content), 1)]


def create_manual_message(contact, text):
    """Mensaje de un operador: se guarda en el historial y se encola en el Outbox (no llama a Meta)."""
    now = timezone.now()
    with transaction.atomic():
        message = WhatsAppMessage.objects.create(
            contact=contact, message_id=f'local-{uuid.uuid4()}', text=text, response=text,
            status=WhatsAppMessage.PROCESSED, is_from_customer=False, is_manual=True, processed_at=now,
        )
        enqueue_outbox(contact, message, text)
        WhatsAppContact.objects.filter(pk=contact.pk).update(last_message_at=now, updated_at=now)
    return message


def _recover_abandoned_messages(progress_callback=None):
    now = timezone.now()
    cutoff = now - timedelta(seconds=max(1, settings.WHATSAPP_PROCESSING_TIMEOUT_SECONDS))
    stale = WhatsAppMessage.objects.filter(
        status=WhatsAppMessage.PROCESSING, is_from_customer=True,
    ).filter(models.Q(processing_started_at__lte=cutoff) | models.Q(processing_started_at__isnull=True))
    groups = list(stale.order_by().values_list('contact_id', 'processing_token').distinct())
    for contact_id, token in groups:
        if progress_callback:
            progress_callback()
        with transaction.atomic():
            group = list(WhatsAppMessage.objects.select_for_update().filter(
                contact_id=contact_id, processing_token=token,
                status=WhatsAppMessage.PROCESSING, is_from_customer=True,
            ).order_by('id'))
            if not group or any(item.processing_started_at and item.processing_started_at > cutoff for item in group):
                continue
            exhausted = any(item.processing_attempts >= max(1, settings.WHATSAPP_PROCESSING_MAX_ATTEMPTS) for item in group)
            if exhausted:
                # Tell the customer instead of failing silently.
                _finish_processing(group, token, PROCESSING_ERROR, failed=True)
            else:
                WhatsAppMessage.objects.filter(pk__in=[item.pk for item in group]).update(
                    status=WhatsAppMessage.PENDING,
                    processing_started_at=None, processing_token=None,
                    processed_at=None,
                )
            logger.warning('WhatsApp abandoned processing recovered: contact_id=%s messages=%s exhausted=%s',
                           contact_id, len(group), exhausted)


def _finish_processing(messages, token, response=None, failed=False):
    """Commit only while this attempt still owns every message in the batch."""
    with transaction.atomic():
        contact = WhatsAppContact.objects.select_for_update().filter(pk=messages[0].contact_id).first()
        if contact is None:
            return False
        owned = list(WhatsAppMessage.objects.select_for_update().filter(
            pk__in=[item.pk for item in messages],
            status=WhatsAppMessage.PROCESSING, processing_token=token,
        ).order_by('id'))
        if len(owned) != len(messages):
            return False
        if contact.mode == WhatsAppContact.HUMAN:
            # Un operador tomó el control mientras se procesaba: no se responde ni se avisa del error.
            response = None
            failed = False
        WhatsAppMessage.objects.filter(pk__in=[item.pk for item in owned]).update(
            status=WhatsAppMessage.FAILED if failed else WhatsAppMessage.PROCESSED,
            response=response or '', processed_at=timezone.now(),
            processing_started_at=None, processing_token=None,
            retry_after_at=None,
        )
        if response is not None:
            if failed:
                # An error reply must not erase the options the customer was choosing from.
                response = InventoryAnswer(response, latest_inventory_context(contact.messages))
            assistant = _create_assistant_message(contact, response)
            enqueue_outbox(contact, assistant, response)
            WhatsAppContact.objects.filter(pk=contact.pk).update(last_message_at=timezone.now(), updated_at=timezone.now())
    return True


def _defer_processing(messages, token, retry_after):
    with transaction.atomic():
        owned = list(WhatsAppMessage.objects.select_for_update().filter(
            pk__in=[message.pk for message in messages],
            status=WhatsAppMessage.PROCESSING, processing_token=token,
        ).order_by('id'))
        if len(owned) != len(messages):
            return False
        WhatsAppMessage.objects.filter(pk__in=[message.pk for message in owned]).update(
            status=WhatsAppMessage.PENDING, processing_token=None, processing_started_at=None,
            retry_after_at=timezone.now() + timedelta(seconds=retry_after),
            processing_attempts=models.F('processing_attempts') - 1,
        )
    return True


def _human_mode(contact_pk):
    return WhatsAppContact.objects.filter(pk=contact_pk, mode=WhatsAppContact.HUMAN).exists()


def _agent_history(contact, batch):
    """Últimos mensajes del contacto anteriores al lote actual, como {'role', 'content'} en orden cronológico.

    Las respuestas de un operador cuentan como del asistente. No incluye mensajes aún pendientes ni los avisos de error."""
    limit = max(0, settings.AI_HISTORY_MESSAGES)
    if not limit or not batch:
        return []
    # Se ordena por `pk` (orden de inserción) y no por `created_at`: dos mensajes seguidos pueden compartir
    # la misma marca de tiempo si el reloj tiene poca resolución.
    rows = WhatsAppMessage.objects.filter(
        contact=contact, pk__lt=min(item.pk for item in batch),
    ).exclude(pk__in=[item.pk for item in batch]).exclude(
        status__in=(WhatsAppMessage.PENDING, WhatsAppMessage.PROCESSING),
    ).exclude(is_from_customer=False, text=PROCESSING_ERROR).order_by('-pk')[:limit]
    history = []
    for row in reversed(list(rows)):
        content = row.text or ('[El cliente envió una imagen]' if row.media_id else '')
        if content:
            history.append({'role': 'user' if row.is_from_customer else 'assistant', 'content': content})
    return history


def _generate_response(contact, batch, text, image_data_url):
    """Responde con el agente si está activado y, si falla o no puede responder, con el motor de plantillas."""
    provider = settings.WHATSAPP_DEFAULT_PROVIDER or OPENROUTER
    conversation = SimpleNamespace(provider=provider, messages=contact.messages)
    if settings.AI_AGENT_ENABLED:
        try:
            agent_text, keywords = text, None
            if image_data_url:
                found = (image_keywords(provider, image_data_url) or '').strip()
                keywords = None if found.lower() == 'desconocido' else found
                if text == IMAGE_PLACEHOLDER:
                    agent_text = '' if keywords else 'El cliente envió una imagen que no se pudo identificar.'
            return answer_with_agent(conversation, agent_text, _agent_history(contact, batch), keywords)
        except LimitExceeded:
            raise
        except AgentError as exc:
            logger.warning('Agent could not answer, using the template engine: %s', exc)
        except Exception:
            logger.exception('Unexpected agent error, using the template engine')
    return answer_sheet_message(conversation, text, image_data_url=image_data_url)


def process_pending_messages(force=False, max_contacts=None, progress_callback=None, after_contact=None):
    _recover_abandoned_messages(progress_callback=progress_callback)
    cutoff = timezone.now() if force else timezone.now() - timedelta(seconds=settings.MESSAGE_DEBOUNCE_SECONDS)
    pending = WhatsAppMessage.objects.filter(
        status=WhatsAppMessage.PENDING,
        is_from_customer=True,
        created_at__lte=cutoff,
    ).filter(models.Q(retry_after_at__isnull=True) | models.Q(retry_after_at__lte=timezone.now())).select_related('contact').order_by('contact_id', 'created_at', 'id')
    processed_contacts = set()
    count = 0
    for message in pending:
        if message.contact_id in processed_contacts:
            continue
        if max_contacts is not None and len(processed_contacts) >= max_contacts:
            break
        if processed_contacts and after_contact:
            # Let replies already generated leave before the next (slow) AI call.
            after_contact()
        if progress_callback:
            progress_callback()
        processed_contacts.add(message.contact_id)
        token = uuid.uuid4()
        with transaction.atomic():
            claimable = list(WhatsAppMessage.objects.select_for_update(skip_locked=True).filter(
                contact=message.contact,
                status=WhatsAppMessage.PENDING,
                is_from_customer=True,
                created_at__lte=cutoff,
            ).filter(models.Q(retry_after_at__isnull=True) | models.Q(retry_after_at__lte=timezone.now())).order_by('created_at', 'id'))
            if claimable:
                WhatsAppMessage.objects.filter(pk__in=[item.pk for item in claimable]).update(
                    status=WhatsAppMessage.PROCESSING,
                    processing_started_at=timezone.now(), processing_token=token,
                    processing_attempts=models.F('processing_attempts') + 1,
                )
        messages = claimable
        if not messages:
            continue
        if _human_mode(message.contact_id):
            # En modo humano el bot no responde ni consume frecuencia: el mensaje solo queda registrado.
            count += int(_finish_processing(messages, token))
            continue
        try:
            enforce_rates([(identity('whatsapp', message.contact.phone_number), settings.WHATSAPP_RATE_PER_CONTACT)])
        except LimitExceeded as exc:
            _defer_processing(messages, token, exc.retry_after)
            continue
        media_message = next((item for item in reversed(messages) if item.media_id), None)
        text = '\n'.join(item.text for item in messages if item.text).strip() or 'El usuario envió una imagen.'
        image_data_url = None
        if media_message:
            try:
                image_data_url = download_media_data(media_message.media_id)
            except Exception:
                logger.exception('Could not download WhatsApp media for %s', _masked_phone(message.contact.phone_number))
                count += int(_finish_processing(messages, token, PROCESSING_ERROR, failed=True))
                continue
        # El modo pudo cambiar mientras se descargaba la imagen.
        if _human_mode(message.contact_id):
            count += int(_finish_processing(messages, token))
            continue
        try:
            with principal_slot(identity('whatsapp', message.contact.phone_number)):
                response = _generate_response(message.contact, messages, text, image_data_url)
        except LimitExceeded as exc:
            if exc.code == 'context_limit':
                count += int(_finish_processing(messages, token, PROCESSING_ERROR, failed=True))
            else:
                _defer_processing(messages, token, exc.retry_after)
            continue
        except ProviderError:
            logger.exception('OpenRouter failed for %s', _masked_phone(message.contact.phone_number))
            count += int(_finish_processing(messages, token, PROCESSING_ERROR, failed=True))
            continue
        except Exception:
            logger.exception('Unexpected WhatsApp processing error for %s', _masked_phone(message.contact.phone_number))
            count += int(_finish_processing(messages, token, PROCESSING_ERROR, failed=True))
            continue
        # Committed outside the AI try block: a failure here must not be retried as an AI error.
        count += int(_finish_processing(messages, token, response))
    return count


def _outbox_unlocked(now):
    stale_lock = now - timedelta(seconds=max(1, settings.WHATSAPP_OUTBOX_LOCK_SECONDS))
    return models.Q(locked_at__isnull=True) | models.Q(locked_at__lte=stale_lock)


def _earlier_parts(**filters):
    return Outbox.objects.filter(
        group_id=OuterRef('group_id'), part_number__lt=OuterRef('part_number'), **filters)


def _outbox_eligible(now, max_attempts):
    # Una parte espera a que las anteriores de su grupo hayan sido aceptadas por Meta.
    return Outbox.objects.filter(
        sent=False, failed_at__isnull=True, attempts__lt=max_attempts,
    ).filter(_outbox_unlocked(now)).filter(
        models.Q(next_attempt_at__isnull=True) | models.Q(next_attempt_at__lte=now)
    ).exclude(Exists(_earlier_parts(sent=False)))


def _meta_message_id(result):
    """ID del mensaje que devuelve Meta al aceptar el envío ('' si la respuesta no lo trae)."""
    try:
        value = result['messages'][0]['id']
    except (KeyError, IndexError, TypeError):
        return ''
    return value if isinstance(value, str) else ''


def _finish_outbox(item, token, error=None, result=None):
    now = timezone.now()
    values = {'locked_at': None, 'lock_token': None, 'next_attempt_at': None}
    if error is None:
        values.update(sent=True, sent_at=now, last_error='', meta_message_id=_meta_message_id(result),
                      delivery_status='sent', delivery_updated_at=now, delivery_error='')
    else:
        values['last_error'] = error.diagnostic()[:1000]
        if not error.retryable or item.attempts >= max(1, settings.WHATSAPP_OUTBOX_MAX_ATTEMPTS):
            values['failed_at'] = now
        else:
            base = max(1, settings.WHATSAPP_OUTBOX_RETRY_BASE_SECONDS)
            maximum = max(1, settings.WHATSAPP_OUTBOX_RETRY_MAX_SECONDS)
            delay = min(maximum, base * (2 ** min(max(0, item.attempts - 1), 30)))
            values['next_attempt_at'] = now + timedelta(seconds=delay)
    return Outbox.objects.filter(pk=item.pk, sent=False, failed_at__isnull=True, lock_token=token).update(**values) == 1


def process_outbox(progress_callback=None):
    count = 0
    now = timezone.now()
    max_attempts = max(1, settings.WHATSAPP_OUTBOX_MAX_ATTEMPTS)
    # Include legacy failures and attempts interrupted after claiming. Never steal an active lock.
    exhausted = Outbox.objects.filter(sent=False, failed_at__isnull=True, attempts__gte=max_attempts).filter(
        _outbox_unlocked(now)
    ).update(failed_at=now, locked_at=None, lock_token=None, next_attempt_at=None)
    if exhausted:
        logger.warning('WhatsApp outbox attempts exhausted: count=%s', exhausted)
    # Si una parte falló para siempre, las siguientes del grupo no se envían fuera de orden.
    orphaned = Outbox.objects.filter(sent=False, failed_at__isnull=True).filter(
        Exists(_earlier_parts(failed_at__isnull=False))
    ).update(failed_at=now, locked_at=None, lock_token=None, next_attempt_at=None,
             last_error='[permanent] No se envía: una parte anterior del mismo mensaje falló.')
    if orphaned:
        logger.warning('WhatsApp outbox parts discarded after an earlier part failed: count=%s', orphaned)
    # Re-evaluate after each send so later parts can advance in this same invocation.
    visited = set()
    for _ in range(20):
        if progress_callback:
            progress_callback()
        candidate = _outbox_eligible(timezone.now(), max_attempts).exclude(pk__in=visited).order_by('created_at', 'id').first()
        if candidate is None:
            break
        visited.add(candidate.pk)
        token = uuid.uuid4()
        with transaction.atomic():
            item = _outbox_eligible(timezone.now(), max_attempts).select_for_update(skip_locked=True).filter(
                pk=candidate.pk,
            ).select_related('contact').first()
            if not item:
                continue
            if len(item.content) > MAX_TEXT_LENGTH:
                parts = split_whatsapp_text(item.content)
                item.content = parts[0]
                item.save(update_fields=('content',))
                for index, part in enumerate(parts[1:], item.part_number + 1):
                    Outbox.objects.create(contact_id=item.contact_id, message_id=item.message_id,
                                          content=part, group_id=item.group_id, part_number=index)
            item.locked_at = timezone.now()
            item.lock_token = token
            # Count the claim so a crash cannot create unlimited uncounted attempts.
            item.attempts += 1
            item.save(update_fields=('locked_at', 'lock_token', 'attempts'))
        try:
            result = send_text_message(item.contact.phone_number, item.content)
        except WhatsAppAPIError as exc:
            if _finish_outbox(item, token, exc):
                logger.warning('Queued WhatsApp send failed: outbox_id=%s attempt=%s error=%s',
                               item.pk, item.attempts, exc.diagnostic())
            continue
        count += int(_finish_outbox(item, token, result=result))
    return count


_DELIVERY_RANK = {'sent': 1, 'delivered': 2, 'read': 3}


def record_delivery_status(meta_message_id, status, errors=None):
    """Guarda el estado de entrega que Meta notifica por el webhook.

    Solo avanza (sent -> delivered -> read); `failed` se registra salvo que ya esté leído.
    Devuelve True si actualizó alguna fila.
    """
    if not meta_message_id or not isinstance(meta_message_id, str) or status not in {*_DELIVERY_RANK, 'failed'}:
        return False
    error = ''
    if status == 'failed':
        first = errors[0] if isinstance(errors, list) and errors and isinstance(errors[0], dict) else {}
        error = f"{first.get('code', '')} {first.get('title') or first.get('message') or ''}".strip()[:500]
    with transaction.atomic():
        items = list(Outbox.objects.select_for_update().filter(meta_message_id=meta_message_id))
        updated = 0
        for item in items:
            current = _DELIVERY_RANK.get(item.delivery_status, 0)
            if item.delivery_status == 'read' or (status != 'failed' and _DELIVERY_RANK[status] <= current):
                continue
            item.delivery_status = status
            item.delivery_updated_at = timezone.now()
            item.delivery_error = error
            item.save(update_fields=('delivery_status', 'delivery_updated_at', 'delivery_error'))
            updated += 1
    if status == 'failed' and updated:
        logger.warning('WhatsApp delivery failed: outbox_message=%s error=%s', meta_message_id[-8:], error)
    return bool(updated)
