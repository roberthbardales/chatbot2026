from django.urls import path

from . import views

app_name = 'chat'

urlpatterns = [
    path('', views.ChatView.as_view(), name='index'),
    path('send/', views.SendMessageView.as_view(), name='send'),
]
