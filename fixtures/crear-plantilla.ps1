param(
    [string]$ProjectName
)

$ErrorActionPreference = 'Stop'

function Write-Utf8Text {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,

        [AllowEmptyString()]
        [Parameter(Mandatory = $true)]
        [string]$Content
    )

    $directory = Split-Path -Parent $Path
    if ($directory -and -not (Test-Path -LiteralPath $directory)) {
        New-Item -ItemType Directory -Path $directory -Force | Out-Null
    }

    $encoding = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($Path, $Content, $encoding)
}

function New-SecretKey {
    # Django usa una clave aleatoria de 50 caracteres con este alfabeto.
    $chars = 'abcdefghijklmnopqrstuvwxyz0123456789!@#$%^&*(-_=+)'
    $bytes = New-Object byte[] 50
    [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    $builder = New-Object System.Text.StringBuilder
    foreach ($byte in $bytes) {
        [void]$builder.Append($chars[$byte % $chars.Length])
    }
    $builder.ToString()
}

function Normalize-ProjectName {
    param([string]$Name)

    $normalized = $Name.Trim()
    if ([string]::IsNullOrWhiteSpace($normalized)) { throw 'Debes indicar un nombre de proyecto valido.' }
    if ($normalized -notmatch '^[A-Za-z_][A-Za-z0-9_]*$') { throw 'El nombre debe ser un identificador Python valido: solo letras, numeros y guion bajo.' }
    return $normalized
}

function Get-PythonCommand {
    $python = Get-Command python -ErrorAction SilentlyContinue

    if ($python) {
        return $python.Source
    }

    $py = Get-Command py -ErrorAction SilentlyContinue

    if ($py) {
        return $py.Source
    }

    Write-Warning "Python no está instalado o no está disponible en PATH."
    return $null
}

if ([string]::IsNullOrWhiteSpace($ProjectName)) {
    $ProjectName = Read-Host 'Nombre del proyecto'
}

$ProjectName = Normalize-ProjectName -Name $ProjectName
$dbName = 'db_' + $ProjectName.ToLower()
$destinationPath = Join-Path (Get-Location).Path $ProjectName

if (Test-Path -LiteralPath $destinationPath) {
    Write-Host ""
    Write-Host "La carpeta ya existe:" -ForegroundColor Yellow
    Write-Host $destinationPath
    Write-Host ""

    $respuesta = Read-Host "¿Deseas sobrescribir los archivos de la plantilla? [S/N]"

    if ($respuesta -notmatch '^[Ss]$') {
        Write-Host "Operación cancelada." -ForegroundColor Red
        exit 1
    }

    Write-Host "Continuando. Los archivos de la plantilla serán creados o actualizados..." -ForegroundColor Yellow
}
else {
    New-Item -ItemType Directory -Path $destinationPath -Force | Out-Null
}

foreach ($dir in @(
    'applications\home\migrations',
    'applications\users\migrations',
    'templates\include',
    'templates\home',
    'templates\users',
    'static',
    'media',
    'fixtures'
)) {
    New-Item -ItemType Directory -Path (Join-Path $destinationPath $dir) -Force | Out-Null
}

$files = @{}

# ------------------------------------------------------------------
# ARCHIVOS BASE DE LA RAIZ
# ------------------------------------------------------------------
$files['applications\__init__.py'] = ""
$files['applications\home\__init__.py'] = ""
$files['applications\users\__init__.py'] = ""
$files['applications\home\migrations\__init__.py'] = ""
$files['applications\users\migrations\__init__.py'] = ""
$files['static\.gitkeep'] = ""

$files['.gitignore'] = @"
venv/
env/
__pycache__/
*.pyc
*.pyo
db.sqlite3
media/
staticfiles/
.env
.vscode/
.idea/
*.log
"@

$files['requirements.txt'] = @"
Django==3.2.25
asgiref==3.11.1
sqlparse==0.4.4
pytz==2024.1
django-environ==0.11.2
Pillow==9.5.0
django-model-utils==5.0.0
psycopg2-binary==2.9.9
"@

$files['manage.py'] = @"
#!/usr/bin/env python
"""Django's command-line utility for administrative tasks."""
import os
import sys

def main():
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', '$ProjectName.settings')
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Couldn't import Django. Are you sure it's installed and "
            "available on your PYTHONPATH environment variable? Did you "
            "forget to activate a virtual environment?"
        ) from exc
    execute_from_command_line(sys.argv)

if __name__ == '__main__':
    main()
"@

$files[".env"] = @"
# -------------------------
# Django
# -------------------------
SECRET_KEY=$((New-SecretKey))
DEBUG=True
ALLOWED_HOSTS=localhost,127.0.0.1

# -------------------------
# Database
# -------------------------
DB_NAME=$dbName
DB_USER=russell
DB_PASSWORD=russell2020
DB_HOST=localhost
DB_PORT=5432
"@

$files['activar.bat'] = "@echo off`r`ncd /d ""%~dp0""`r`ncall venv\Scripts\activate`r`ncmd /k`r`n"
$files['README.md'] = "# $ProjectName`r`n"

# ------------------------------------------------------------------
# PAQUETE DEL PROYECTO
# ------------------------------------------------------------------
$files["$ProjectName\__init__.py"] = ""
$files["$ProjectName\asgi.py"] = @"
import os
from django.core.asgi import get_asgi_application
os.environ.setdefault('DJANGO_SETTINGS_MODULE', '$ProjectName.settings')
application = get_asgi_application()
"@
$files["$ProjectName\wsgi.py"] = @"
import os
from django.core.wsgi import get_wsgi_application
os.environ.setdefault('DJANGO_SETTINGS_MODULE', '$ProjectName.settings')
application = get_wsgi_application()
"@
$files["$ProjectName\settings.py"] = @"
"""
Django settings for $ProjectName project.

Generated by 'django-admin startproject' using Django 3.2.25.
"""

from pathlib import Path
import environ

# --------------------------------------------------
# BASE DIR
# --------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent

# --------------------------------------------------
# django-environ
# --------------------------------------------------
env = environ.Env(
    DEBUG=(bool, False)
)

# Leer archivo .env
environ.Env.read_env(BASE_DIR / '.env')

# --------------------------------------------------
# SECURITY
# --------------------------------------------------
SECRET_KEY = env('SECRET_KEY')

DEBUG = env.bool('DEBUG', default=False)

ALLOWED_HOSTS = env.list('ALLOWED_HOSTS', default=[])

# --------------------------------------------------
# APPLICATIONS
# --------------------------------------------------
INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'applications.users',
    'applications.home',
]

# --------------------------------------------------
# MIDDLEWARE
# --------------------------------------------------
MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

# --------------------------------------------------
# URLS
# --------------------------------------------------
ROOT_URLCONF = '$ProjectName.urls'

