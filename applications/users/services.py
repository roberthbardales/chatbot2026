from django.contrib.auth import authenticate, login, logout

from .models import User


def create_user(form_data):
    User.objects.create_user(
        form_data['email'],
        form_data['password1'],
        first_name=form_data['first_name'],
        last_name=form_data['last_name'],
        occupation=form_data['occupation'],
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