from django.urls import path
from . import views

app_name = 'app_users'

urlpatterns = [
    path('register/', views.UserRegisterView.as_view(), name='register'),
    path('login/', views.LoginUser.as_view(), name='login'),
    path('logout/', views.LogoutView.as_view(), name='logout'),
    path('update/', views.UpdatePasswordView.as_view(), name='user-update'),
    path('lista/', views.ListaUsuariosView.as_view(), name='lista'),
    path('', views.DashboardView.as_view(), name='dashboard'),
]