# --------------------------------------------------
# TEMPLATES
# --------------------------------------------------
TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

# --------------------------------------------------
# WSGI
# --------------------------------------------------
WSGI_APPLICATION = '$ProjectName.wsgi.application'

# --------------------------------------------------
# DATABASE
# --------------------------------------------------
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql_psycopg2',
        'NAME': env('DB_NAME'),
        'USER': env('DB_USER'),
        'PASSWORD': env('DB_PASSWORD'),
        'HOST': env('DB_HOST', default='localhost'),
        'PORT': env('DB_PORT', default='5432'),
    }
}

# --------------------------------------------------
# PASSWORD VALIDATION
# --------------------------------------------------
AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]

# --------------------------------------------------
# INTERNATIONALIZATION
# --------------------------------------------------
LANGUAGE_CODE = 'es-pe'

TIME_ZONE = 'America/Lima'

USE_I18N = True

USE_L10N = True

USE_TZ = True

# --------------------------------------------------
# STATIC FILES
# --------------------------------------------------
STATIC_URL = '/static/'

STATICFILES_DIRS = [
    BASE_DIR / 'static'
]

FIXTURE_DIRS = [
    BASE_DIR / 'fixtures'
]

STATIC_ROOT = BASE_DIR / 'staticfiles'

# --------------------------------------------------
# MEDIA FILES
# --------------------------------------------------
MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

# --------------------------------------------------
# DEFAULT PRIMARY KEY
# --------------------------------------------------
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

AUTH_USER_MODEL = 'users.User'

LOGIN_URL = 'app_users:login'
LOGIN_REDIRECT_URL = 'app_users:dashboard'
LOGOUT_REDIRECT_URL = 'app_users:login'

# --------------------------------------------------
# SEGURIDAD EN PRODUCCIÓN
# --------------------------------------------------
if not DEBUG:
    SECURE_SSL_REDIRECT = True
    SECURE_HSTS_SECONDS = 31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True
    SECURE_CONTENT_TYPE_NOSNIFF = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    X_FRAME_OPTIONS = 'DENY'
"@
$files["$ProjectName\urls.py"] = @"
from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', include('applications.home.urls')),
    path('users/', include('applications.users.urls')),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
"@

# ------------------------------------------------------------------
# APP HOME
# ------------------------------------------------------------------
$files['applications\home\admin.py'] = @"
from django.contrib import admin

# Register your models here.
"@
$files['applications\home\apps.py'] = @"
from django.apps import AppConfig

class HomeConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'applications.home'
"@
$files['applications\home\models.py'] = @"
from django.db import models

# Create your models here.
"@
$files['applications\home\tests.py'] = @"
from django.test import TestCase

# Create your tests here.
"@
$files['applications\home\urls.py'] = @"
from django.urls import path
from . import views

app_name = 'app_home'

urlpatterns = [
    path('', views.IndexView.as_view(), name='index'),
]
"@
$files['applications\home\views.py'] = @"
from django.views.generic import TemplateView


class IndexView(TemplateView):
    template_name = 'home/index.html'
"@

# ------------------------------------------------------------------
# APP USERS - MODELO
# ------------------------------------------------------------------
$files['applications\users\models.py'] = @"
from django.db import models
from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin
from .managers import UserManager


class User(AbstractBaseUser, PermissionsMixin):
    ADMINISTRADOR = '0'
    USUARIO = '1'
    EMPLEADO = '2'
    OTRO = '3'

    VARON = 'M'
    MUJER = 'F'
    OTROS = 'O'

    OCCUPATION_CHOICES = (
        (ADMINISTRADOR, 'Administrador'),
        (USUARIO, 'Usuario'),
        (EMPLEADO, 'Empleado'),
        (OTRO, 'Otro'),
    )

    GENDER_CHOICES = (
        (VARON, 'Masculino'),
        (MUJER, 'Femenino'),
        (OTROS, 'Otro'),
    )

    email = models.EmailField(unique=True)
    first_name = models.CharField(max_length=50)
    last_name = models.CharField(max_length=50)
    occupation = models.CharField(max_length=3, choices=OCCUPATION_CHOICES, blank=True)
    gender = models.CharField(max_length=1, choices=GENDER_CHOICES, blank=True)
    date_birth = models.DateField(null=True, blank=True)
    phone = models.CharField(max_length=15, blank=True)
    is_staff = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)

    objects = UserManager()

    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = ['first_name', 'last_name']

    def __str__(self):
        return f'{self.get_full_name()} ({self.email})'

    def get_full_name(self):
        return f'{self.first_name} {self.last_name}'.strip()

    def get_short_name(self):
        return self.first_name
"@

$files['applications\users\managers.py'] = @"
from django.contrib.auth.models import BaseUserManager


class UserManager(BaseUserManager):
    def _create_user(self, email, password, **extra_fields):
        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, email, password=None, **extra_fields):
        extra_fields.setdefault('is_staff', False)
        extra_fields.setdefault('is_superuser', False)
        extra_fields.setdefault('is_active', True)
        return self._create_user(email, password, **extra_fields)

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)
        extra_fields.setdefault('is_active', True)
        return self._create_user(email, password, **extra_fields)
"@

$files['applications\users\apps.py'] = @"
from django.apps import AppConfig

class UsersConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'applications.users'
"@

$files['applications\users\mixins.py'] = @"
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.urls import reverse_lazy

from .models import User


class BaseRolePermisoMixin(LoginRequiredMixin):
    login_url = reverse_lazy('app_users:login')
    required_occupation = None

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        if request.user.is_superuser:
            return super().dispatch(request, *args, **kwargs)
        if request.user.occupation != self.required_occupation:
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)


class AdministradorPermisoMixin(BaseRolePermisoMixin):
    required_occupation = User.ADMINISTRADOR


class UsuarioPermisoMixin(BaseRolePermisoMixin):
    required_occupation = User.USUARIO


class EmpleadoPermisoMixin(BaseRolePermisoMixin):
    required_occupation = User.EMPLEADO


class OtroPermisoMixin(BaseRolePermisoMixin):
    required_occupation = User.OTRO
"@

$files['applications\users\forms.py'] = @"
from django import forms
from django.contrib.auth.password_validation import validate_password
from django.utils import timezone
from .models import User


