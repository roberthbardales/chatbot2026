from django.urls import path

from .views import (change_mode, connection_status, conversation_messages,
                    conversations, dashboard, delete_conversation,
                    send_manual_message, webhook)


urlpatterns = [
    path('', dashboard, name='dashboard'),
    path('webhook/', webhook, name='webhook'),
    path('api/connection/status', connection_status, name='connection-status'),
    path('api/conversations', conversations, name='conversations'),
    path('api/messages/<int:contact_id>', conversation_messages, name='conversation-messages'),
    path('api/conversations/<int:contact_id>', delete_conversation, name='delete-conversation'),
    path('api/mode/<int:contact_id>', change_mode, name='change-mode'),
    path('api/messages/<int:contact_id>/send', send_manual_message, name='send-manual-message'),
]
