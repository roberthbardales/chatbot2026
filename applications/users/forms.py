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