class UserRegisterForm(forms.Form):
    email = forms.EmailField(label='Correo electrónico')
    first_name = forms.CharField(label='Nombres', max_length=50)
    last_name = forms.CharField(label='Apellidos', max_length=50)
    occupation = forms.ChoiceField(label='Ocupación', choices=User.OCCUPATION_CHOICES)
    gender = forms.ChoiceField(label='Género', choices=User.GENDER_CHOICES)
    phone = forms.CharField(label='Celular', max_length=15, required=False)
    date_birth = forms.DateField(label='Fecha de nacimiento', required=False)
    password1 = forms.CharField(label='Contraseña', widget=forms.PasswordInput)
    password2 = forms.CharField(label='Confirmar contraseña', widget=forms.PasswordInput)

    def clean(self):
        cleaned_data = super().clean()
        password1 = cleaned_data.get('password1')
        password2 = cleaned_data.get('password2')
        if password1 and password2 and password1 != password2:
            self.add_error('password2', 'Las contraseñas no coinciden.')
        return cleaned_data

    def clean_email(self):
        email = self.cleaned_data.get('email')
        if email and User.objects.filter(email__iexact=email).exists():
            self.add_error('email', 'Ya existe un usuario con este correo electrónico.')
        return email

    def clean_date_birth(self):
        date_birth = self.cleaned_data.get('date_birth')
        if date_birth and date_birth > timezone.localdate():
            self.add_error('date_birth', 'La fecha de nacimiento no puede ser futura.')
        return date_birth

    def clean_password1(self):
        password = self.cleaned_data.get('password1')
        if password:
            validate_password(password)
        return password


class LoginForm(forms.Form):
    email = forms.EmailField(label='Correo electrónico')
    password = forms.CharField(label='Contraseña', widget=forms.PasswordInput)


class UpdatePasswordForm(forms.Form):
    password1 = forms.CharField(label='Contraseña actual', widget=forms.PasswordInput)
    password2 = forms.CharField(label='Nueva contraseña', widget=forms.PasswordInput)
    password3 = forms.CharField(label='Confirmar nueva contraseña', widget=forms.PasswordInput)

    def clean(self):
        cleaned_data = super().clean()
        password2 = cleaned_data.get('password2')
        password3 = cleaned_data.get('password3')
        password1 = cleaned_data.get('password1')
        if password2 and password3 and password2 != password3:
            self.add_error('password3', 'Las contraseñas no coinciden.')
        if password1 and password2 and password1 == password2:
            self.add_error('password2', 'La nueva contraseña no puede ser igual a la actual.')
        return cleaned_data

    def clean_password2(self):
        password = self.cleaned_data.get('password2')
        if password:
            validate_password(password)
        return password
"@

$files['applications\users\services.py'] = @"
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
"@

$files['applications\users\views.py'] = @"
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpResponseRedirect
from django.shortcuts import render
from django.urls import reverse, reverse_lazy
from django.views.generic import View
from django.views.generic.edit import FormView

from .forms import LoginForm, UpdatePasswordForm, UserRegisterForm
from .mixins import AdministradorPermisoMixin
from . import services


class UserRegisterView(FormView):
    template_name = 'users/register.html'
    form_class = UserRegisterForm
    success_url = reverse_lazy('app_users:login')

    def form_valid(self, form):
        services.create_user(form.cleaned_data)
        messages.success(self.request, 'Cuenta creada correctamente. Inicia sesión.')
        return super().form_valid(form)


class LoginUser(FormView):
    template_name = 'users/login.html'
    form_class = LoginForm
    success_url = reverse_lazy('app_users:dashboard')

    def form_invalid(self, form):
        if not form.non_field_errors():
            form.add_error(None, 'Email o contraseña incorrectos.')
        return super().form_invalid(form)

    def form_valid(self, form):
        user = services.authenticate_user(
            self.request,
            form.cleaned_data['email'],
            form.cleaned_data['password'],
        )
        if user is None:
            form.add_error(None, 'Email o contraseña incorrectos.')
            return self.form_invalid(form)
        messages.success(self.request, f'Bienvenido, {user.first_name}.')
        return super().form_valid(form)


class LogoutView(LoginRequiredMixin, View):
    def get(self, request, *args, **kwargs):
        services.logout_user(request)
        return HttpResponseRedirect(reverse('app_users:login'))


class UpdatePasswordView(LoginRequiredMixin, FormView):
    template_name = 'users/cambiar_password.html'
    form_class = UpdatePasswordForm
    success_url = reverse_lazy('app_users:login')
    login_url = reverse_lazy('app_users:login')

    def form_valid(self, form):
        usuario = self.request.user
        user = services.authenticate_user(
            self.request,
            usuario.email,
            form.cleaned_data['password1'],
        )
        if user is None:
            form.add_error(None, 'La contraseña actual es incorrecta.')
            return self.form_invalid(form)

        services.change_password(usuario, form.cleaned_data['password2'])
        services.logout_user(self.request)
        messages.success(self.request, 'Contraseña actualizada correctamente. Vuelve a iniciar sesión.')
        return super().form_valid(form)


class DashboardView(LoginRequiredMixin, View):
    template_name = 'users/dashboard.html'

    def get(self, request):
        return render(request, self.template_name, {'user': request.user})


class ListaUsuariosView(AdministradorPermisoMixin, View):
    template_name = 'users/lista_usuarios.html'

    def get(self, request):
        usuarios = services.get_all_users()
        return render(request, self.template_name, {'usuarios': usuarios})
"@

$files['applications\users\urls.py'] = @"
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
"@

$files['applications\users\admin.py'] = @"
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.forms import UserChangeForm, UserCreationForm
from django.utils.translation import gettext_lazy as _

from .models import User


class CustomUserCreationForm(UserCreationForm):
    class Meta:
        model = User
        fields = ('email',)


class CustomUserChangeForm(UserChangeForm):
    class Meta:
        model = User
        fields = ('email',)


class UserAdminCustom(UserAdmin):
    add_form = CustomUserCreationForm
    form = CustomUserChangeForm
    model = User

    list_display = ('email', 'first_name', 'last_name', 'occupation', 'is_staff', 'is_active')
    list_filter = ('is_staff', 'is_active', 'occupation', 'gender')
    search_fields = ('email', 'first_name', 'last_name')
    ordering = ('email',)

    fieldsets = (
        (None, {'fields': ('email', 'password')}),
        (_('Datos personales'), {'fields': ('first_name', 'last_name', 'occupation', 'gender', 'date_birth', 'phone')}),
        (_('Permisos'), {
            'fields': ('is_active', 'is_staff', 'is_superuser', 'groups', 'user_permissions'),
        }),
        (_('Fechas importantes'), {'fields': ('last_login',)}),
    )

    add_fieldsets = (
        (None, {
            'classes': ('wide',),
            'fields': ('email', 'first_name', 'last_name', 'password1', 'password2'),
        }),
    )


admin.site.register(User, UserAdminCustom)
"@

$files['applications\users\migrations\0001_initial.py'] = @"
# Generated by Django 3.2.25 on 2026-09-02 05:51

