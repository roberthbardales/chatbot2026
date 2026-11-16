import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone

from .providers import OPENROUTER, PROVIDER_CHOICES

GOOGLE_SHEETS = 'google_sheets'
SOURCE_CHOICES = (
    (GOOGLE_SHEETS, 'Inventario Google Sheets'),
)


class Conversation(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='chat_conversations', null=True, blank=True)
    provider = models.CharField(max_length=20, choices=PROVIDER_CHOICES, default=OPENROUTER)
    source = models.CharField(max_length=20, choices=SOURCE_CHOICES, default=GOOGLE_SHEETS)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ('-updated_at',)


class UsageBucket(models.Model):
    key = models.CharField(max_length=128, unique=True)
    window_started_at = models.DateTimeField()
    count = models.PositiveIntegerField(default=0)


class UsageLease(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    scope = models.CharField(max_length=128, db_index=True)
    expires_at = models.DateTimeField(db_index=True)


class AIUsageDay(models.Model):
    day = models.DateField(unique=True)
    requests = models.PositiveIntegerField(default=0)
    reserved_tokens = models.PositiveBigIntegerField(default=0)
    alerted = models.BooleanField(default=False)


class InventorySyncLock(models.Model):
    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)


class InventorySyncRun(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    started_at = models.DateTimeField(default=timezone.now)
    finished_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=16, default='running')
    fingerprint = models.CharField(max_length=64, blank=True)
    summary = models.JSONField(default=dict)
    error = models.CharField(max_length=1000, blank=True)


class Message(models.Model):
    USER = 'user'
    ASSISTANT = 'assistant'
    ROLE_CHOICES = ((USER, 'Usuario'), (ASSISTANT, 'Asistente'))

    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name='messages')
    role = models.CharField(max_length=10, choices=ROLE_CHOICES)
    content = models.TextField()
    provider = models.CharField(max_length=20, choices=PROVIDER_CHOICES, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    inventory_context = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ('created_at', 'pk')


class Product(models.Model):
    sku = models.CharField(max_length=120, unique=True)
    name = models.CharField(max_length=255)
    category = models.CharField(max_length=120, blank=True)
    brand = models.CharField(max_length=120, blank=True)
    model = models.CharField(max_length=120, blank=True)
    specs = models.TextField(blank=True)
    price = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    stock = models.IntegerField(null=True, blank=True)
    # True/False según la hoja («Disponible» o la cantidad); None si no hay ningún dato.
    available = models.BooleanField(null=True, blank=True)
    unit = models.CharField(max_length=80, blank=True)
    keywords = models.TextField(blank=True)
    description = models.TextField(blank=True)
    active = models.BooleanField(default=True)
    content_hash = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ('name', 'sku')
