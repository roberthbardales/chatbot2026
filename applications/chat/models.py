import uuid

from django.conf import settings
from django.db import models
from pgvector.django import VectorField

from .providers import OPENROUTER, PROVIDER_CHOICES

BLOG = 'blog'
DUMMYJSON = 'dummyjson'
GOOGLE_SHEETS = 'google_sheets'
SOURCE_CHOICES = (
    (BLOG, 'Artículos del blog'),
    (DUMMYJSON, 'Productos DummyJSON'),
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


class Message(models.Model):
    USER = 'user'
    ASSISTANT = 'assistant'
    ROLE_CHOICES = ((USER, 'Usuario'), (ASSISTANT, 'Asistente'))

    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name='messages')
    role = models.CharField(max_length=10, choices=ROLE_CHOICES)
    content = models.TextField()
    provider = models.CharField(max_length=20, choices=PROVIDER_CHOICES, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ('created_at', 'pk')


class ProductDocument(models.Model):
    dummyjson_id = models.PositiveIntegerField(unique=True)
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    category = models.CharField(max_length=120, blank=True)
    brand = models.CharField(max_length=120, blank=True)
    sku = models.CharField(max_length=120, blank=True)
    price = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    stock = models.IntegerField(null=True, blank=True)
    content = models.TextField()
    content_hash = models.CharField(max_length=64)
    embedding = VectorField(dimensions=768)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ('dummyjson_id',)


class Product(models.Model):
    sku = models.CharField(max_length=120, unique=True)
    name = models.CharField(max_length=255)
    category = models.CharField(max_length=120, blank=True)
    brand = models.CharField(max_length=120, blank=True)
    model = models.CharField(max_length=120, blank=True)
    specs = models.TextField(blank=True)
    price = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    stock = models.IntegerField(null=True, blank=True)
    unit = models.CharField(max_length=80, blank=True)
    keywords = models.TextField(blank=True)
    description = models.TextField(blank=True)
    active = models.BooleanField(default=True)
    content_hash = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ('name', 'sku')


class Combo(models.Model):
    name = models.CharField(max_length=255, unique=True)
    description = models.TextField(blank=True)
    product_skus = models.TextField(blank=True)
    price = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    active = models.BooleanField(default=True)
    content_hash = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ('name',)