from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('auth', '0012_alter_user_first_name_max_length'),
    ]

    operations = [
        migrations.CreateModel(
            name='User',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('password', models.CharField(max_length=128, verbose_name='password')),
                ('last_login', models.DateTimeField(blank=True, null=True, verbose_name='last login')),
                ('is_superuser', models.BooleanField(default=False, help_text='Designates that this user has all permissions without explicitly assigning them.', verbose_name='superuser status')),
                ('email', models.EmailField(max_length=254, unique=True)),
                ('first_name', models.CharField(max_length=50)),
                ('last_name', models.CharField(max_length=50)),
                ('occupation', models.CharField(blank=True, choices=[('0', 'Administrador'), ('1', 'Usuario'), ('2', 'Empleado'), ('3', 'Otro')], max_length=3)),
                ('gender', models.CharField(blank=True, choices=[('M', 'Masculino'), ('F', 'Femenino'), ('O', 'Otro')], max_length=1)),
                ('date_birth', models.DateField(blank=True, null=True)),
                ('phone', models.CharField(blank=True, max_length=15)),
                ('is_staff', models.BooleanField(default=False)),
                ('is_active', models.BooleanField(default=True)),
                ('groups', models.ManyToManyField(blank=True, help_text='The groups this user belongs to. A user will get all permissions granted to each of their groups.', related_name='user_set', related_query_name='user', to='auth.Group', verbose_name='groups')),
                ('user_permissions', models.ManyToManyField(blank=True, help_text='Specific permissions for this user.', related_name='user_set', related_query_name='user', to='auth.Permission', verbose_name='user permissions')),
            ],
            options={
                'abstract': False,
            },
        ),
    ]
"@

# ------------------------------------------------------------------
# APP USERS - TESTS
# ------------------------------------------------------------------
$files['applications\users\tests.py'] = @"
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

User = get_user_model()


class UserRegisterViewTests(TestCase):

    def test_register_creates_user(self):
        data = {
            'email': 'usuario@test.com',
            'first_name': 'Juan',
            'last_name': 'Pérez',
            'occupation': '1',
            'gender': 'M',
            'phone': '999888777',
            'password1': 'Passw0rd!123',
            'password2': 'Passw0rd!123',
        }
        response = self.client.post(reverse('app_users:register'), data)
        self.assertRedirects(response, reverse('app_users:login'))
        self.assertTrue(User.objects.filter(email='usuario@test.com').exists())
        user = User.objects.get(email='usuario@test.com')
        self.assertEqual(user.first_name, 'Juan')
        self.assertEqual(user.occupation, '1')

    def test_register_duplicate_email_rejected(self):
        User.objects.create_user('dup@test.com', 'Passw0rd!123', first_name='Ana', last_name='Luz')
        data = {
            'email': 'dup@test.com',
            'first_name': 'Otro',
            'last_name': 'User',
            'occupation': '1',
            'gender': 'M',
            'password1': 'Passw0rd!123',
            'password2': 'Passw0rd!123',
        }
        response = self.client.post(reverse('app_users:register'), data)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Ya existe un usuario con este correo electrónico.')

    def test_register_password_mismatch_rejected(self):
        data = {
            'email': 'x@test.com',
            'first_name': 'A',
            'last_name': 'B',
            'occupation': '1',
            'gender': 'M',
            'password1': 'Passw0rd!123',
            'password2': 'OtraPass0rd!456',
        }
        response = self.client.post(reverse('app_users:register'), data)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Las contraseñas no coinciden.')


