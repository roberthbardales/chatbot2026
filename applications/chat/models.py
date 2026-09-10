import uuid

from django.conf import settings
from django.db import models

from .providers import GROQ, PROVIDER_CHOICES

BLOG = 'blog'
DUMMYJSON = 'dummyjson'
SOURCE_CHOICES = ((BLOG, 'Artículos del blog'), (DUMMYJSON, 'Productos'))


class Conversation(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='chat_conversations')
    provider = models.CharField(max_length=20, choices=PROVIDER_CHOICES, default=GROQ)
    source = models.CharField(max_length=20, choices=SOURCE_CHOICES, default=DUMMYJSON)
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
