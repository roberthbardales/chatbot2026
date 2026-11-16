from django.conf import settings
from django.contrib.auth import authenticate, login, logout

from applications.chat.limits import client_address, enforce_rates, identity

from .models import User


def throttle_login(request, email):
    """Limita intentos de acceso por correo y por IP. Lanza LimitExceeded al superarlos."""
    enforce_rates([
        (identity('login-email', (email or '').strip().lower()), settings.AUTH_LOGIN_PER_EMAIL),
        (identity('login-ip', client_address(request)), settings.AUTH_LOGIN_PER_IP),
    ], window=settings.AUTH_RATE_WINDOW_SECONDS)


def throttle_register(request):
    """Limita altas de cuentas por IP. Lanza LimitExceeded al superarlo."""
    enforce_rates([(identity('register-ip', client_address(request)), settings.AUTH_REGISTER_PER_IP)],
                  window=settings.AUTH_RATE_WINDOW_SECONDS)


def create_user(form_data):
    User.objects.create_user(
        form_data['email'],
        form_data['password1'],
        first_name=form_data['first_name'],
        last_name=form_data['last_name'],
        # Los registros públicos nunca pueden crear cuentas administrativas.
        occupation=User.USUARIO,
        gender=form_data['gender'],
        date_birth=form_data['date_birth'],
        phone=form_data.get('phone', ''),
    )


def authenticate_user(request, email, password):
    user = authenticate(request, email=email, password=password)
    if user is not None:
        login(request, user)
    return user


def get_all_users():
    return User.objects.all().order_by('first_name', 'last_name')


def change_password(user, new_password):
    user.set_password(new_password)
    user.save(update_fields=['password'])


def logout_user(request):
    logout(request)
