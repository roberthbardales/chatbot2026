from django.urls import path

from . import views


urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('webhook/', views.webhook, name='webhook'),
    path('api/status', views.system_status, name='system-status'),
    path('api/conversations', views.conversations, name='conversations'),
    path('api/messages/<int:contact_id>', views.conversation_messages, name='conversation-messages'),
    path('api/mode/<int:contact_id>', views.change_mode, name='change-mode'),
    path('api/messages/<int:contact_id>/send', views.send_manual_message, name='send-manual-message'),
]
