from django.db import models


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

    class Meta:
        ordering = ('created_at', 'id')
