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