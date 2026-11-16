import uuid

from django.db import models
from django.utils import timezone
from django.conf import settings


class WhatsAppContact(models.Model):
    phone_number = models.CharField(max_length=32, unique=True)
    display_name = models.CharField(max_length=255, blank=True)
    is_active = models.BooleanField(default=True)
    HUMAN = 'human'
    AI = 'ai'
    MODE_CHOICES = ((AI, 'IA'), (HUMAN, 'Humano'))
    mode = models.CharField(max_length=10, choices=MODE_CHOICES, default=AI)
    last_message_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


class WorkerHeartbeat(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    started_at = models.DateTimeField(default=timezone.now)
    heartbeat_at = models.DateTimeField()
    last_progress_at = models.DateTimeField()
    last_success_at = models.DateTimeField(null=True, blank=True)
    stopped_at = models.DateTimeField(null=True, blank=True)
    phase = models.CharField(max_length=20, default='starting')
    last_error = models.CharField(max_length=255, blank=True)


class WhatsAppMessage(models.Model):
    RECEIVED = 'received'
    PROCESSED = 'processed'
    FAILED = 'failed'
    PENDING = 'pending'
    PROCESSING = 'processing'
    STATUS_CHOICES = (
        (RECEIVED, 'Recibido'),
        (PROCESSED, 'Procesado'),
        (FAILED, 'Fallido'),
        (PENDING, 'Pendiente'),
        (PROCESSING, 'Procesando'),
    )

    contact = models.ForeignKey(WhatsAppContact, on_delete=models.CASCADE, related_name='messages')
    message_id = models.CharField(max_length=255, unique=True)
    text = models.TextField(blank=True)
    media_id = models.CharField(max_length=255, blank=True)
    media_type = models.CharField(max_length=100, blank=True)
    is_from_customer = models.BooleanField(default=True)
    response = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=RECEIVED)
    created_at = models.DateTimeField(auto_now_add=True)
    processed_at = models.DateTimeField(null=True, blank=True)
    processing_started_at = models.DateTimeField(null=True, blank=True)
    processing_token = models.UUIDField(null=True, blank=True, db_index=True)
    processing_attempts = models.PositiveIntegerField(default=0)
    inventory_context = models.JSONField(default=dict, blank=True)
    retry_after_at = models.DateTimeField(null=True, blank=True, db_index=True)

    class Meta:
        ordering = ('created_at', 'id')


class Outbox(models.Model):
    contact = models.ForeignKey(WhatsAppContact, on_delete=models.CASCADE, related_name='outbox')
    message = models.ForeignKey(WhatsAppMessage, on_delete=models.CASCADE, related_name='outbox_entries')
    content = models.TextField()
    sent = models.BooleanField(default=False)
    attempts = models.PositiveIntegerField(default=0)
    last_error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    locked_at = models.DateTimeField(null=True, blank=True)
    lock_token = models.UUIDField(null=True, blank=True)
    next_attempt_at = models.DateTimeField(null=True, blank=True, db_index=True)
    failed_at = models.DateTimeField(null=True, blank=True)
    group_id = models.UUIDField(default=uuid.uuid4, db_index=True)
    part_number = models.PositiveIntegerField(default=1)
    meta_message_id = models.CharField(max_length=255, unique=True, null=True, blank=True)
    delivery_status = models.CharField(max_length=16, blank=True)
    provider_sent_at = models.DateTimeField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    read_at = models.DateTimeField(null=True, blank=True)
    delivery_failed_at = models.DateTimeField(null=True, blank=True)
    delivery_error = models.CharField(max_length=1000, blank=True)
    manual_retries = models.PositiveIntegerField(default=0)
    last_retry_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ('created_at', 'id')
        constraints = [
            models.UniqueConstraint(fields=('group_id', 'part_number'), name='outbox_unique_group_part'),
        ]


class DeliveryEvent(models.Model):
    fingerprint = models.CharField(max_length=64, unique=True)
    meta_message_id = models.CharField(max_length=255, db_index=True)
    recipient_id = models.CharField(max_length=32)
    phone_number_id = models.CharField(max_length=64)
    status = models.CharField(max_length=16)
    occurred_at = models.DateTimeField()
    received_at = models.DateTimeField(auto_now_add=True)
    error = models.CharField(max_length=1000, blank=True)
    outbox = models.ForeignKey(Outbox, on_delete=models.CASCADE, null=True, blank=True, related_name='delivery_events')
    applied_at = models.DateTimeField(null=True, blank=True, db_index=True)


class OutboxRetryAudit(models.Model):
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    actor_identifier = models.CharField(max_length=64)
    outbox = models.ForeignKey(Outbox, null=True, on_delete=models.SET_NULL, related_name='retry_audits')
    group_id = models.UUIDField(db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    reason = models.CharField(max_length=500)
    snapshot = models.JSONField(default=list)
