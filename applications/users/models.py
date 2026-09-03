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