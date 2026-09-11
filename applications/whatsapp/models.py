from django.db import models


class WhatsAppContact(models.Model):
    phone_number = models.CharField(max_length=32, unique=True)
    display_name = models.CharField(max_length=255, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


class WhatsAppMessage(models.Model):
    RECEIVED = 'received'
    PROCESSED = 'processed'
    FAILED = 'failed'
    STATUS_CHOICES = (
        (RECEIVED, 'Recibido'),
        (PROCESSED, 'Procesado'),
        (FAILED, 'Fallido'),
    )

    contact = models.ForeignKey(WhatsAppContact, on_delete=models.CASCADE, related_name='messages')
    message_id = models.CharField(max_length=255, unique=True)
    text = models.TextField()
    response = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=RECEIVED)
    created_at = models.DateTimeField(auto_now_add=True)
    processed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ('created_at', 'id')
