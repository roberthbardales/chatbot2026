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