class LoginUserTests(TestCase):

    def setUp(self):
        self.password = 'Passw0rd!123'
        self.user = User.objects.create_user('login@test.com', self.password, first_name='Carlos', last_name='Ruiz')

    def test_login_success(self):
        response = self.client.post(reverse('app_users:login'), {
            'email': 'login@test.com',
            'password': self.password,
        })
        self.assertRedirects(response, reverse('app_users:dashboard'))

    def test_login_wrong_password(self):
        response = self.client.post(reverse('app_users:login'), {
            'email': 'login@test.com',
            'password': 'incorrecta',
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Email o contraseña incorrectos.')

    def test_login_unknown_email(self):
        response = self.client.post(reverse('app_users:login'), {
            'email': 'nobody@test.com',
            'password': 'Passw0rd!123',
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Email o contraseña incorrectos.')


class UpdatePasswordViewTests(TestCase):

    def setUp(self):
        self.password = 'Passw0rd!123'
        self.user = User.objects.create_user('pass@test.com', self.password, first_name='Ana', last_name='Lira')
        self.client.login(email='pass@test.com', password=self.password)

    def test_change_password_success(self):
        response = self.client.post(reverse('app_users:user-update'), {
            'password1': self.password,
            'password2': 'NuevaPass0rd!456',
            'password3': 'NuevaPass0rd!456',
        })
        self.assertRedirects(response, reverse('app_users:login'))
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('NuevaPass0rd!456'))

    def test_change_password_wrong_current(self):
        response = self.client.post(reverse('app_users:user-update'), {
            'password1': 'incorrecta',
            'password2': 'NuevaPass0rd!456',
            'password3': 'NuevaPass0rd!456',
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'La contraseña actual es incorrecta.')

    def test_change_password_mismatch(self):
        response = self.client.post(reverse('app_users:user-update'), {
            'password1': self.password,
            'password2': 'NuevaPass0rd!456',
            'password3': 'OtraPass0rd!789',
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Las contraseñas no coinciden.')


class ListaUsuariosViewTests(TestCase):

    def setUp(self):
        self.admin = User.objects.create_user(
            'admin@test.com', 'Passw0rd!123',
            first_name='Admin', last_name='Root', occupation='0',
        )
        self.usuario = User.objects.create_user(
            'usuario@test.com', 'Passw0rd!123',
            first_name='Pa', last_name='Ciente', occupation='1',
        )

    def test_admin_can_list_users(self):
        self.client.login(email='admin@test.com', password='Passw0rd!123')
        response = self.client.get(reverse('app_users:lista'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'usuario@test.com')
        self.assertQuerysetEqual(
            response.context['usuarios'],
            list(User.objects.filter(pk__in=[self.admin.pk, self.usuario.pk])),
            ordered=False,
        )

    def test_non_admin_forbidden(self):
        self.client.login(email='usuario@test.com', password='Passw0rd!123')
        response = self.client.get(reverse('app_users:lista'))
        self.assertEqual(response.status_code, 403)

    def test_anonymous_redirected_to_login(self):
        response = self.client.get(reverse('app_users:lista'))
        self.assertRedirects(response, reverse('app_users:login') + '?next=/users/lista/')


class LogoutViewTests(TestCase):

    def test_logout_redirects_to_login(self):
        user = User.objects.create_user('out@test.com', 'Passw0rd!123', first_name='X', last_name='Y')
        self.client.login(email='out@test.com', password='Passw0rd!123')
        response = self.client.get(reverse('app_users:logout'))
        self.assertRedirects(response, reverse('app_users:login'))
"@

# ------------------------------------------------------------------
# TEMPLATES
# ------------------------------------------------------------------
$files['templates\base.html'] = @"
<!doctype html>
<html lang="es">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>{% block title %}Reserva{% endblock %}</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <style>
        :root {
            --web-primary: #2563eb;
            --web-primary-dark: #1d4ed8;
            --web-accent: #eaf2ff;
            --web-ink: #0f172a;
            --web-muted: #64748b;
        }

        body {
            color: var(--web-ink);
            background: #f8fafc;
        }

        .text-muted {
            color: var(--web-muted) !important;
        }

        .hero-card {
            background:
                radial-gradient(circle at top left, rgba(255, 255, 255, 0.18), transparent 30%),
                linear-gradient(135deg, #0f172a 0%, #1e3a8a 55%, #2563eb 100%);
        }

        .brand-mark {
            display: inline-flex;
            align-items: center;
            justify-content: center;
            width: 2rem;
            height: 2rem;
            border-radius: 0.75rem;
            background: var(--web-accent);
            color: var(--web-primary);
            font-weight: 800;
        }
    </style>
    {% block extra_css %}{% endblock %}
</head>
<body class="flex min-h-screen flex-col bg-[#f8fafc] text-[#0f172a] antialiased">
    {% include 'include/header.html' %}

    <main class="flex-1 py-8">
        <div class="mx-auto w-full max-w-6xl px-4">
            {% if messages %}
                <div class="mb-4 space-y-2">
                    {% for message in messages %}
                        <div class="rounded-lg border px-4 py-3 text-sm {% if message.tags == 'error' %}border-red-200 bg-red-50 text-red-700{% elif message.tags == 'success' %}border-green-200 bg-green-50 text-green-700{% elif message.tags == 'warning' %}border-yellow-200 bg-yellow-50 text-yellow-700{% else %}border-blue-200 bg-blue-50 text-blue-700{% endif %}" role="alert">
                            {{ message }}
                        </div>
                    {% endfor %}
                </div>
            {% endif %}

            {% block content %}{% endblock %}
        </div>
    </main>

    {% include 'include/footer.html' %}
    {% block extra_js %}{% endblock %}
</body>
</html>
"@

$files['templates\include\header.html'] = @"
<header class="border-b border-gray-200 bg-white shadow-sm">
    <nav class="mx-auto w-full max-w-6xl px-4">
        <div class="flex h-16 items-center justify-between">
            <a class="flex items-center gap-2 font-bold text-[#0f172a]" href="/">
                <span class="brand-mark">RB</span>
                <span>Sistema Web con Usuarios</span>
            </a>

            <ul class="flex items-center gap-3">
                <li>
                    <a class="px-3 py-2 text-sm font-medium text-[#0f172a] hover:text-[#2563eb]" href="/">Inicio</a>
                </li>

                {% if request.user.is_authenticated %}
                {% if request.user.occupation == '0' or request.user.is_superuser %}
                <li>
                    <a class="px-3 py-2 text-sm font-medium text-[#0f172a] hover:text-[#2563eb]" href="{% url 'app_users:lista' %}">Usuarios</a>
                </li>
                {% endif %}
                <li>
                    <a class="inline-block rounded-md border border-gray-300 px-3 py-2 text-sm font-medium text-gray-700 hover:bg-gray-100" href="{% url 'app_users:user-update' %}">Cambiar contraseña</a>
                </li>
                <li>
                    <a class="inline-block rounded-md border border-[#2563eb] px-3 py-2 text-sm font-medium text-[#2563eb] hover:bg-[#2563eb] hover:text-white" href="{% url 'app_users:dashboard' %}">Perfil</a>
                </li>
                <li>
                    <a class="inline-block rounded-md bg-red-600 px-3 py-2 text-sm font-medium text-white hover:bg-red-700" href="{% url 'app_users:logout' %}">Salir</a>
                </li>
                {% else %}
                <li>
                    <a class="inline-block rounded-md border border-[#2563eb] px-3 py-2 text-sm font-medium text-[#2563eb] hover:bg-[#2563eb] hover:text-white" href="{% url 'app_users:login' %}">Ingresar</a>
                </li>
                <li>
                    <a class="inline-block rounded-md bg-[#2563eb] px-3 py-2 text-sm font-medium text-white hover:bg-[#1d4ed8]" href="{% url 'app_users:register' %}">Registrarse</a>
                </li>
                {% endif %}
            </ul>
        </div>
    </nav>
</header>
"@

$files['templates\include\footer.html'] = @"
<footer class="mt-auto border-t border-gray-200 bg-white py-6">
    <div class="mx-auto w-full max-w-6xl px-4">
        <div class="flex flex-col items-center justify-between gap-4 md:flex-row">
            <div class="font-semibold text-[#0f172a]">Sistema Basico</div>
            <div class="text-center md:text-right">
                <div class="text-sm text-[#64748b]">roberthbardales@gmail.com</div>
                <div class="text-sm text-[#64748b]">© {% now "Y" %} Russell Bardales</div>
            </div>
        </div>
    </div>
</footer>
"@

$files['templates\home\index.html'] = @"
{% extends 'base.html' %}

{% block title %}Inicio | Desarrollo Web{% endblock %}

{% block content %}
<section class="hero-card mb-6 rounded-2xl p-8 text-white shadow-lg lg:p-10">
    <div class="flex flex-col gap-6 lg:flex-row lg:items-center">
        <div class="lg:flex-1">
            <span class="mb-3 inline-block rounded-full bg-white/90 px-3 py-1 text-xs font-semibold uppercase tracking-wide text-[#0f172a]">Desarrollo de sistemas web</span>
            <h1 class="mb-3 text-3xl font-bold leading-tight lg:text-5xl">Creamos sistemas web modernos con Django, Python y PostgreSQL.</h1>
            <p class="mb-6 text-lg text-white/90">
                Construimos soluciones simples y funcionales para tu negocio, con despliegue en Linux, DigitalOcean y Docker.
            </p>
            <div class="flex flex-col gap-3 sm:flex-row">
                <a class="inline-block rounded-lg bg-white px-6 py-3 text-center font-semibold text-[#0f172a] hover:bg-gray-100" href="{% url 'app_users:login' %}">Ver proyecto</a>
                <a class="inline-block rounded-lg border-2 border-white px-6 py-3 text-center font-semibold text-white hover:bg-white hover:text-[#0f172a]" href="{% url 'app_users:login' %}">Ingresar al panel</a>
            </div>
        </div>
        <div class="lg:w-2/5">
            <div class="rounded-2xl bg-white p-6 text-[#0f172a] shadow-md">
                <h2 class="mb-3 text-lg font-semibold">Tecnologías destacadas</h2>
                <ul class="space-y-2">
                    <li class="flex items-start gap-2">
                        <span class="mt-2 h-1.5 w-1.5 shrink-0 rounded-full bg-[#2563eb]"></span>
                        <span>Django y Python para aplicaciones robustas</span>
                    </li>
                    <li class="flex items-start gap-2">
                        <span class="mt-2 h-1.5 w-1.5 shrink-0 rounded-full bg-[#2563eb]"></span>
                        <span>PostgreSQL para gestión de datos</span>
                    </li>
                    <li class="flex items-start gap-2">
                        <span class="mt-2 h-1.5 w-1.5 shrink-0 rounded-full bg-[#2563eb]"></span>
                        <span>Linux, DigitalOcean y Docker para despliegue</span>
                    </li>
                    <li class="flex items-start gap-2">
                        <span class="mt-2 h-1.5 w-1.5 shrink-0 rounded-full bg-[#2563eb]"></span>
                        <span>PHP y WordPress para sitios y contenidos</span>
                    </li>
                </ul>
            </div>
        </div>
    </div>
</section>
{% endblock %}
"@

$files['templates\users\login.html'] = @"
{% extends 'base.html' %}

{% block title %}Login | Reserva{% endblock %}

{% block content %}
<div class="flex justify-center">
    <div class="w-full max-w-md">
        <div class="rounded-xl border border-gray-100 bg-white p-6 shadow-sm sm:p-8">
            <h1 class="mb-6 text-center text-2xl font-bold text-[#0f172a]">Ingresar</h1>
            <form method="post" novalidate class="space-y-4">
                {% csrf_token %}

                {% if form.non_field_errors %}
                    <div class="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
                        {% for error in form.non_field_errors %}
                            {{ error }}
                        {% endfor %}
                    </div>
                {% endif %}

                <div>
                    <label for="{{ form.email.id_for_label }}" class="mb-1 block text-sm font-medium text-[#0f172a]">Correo electrónico</label>
                    <input type="email" name="{{ form.email.html_name }}" id="{{ form.email.id_for_label }}" value="{{ form.email.value|default:'' }}"
                           class="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-[#2563eb] focus:outline-none focus:ring-2 focus:ring-[#2563eb]/30" required>
                    {% for error in form.email.errors %}
                        <div class="mt-1 text-sm text-red-600">{{ error }}</div>
                    {% endfor %}
                </div>

                <div>
                    <label for="{{ form.password.id_for_label }}" class="mb-1 block text-sm font-medium text-[#0f172a]">Contraseña</label>
                    <input type="password" name="{{ form.password.html_name }}" id="{{ form.password.id_for_label }}"
                           class="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-[#2563eb] focus:outline-none focus:ring-2 focus:ring-[#2563eb]/30" required>
                    {% for error in form.password.errors %}
                        <div class="mt-1 text-sm text-red-600">{{ error }}</div>
                    {% endfor %}
                </div>

                <button class="w-full rounded-lg bg-[#2563eb] px-4 py-2.5 text-sm font-semibold text-white hover:bg-[#1d4ed8]" type="submit">Entrar</button>
            </form>
        </div>
    </div>
</div>
{% endblock %}
"@

$files['templates\users\register.html'] = @"
{% extends 'base.html' %}

{% block title %}Registro | Reserva{% endblock %}

{% block content %}
<div class="flex justify-center">
    <div class="w-full max-w-2xl">
        <div class="rounded-xl border border-gray-100 bg-white p-6 shadow-sm sm:p-8">
            <h1 class="mb-6 text-center text-2xl font-bold text-[#0f172a]">Crear cuenta</h1>
            <form method="post" novalidate class="space-y-4">
                {% csrf_token %}

                {% if form.non_field_errors %}
                    <div class="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
                        {% for error in form.non_field_errors %}
                            {{ error }}
                        {% endfor %}
                    </div>
                {% endif %}

                <div class="grid gap-4 sm:grid-cols-2">
                    <div>
                        <label for="{{ form.first_name.id_for_label }}" class="mb-1 block text-sm font-medium text-[#0f172a]">Nombres</label>
                        <input type="text" name="{{ form.first_name.html_name }}" id="{{ form.first_name.id_for_label }}" value="{{ form.first_name.value|default:'' }}"
                               class="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-[#2563eb] focus:outline-none focus:ring-2 focus:ring-[#2563eb]/30" required>
                        {% for error in form.first_name.errors %}
                            <div class="mt-1 text-sm text-red-600">{{ error }}</div>
                        {% endfor %}
                    </div>
                    <div>
                        <label for="{{ form.last_name.id_for_label }}" class="mb-1 block text-sm font-medium text-[#0f172a]">Apellidos</label>
                        <input type="text" name="{{ form.last_name.html_name }}" id="{{ form.last_name.id_for_label }}" value="{{ form.last_name.value|default:'' }}"
                               class="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-[#2563eb] focus:outline-none focus:ring-2 focus:ring-[#2563eb]/30" required>
                        {% for error in form.last_name.errors %}
                            <div class="mt-1 text-sm text-red-600">{{ error }}</div>
                        {% endfor %}
                    </div>
                </div>

                <div>
                    <label for="{{ form.email.id_for_label }}" class="mb-1 block text-sm font-medium text-[#0f172a]">Correo electrónico</label>
                    <input type="email" name="{{ form.email.html_name }}" id="{{ form.email.id_for_label }}" value="{{ form.email.value|default:'' }}"
                           class="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-[#2563eb] focus:outline-none focus:ring-2 focus:ring-[#2563eb]/30" required>
                    {% for error in form.email.errors %}
                        <div class="mt-1 text-sm text-red-600">{{ error }}</div>
                    {% endfor %}
                </div>

                <div class="grid gap-4 sm:grid-cols-2">
                    <div>
                        <label for="{{ form.occupation.id_for_label }}" class="mb-1 block text-sm font-medium text-[#0f172a]">Ocupación</label>
                        <select name="{{ form.occupation.html_name }}" id="{{ form.occupation.id_for_label }}"
                                class="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-[#2563eb] focus:outline-none focus:ring-2 focus:ring-[#2563eb]/30">
                            {% for value, label in form.fields.occupation.choices %}
                                <option value="{{ value }}" {% if form.occupation.value == value %}selected{% endif %}>{{ label }}</option>
                            {% endfor %}
                        </select>
                        {% for error in form.occupation.errors %}
                            <div class="mt-1 text-sm text-red-600">{{ error }}</div>
                        {% endfor %}
                    </div>
                    <div>
                        <label for="{{ form.gender.id_for_label }}" class="mb-1 block text-sm font-medium text-[#0f172a]">Género</label>
                        <select name="{{ form.gender.html_name }}" id="{{ form.gender.id_for_label }}"
                                class="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-[#2563eb] focus:outline-none focus:ring-2 focus:ring-[#2563eb]/30">
                            {% for value, label in form.fields.gender.choices %}
                                <option value="{{ value }}" {% if form.gender.value == value %}selected{% endif %}>{{ label }}</option>
                            {% endfor %}
                        </select>
                        {% for error in form.gender.errors %}
                            <div class="mt-1 text-sm text-red-600">{{ error }}</div>
                        {% endfor %}
                    </div>
                </div>

                <div class="grid gap-4 sm:grid-cols-2">
                    <div>
                        <label for="{{ form.phone.id_for_label }}" class="mb-1 block text-sm font-medium text-[#0f172a]">Celular</label>
                        <input type="text" name="{{ form.phone.html_name }}" id="{{ form.phone.id_for_label }}" value="{{ form.phone.value|default:'' }}"
                               class="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-[#2563eb] focus:outline-none focus:ring-2 focus:ring-[#2563eb]/30">
                        {% for error in form.phone.errors %}
                            <div class="mt-1 text-sm text-red-600">{{ error }}</div>
                        {% endfor %}
                    </div>
                    <div>
                        <label for="{{ form.date_birth.id_for_label }}" class="mb-1 block text-sm font-medium text-[#0f172a]">Fecha de nacimiento</label>
                        <input type="date" name="{{ form.date_birth.html_name }}" id="{{ form.date_birth.id_for_label }}" value="{{ form.date_birth.value|default:'' }}"
                               class="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-[#2563eb] focus:outline-none focus:ring-2 focus:ring-[#2563eb]/30">
                        {% for error in form.date_birth.errors %}
                            <div class="mt-1 text-sm text-red-600">{{ error }}</div>
                        {% endfor %}
                    </div>
                </div>

                <div class="grid gap-4 sm:grid-cols-2">
                    <div>
                        <label for="{{ form.password1.id_for_label }}" class="mb-1 block text-sm font-medium text-[#0f172a]">Contraseña</label>
                        <input type="password" name="{{ form.password1.html_name }}" id="{{ form.password1.id_for_label }}"
                               class="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-[#2563eb] focus:outline-none focus:ring-2 focus:ring-[#2563eb]/30" required>
                        {% for error in form.password1.errors %}
                            <div class="mt-1 text-sm text-red-600">{{ error }}</div>
                        {% endfor %}
                    </div>
                    <div>
                        <label for="{{ form.password2.id_for_label }}" class="mb-1 block text-sm font-medium text-[#0f172a]">Confirmar contraseña</label>
                        <input type="password" name="{{ form.password2.html_name }}" id="{{ form.password2.id_for_label }}"
                               class="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-[#2563eb] focus:outline-none focus:ring-2 focus:ring-[#2563eb]/30" required>
                        {% for error in form.password2.errors %}
                            <div class="mt-1 text-sm text-red-600">{{ error }}</div>
                        {% endfor %}
                    </div>
                </div>

                <button class="w-full rounded-lg bg-[#2563eb] px-4 py-2.5 text-sm font-semibold text-white hover:bg-[#1d4ed8]" type="submit">Registrarme</button>
            </form>

            <p class="mt-4 text-center text-sm text-[#64748b]">
                ¿Ya tienes cuenta?
                <a class="font-medium text-[#2563eb] hover:underline" href="{% url 'app_users:login' %}">Inicia sesión</a>
            </p>
        </div>
    </div>
</div>
{% endblock %}
"@

$files['templates\users\dashboard.html'] = @"
{% extends 'base.html' %}

{% block title %}Dashboard | Reserva{% endblock %}

{% block content %}
<div class="flex justify-center">
    <div class="w-full max-w-2xl">
        <div class="rounded-xl border border-gray-100 bg-white p-6 shadow-sm sm:p-8">
            <h1 class="mb-4 text-2xl font-bold text-[#0f172a]">Tu perfil</h1>
            <dl class="space-y-3">
                <div class="flex justify-between border-b border-gray-100 pb-2">
                    <dt class="text-sm font-medium text-[#64748b]">Nombre</dt>
                    <dd class="text-sm font-semibold text-[#0f172a]">{{ user.first_name }} {{ user.last_name }}</dd>
                </div>
                <div class="flex justify-between border-b border-gray-100 pb-2">
                    <dt class="text-sm font-medium text-[#64748b]">Email</dt>
                    <dd class="text-sm font-semibold text-[#0f172a]">{{ user.email }}</dd>
                </div>
                <div class="flex justify-between border-b border-gray-100 pb-2">
                    <dt class="text-sm font-medium text-[#64748b]">Celular</dt>
                    <dd class="text-sm font-semibold text-[#0f172a]">{{ user.phone|default:"Sin registrar" }}</dd>
                </div>
                <div class="flex justify-between border-b border-gray-100 pb-2">
                    <dt class="text-sm font-medium text-[#64748b]">Ocupación</dt>
                    <dd class="text-sm font-semibold text-[#0f172a]">{{ user.get_occupation_display|default:"Sin registrar" }}</dd>
                </div>
                <div class="flex justify-between">
                    <dt class="text-sm font-medium text-[#64748b]">Género</dt>
                    <dd class="text-sm font-semibold text-[#0f172a]">{{ user.get_gender_display|default:"Sin registrar" }}</dd>
                </div>
            </dl>
        </div>
    </div>
</div>
{% endblock %}
"@

$files['templates\users\lista_usuarios.html'] = @"
{% extends 'base.html' %}

{% block title %}Usuarios | Reserva{% endblock %}

{% block content %}
<div class="flex justify-center">
    <div class="w-full max-w-4xl">
        <div class="rounded-xl border border-gray-100 bg-white p-6 shadow-sm sm:p-8">
            <h1 class="mb-4 text-2xl font-bold text-[#0f172a]">Usuarios del sistema</h1>
            <div class="overflow-x-auto">
                <table class="w-full text-left text-sm">
                    <thead>
                        <tr class="border-b border-gray-200 text-[#64748b]">
                            <th class="px-3 py-2 font-semibold">Nombre</th>
                            <th class="px-3 py-2 font-semibold">Email</th>
                            <th class="px-3 py-2 font-semibold">Ocupación</th>
                            <th class="px-3 py-2 font-semibold">Género</th>
                        </tr>
                    </thead>
                    <tbody class="divide-y divide-gray-100">
                        {% for usuario in usuarios %}
                            <tr class="hover:bg-gray-50">
                                <td class="px-3 py-2 font-medium text-[#0f172a]">{{ usuario.first_name }} {{ usuario.last_name }}</td>
                                <td class="px-3 py-2 text-[#0f172a]">{{ usuario.email }}</td>
                                <td class="px-3 py-2 text-[#0f172a]">{{ usuario.get_occupation_display }}</td>
                                <td class="px-3 py-2 text-[#0f172a]">{{ usuario.get_gender_display }}</td>
                            </tr>
                        {% empty %}
                            <tr>
                                <td colspan="4" class="px-3 py-4 text-center text-[#64748b]">No hay usuarios registrados.</td>
                            </tr>
                        {% endfor %}
                    </tbody>
                </table>
            </div>
        </div>
    </div>
</div>
{% endblock %}
"@

$files['templates\users\cambiar_password.html'] = @"
{% extends 'base.html' %}

{% block title %}Cambiar contraseña | Reserva{% endblock %}

{% block content %}
<div class="flex justify-center">
    <div class="w-full max-w-md">
        <div class="rounded-xl border border-gray-100 bg-white p-6 shadow-sm sm:p-8">
            <h1 class="mb-6 text-center text-2xl font-bold text-[#0f172a]">Cambiar contraseña</h1>
            <form method="post" novalidate class="space-y-4">
                {% csrf_token %}

                {% if form.non_field_errors %}
                    <div class="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
                        {% for error in form.non_field_errors %}
                            {{ error }}
                        {% endfor %}
                    </div>
                {% endif %}

                <div>
                    <label for="{{ form.password1.id_for_label }}" class="mb-1 block text-sm font-medium text-[#0f172a]">Contraseña actual</label>
                    <input type="password" name="{{ form.password1.html_name }}" id="{{ form.password1.id_for_label }}"
                           class="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-[#2563eb] focus:outline-none focus:ring-2 focus:ring-[#2563eb]/30" required>
                    {% for error in form.password1.errors %}
                        <div class="mt-1 text-sm text-red-600">{{ error }}</div>
                    {% endfor %}
                </div>

                <div>
                    <label for="{{ form.password2.id_for_label }}" class="mb-1 block text-sm font-medium text-[#0f172a]">Nueva contraseña</label>
                    <input type="password" name="{{ form.password2.html_name }}" id="{{ form.password2.id_for_label }}"
                           class="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-[#2563eb] focus:outline-none focus:ring-2 focus:ring-[#2563eb]/30" required>
                    {% for error in form.password2.errors %}
                        <div class="mt-1 text-sm text-red-600">{{ error }}</div>
                    {% endfor %}
                </div>

                <div>
                    <label for="{{ form.password3.id_for_label }}" class="mb-1 block text-sm font-medium text-[#0f172a]">Confirmar nueva contraseña</label>
                    <input type="password" name="{{ form.password3.html_name }}" id="{{ form.password3.id_for_label }}"
                           class="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-[#2563eb] focus:outline-none focus:ring-2 focus:ring-[#2563eb]/30" required>
                    {% for error in form.password3.errors %}
                        <div class="mt-1 text-sm text-red-600">{{ error }}</div>
                    {% endfor %}
                </div>

                <button class="w-full rounded-lg bg-[#2563eb] px-4 py-2.5 text-sm font-semibold text-white hover:bg-[#1d4ed8]" type="submit">Actualizar contraseña</button>
            </form>
        </div>
    </div>
</div>
{% endblock %}
"@

# ------------------------------------------------------------------
# FIXTURE DE USUARIOS
# ------------------------------------------------------------------
$files['fixtures\users.json'] = @"
[
  {
    "model": "users.user",
    "pk": null,
    "fields": {
      "password": "pbkdf2_sha256`$260000`$gPHMcnrr3awzapSyCqfqiL`$uMNwJrA3PJlCdUNOpUbXGQzkaoXROC9QEin9IvBY6Yc=",
      "last_login": null,
      "is_superuser": false,
      "first_name": "Papa",
      "last_name": "Bardales",
      "is_staff": false,
      "is_active": true,
      "email": "papa@gmail.com",
      "occupation": "0",
      "gender": "M",
      "date_birth": "1980-01-01",
      "phone": "999999999",
      "groups": [],
      "user_permissions": []
    }
  },
  {
    "model": "users.user",
    "pk": null,
    "fields": {
      "password": "pbkdf2_sha256`$260000`$gPHMcnrr3awzapSyCqfqiL`$uMNwJrA3PJlCdUNOpUbXGQzkaoXROC9QEin9IvBY6Yc=",
      "last_login": null,
      "is_superuser": false,
      "first_name": "Mama",
      "last_name": "Bardales",
      "is_staff": false,
      "is_active": true,
      "email": "mama@gmail.com",
      "occupation": "1",
      "gender": "F",
      "date_birth": "1982-01-01",
      "phone": "999999998",
      "groups": [],
      "user_permissions": []
    }
  },
  {
    "model": "users.user",
    "pk": null,
    "fields": {
      "password": "pbkdf2_sha256`$260000`$gPHMcnrr3awzapSyCqfqiL`$uMNwJrA3PJlCdUNOpUbXGQzkaoXROC9QEin9IvBY6Yc=",
      "last_login": null,
      "is_superuser": false,
      "first_name": "Hermano",
      "last_name": "Bardales",
      "is_staff": false,
      "is_active": true,
      "email": "hermano@gmail.com",
      "occupation": "2",
      "gender": "M",
      "date_birth": "1985-01-01",
      "phone": "999999997",
      "groups": [],
      "user_permissions": []
    }
  },
  {
    "model": "users.user",
    "pk": null,
    "fields": {
      "password": "pbkdf2_sha256`$260000`$gPHMcnrr3awzapSyCqfqiL`$uMNwJrA3PJlCdUNOpUbXGQzkaoXROC9QEin9IvBY6Yc=",
      "last_login": null,
      "is_superuser": false,
      "first_name": "Hermana",
      "last_name": "Bardales",
      "is_staff": false,
      "is_active": true,
      "email": "hermana@gmail.com",
      "occupation": "3",
      "gender": "F",
      "date_birth": "1988-01-01",
      "phone": "999999996",
      "groups": [],
      "user_permissions": []
    }
  }
]
"@

# ------------------------------------------------------------------
# ESCRITURA DE ARCHIVOS
# ------------------------------------------------------------------
foreach ($path in $files.Keys) {
    Write-Utf8Text -Path (Join-Path $destinationPath $path) -Content $files[$path]
}

$pythonCmd = Get-PythonCommand
if ($pythonCmd) {
    $venvPath = Join-Path $destinationPath 'venv'
    $venvPython = Join-Path $venvPath 'Scripts\python.exe'
    & $pythonCmd -m venv $venvPath
    if ($LASTEXITCODE -ne 0) { throw 'No se pudo crear el entorno virtual.' }
    & $venvPython -m pip install --upgrade pip
    if ($LASTEXITCODE -ne 0) { throw 'No se pudo actualizar pip.' }
    & $venvPython -m pip install -r (Join-Path $destinationPath 'requirements.txt')
    if ($LASTEXITCODE -ne 0) { throw 'No se pudieron instalar las dependencias.' }
}

Write-Host "Proyecto creado correctamente en: $destinationPath"
