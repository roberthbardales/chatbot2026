from django.urls import path

from .views import (change_mode, connection_status, conversation_messages,
                    conversations, dashboard, delete_conversation,
                     send_manual_message, webhook, message_media, operations_summary,
                     outbox_failures, retry_outbox)


urlpatterns = [
    path('', dashboard, name='dashboard'),
    path('webhook/', webhook, name='webhook'),
    path('api/connection/status', connection_status, name='connection-status'),
    path('api/operations', operations_summary, name='operations-summary'),
    path('api/outbox/failures', outbox_failures, name='outbox-failures'),
    path('api/outbox/<int:outbox_id>/retry', retry_outbox, name='retry-outbox'),
    path('api/conversations', conversations, name='conversations'),
    path('api/messages/<int:contact_id>', conversation_messages, name='conversation-messages'),
    path('api/conversations/<int:contact_id>', delete_conversation, name='delete-conversation'),
    path('api/mode/<int:contact_id>', change_mode, name='change-mode'),
    path('api/messages/<int:contact_id>/send', send_manual_message, name='send-manual-message'),
    path('api/messages/<int:contact_id>/<int:message_pk>/media', message_media, name='message-media'),
]
