# base — chatbot2026

Documentación general del proyecto: stack, estructura, apps, templates, funcionalidades,
tests y decisiones tomadas. Úsala como referencia para desarrollo y mantenimiento.

---

## 1. Descripción general

Sistema web de reservas/usuarios tipo clínica construido con **Django**, **Python** y **PostgreSQL**.
Incluye autenticación con usuario personalizado, registro, cambio de contraseña, dashboard de
perfil y listado de usuarios protegido para administradores.

---

## 2. Stack tecnológico

| Capa | Tecnología / Paquete |
|------|----------------------|
| Backend | Django 3.2.25 (Python) |
| Base de datos | PostgreSQL (psycopg2-binary), config vía `.env` |
| Entorno / config | django-environ 0.11.2 |
| Frontend | HTML + CSS propio (variables) + Tailwind CSS vía **Play CDN** |
| Extras instalados | Pillow 9.5.0, django-model-utils 5.0.0 (instalados, sin uso actual) |

> **Nota:** Django 3.2 está fuera de soporte (EOL 2024). Se recomienda migrar a 4.2 LTS o superior.
> **Nota Tailwind:** el Play CDN es para desarrollo; para producción se recomienda build con Node.

---

## 3. Estructura del proyecto

```
chatbot2026/
├── manage.py
├── requirements.txt
├── .env                      # Variables de entorno sensibles (NO versionar)
├── activar.bat               # Activa venv
├── chatbot2026/              # Configuración del proyecto
│   ├── settings.py
│   ├── urls.py
│   ├── asgi.py / wsgi.py
├── applications/
│   ├── home/                 # App pública (página de inicio)
│   │   ├── views.py          #   IndexView (TemplateView)
│   │   ├── urls.py           #   / → app_home:index
│   │   ├── apps.py / models.py / admin.py / tests.py / migrations/
│   └── users/                # App de usuarios y autenticación
│       ├── models.py         #   User (AbstractBaseUser, email como username)
│       ├── managers.py       #   UserManager (create_user / create_superuser)
│       ├── forms.py          #   UserRegisterForm, LoginForm, UpdatePasswordForm
│       ├── views.py          #   Vistas de registro/login/logout/password/perfil/lista
│       ├── urls.py           #   app_name = 'app_users'
│       ├── mixins.py         #   AdministradorPermisoMixin
│       ├── services.py       #   Lógica de negocio (create_user, authenticate, etc.)
│       ├── admin.py          #   Admin personalizado para User
│       ├── tests.py          #   13 tests
│       └── migrations/
├── templates/                # Plantillas globales (DIRS apunta aquí)
│   ├── base.html
│   ├── include/ (header.html, footer.html)
│   ├── home/ (index.html)
│   └── users/ (login, register, dashboard, lista_usuarios, cambiar_password)
├── static/                   # Archivos estáticos (vacío, solo .gitkeep)
├── fixtures/                 # Fixtures (vacío, solo .gitkeep)
├── media/                    # Subidas de usuarios (servidas en DEBUG)
└── venv/                     # Entorno virtual
```

---

## 4. Configuración clave (`chatbot2026/settings.py`)

- **Apps instaladas:** defaults de Django + `applications.users`, `applications.home`.
- **Base de datos:** PostgreSQL configurado vía variables de entorno del `.env`
  (`DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST`, `DB_PORT`).
- **Templates:** `DIRS = [BASE_DIR / 'templates']`, `APP_DIRS: True`.
- **Estáticos/media:** `STATICFILES_DIRS = [BASE_DIR / 'static']`, `STATIC_ROOT = staticfiles/`,
  `MEDIA_URL = /media/`, `MEDIA_ROOT = media/`. `static()` solo en DEBUG.
- **Usuario custom:** `AUTH_USER_MODEL = 'users.User'`.
- **URLs de auth:** `LOGIN_URL = 'app_users:login'`, `LOGIN_REDIRECT_URL = 'app_users:dashboard'`,
  `LOGOUT_REDIRECT_URL = 'app_users:login'`.
- **i18n:** `LANGUAGE_CODE = 'es-pe'`, `TIME_ZONE = 'America/Lima'`, `USE_TZ = True`.
- **Seguridad en producción:** bloque `if not DEBUG:` con `SECURE_SSL_REDIRECT`, HSTS,
  `SECURE_CONTENT_TYPE_NOSNIFF`, cookies secure, `X_FRAME_OPTIONS='DENY'`.

### Proyecto `chatbot2026/urls.py` (raíz)
```
/admin/            → admin.site.urls
/                  → applications.home.urls   (app_name = 'app_home')
/users/            → applications.users.urls  (app_name = 'app_users')
```

---

## 5. App `applications.users`

### Modelo `User` (`models.py`)
- Hereda `AbstractBaseUser`, `PermissionsMixin`.
- `USERNAME_FIELD = 'email'`; `REQUIRED_FIELDS = ['first_name', 'last_name']`.
- Campos: `email` (unique), `first_name`, `last_name`, `occupation`, `gender`, `date_birth`,
  `phone`, `is_staff`, `is_active`.
- Roles (`occupation`): `ADMINISTRADOR='0'`, `USUARIO='1'`, `EMPLEADO='2'`, `OTRO='3'`.
- Géneros (`gender`): `VARON='M'`, `MUJER='F'`, `OTRO='O'`.
- Métodos: `__str__` → `"Nombre Apellido (email)"`, `get_full_name()`, `get_short_name()`.
- Manager: `UserManager` (normaliza email, setea flags por defecto).

### Manager (`managers.py`)
- `create_user(email, password=None, **extra)` — is_staff/superuser False, is_active True.
- `create_superuser(...)` — is_staff/superuser/active True.

### Formularios (`forms.py`)
- **UserRegisterForm** (fields.Form): email, first_name, last_name, occupation, gender, phone,
  date_birth, password1, password2.
  - `clean()` — contraseñas deben coincidir.
  - `clean_email()` — email único (rechaza duplicados con mensaje amigable).
  - `clean_date_birth()` — no admite fecha futura.
  - `clean_password1()` — valida con `validate_password`.
- **LoginForm**: email + password.
- **UpdatePasswordForm**: password1 (actual), password2 (nueva), password3 (confirmación).
  - Coincidencia de nueva/confirmación, nueva ≠ actual, fortaleza con `validate_password`.

### Vistas (`views.py`)
| Vista | URL | Permiso | Función |
|-------|-----|---------|---------|
| `UserRegisterView` | `/users/register/` | Público | Registro; mensaje de éxito, redirige a login |
| `LoginUser` | `/users/login/` | Público | Login; mensaje de bienvenida, redirige a dashboard |
| `LogoutView` | `/users/logout/` | Login | Cierra sesión → login |
| `UpdatePasswordView` | `/users/update/` | Login | Cambia contraseña (auto logout → login) |
| `DashboardView` | `/users/` | Login | Muestra perfil del usuario |
| `ListaUsuariosView` | `/users/lista/` | Solo Admin | Lista todos los usuarios |

### Servicios (`services.py`) — lógica de negocio desacoplada de las vistas
- `create_user(form_data)`
- `authenticate_user(request, email, password)` — autentica y hace login
- `get_all_users()` — ordenados por nombre
- `change_password(user, new_password)`
- `logout_user(request)`

### Mixins (`mixins.py`)
- `BaseRolePermisoMixin(LoginRequiredMixin)` — clase base que exige autenticación y (`occupation`)
  coincida con `required_occupation`. Los superusers pasan automáticamente. No-autenticado →
  login; rol incorrecto → `PermissionDenied` (403).
- `AdministradorPermisoMixin`, `UsuarioPermisoMixin`, `EmpleadoPermisoMixin`, `OtroPermisoMixin` —
  cada uno fija `required_occupation` a `User.ADMINISTRADOR`/`USUARIO`/`EMPLEADO`/`OTRO`.

### Admin (`admin.py`)
- `UserAdminCustom(UserAdmin)` registrado en `admin.site.register(User, UserAdminCustom)`.
- Formularios custom basados en `UserCreationForm`/`UserChangeForm` (login por email).
- `list_display`, `list_filter`, `search_fields`, `fieldsets` y `add_fieldsets` adaptados.

### URLs (`app_name = 'app_users'`)
```
register/   → UserRegisterView   (name='register')
login/      → LoginUser          (name='login')
logout/     → LogoutView         (name='logout')
update/     → UpdatePasswordView (name='user-update')
lista/      → ListaUsuariosView  (name='lista')
''          → DashboardView      (name='dashboard')
```

### Tests (`tests.py`) — 13 tests, todos pasando
- **UserRegisterViewTests**: crea usuario; email duplicado rechazado; contraseñas no coinciden rechazado.
- **LoginUserTests**: login correcto; contraseña errónea; email inexistente.
- **UpdatePasswordViewTests**: cambio exitoso; contraseña actual errónea; nueva ≠ confirmación.
- **ListaUsuariosViewTests**: admin lista usuarios; no-admin → 403; anónimo → login (con `?next=`).
- **LogoutViewTests**: logout redirige a login.

> Para ejecutar tests: el usuario de PostgreSQL necesita `CREATEDB` (Django crea la BD
> temporal `test_*`). Ejecutar: `python manage.py test applications.users -v 2`.

---

## 6. App `applications.home`

- `IndexView(TemplateView)` → `templates/home/index.html`.
- URL raíz `/` con `app_name = 'app_home'`.
- Modelos y admin sin contenido (plantilla vacía).

---

## 7. Templates (`templates/`)

Todas las plantillas usan **Tailwind CSS (Play CDN)** — sin Bootstrap.

| Template | Descripción |
|----------|-------------|
| `base.html` | Layout base: bloque `title`, `extra_css`, `content`, `extra_js`; mensajes flash con colores según tag; incluye header/footer |
| `include/header.html` | Navbar: marca RB, enlaces Inicio/Login/Registro (anónimo) o Usuarios(solo admin o superuser)/Cambiar contraseña/Perfil/Salir (autenticado) |
| `include/footer.html` | Footer con copyright y email |
| `home/index.html` | Landing hero con gradiente propio `.hero-card` + tarjeta "Tecnologías destacadas" |
| `users/login.html` | Formulario de login centrado |
| `users/register.html` | Formulario de registro (grid 2 columnas en desktop) |
| `users/dashboard.html` | Perfil del usuario como lista de definición |
| `users/lista_usuarios.html` | Tabla de usuarios (solo admin) |
| `users/cambiar_password.html` | Cambio de contraseña (actual/nueva/confirmación) |

### CSS propio (en `base.html`)
- Variables `--web-primary` (#2563eb), `--web-primary-dark`, `--web-accent`, `--web-ink`, `--web-muted`.
- Clase `.hero-card` (gradiente azul oscuro), `.brand-mark` (logo RB), `.text-muted`.

---

## 8. Funcionalidades y reglas de negocio

1. **Registro** (público): valida email único, contraseñas iguales, fortaleza, nacimiento no futuro.
2. **Login** (público): mensaje genérico "Email o contraseña incorrectos." (evita revelar qué falló).
3. **Logout** (autenticado): cierra sesión y redirige a login.
4. **Cambiar contraseña** (autenticado): verifica la actual, guarda la nueva, cierra sesión.
5. **Dashboard** (autenticado): muestra perfil.
6. **Listar usuarios** (solo administradores): el enlace "Usuarios" solo aparece para
   `occupation == '0'` o superusers; accesos directos de no-admin dan 403; anónimos van a login.
7. **Mensajes flash** al registrarse, entrar y cambiar la contraseña.

---

## 9. Historial de correcciones aplicadas

1. **Mojibake (codificación)** en `forms.py`, `views.py`, `register.html` → UTF-8 correcto.
2. **HTML mal cerrado** en `home/index.html` → añadido `</div>` y `</section>`.
3. **Validación de contraseña** en registro y cambio de contraseña (confirmación + fortaleza).
4. **Template `lista_usuarios.html` enlazado** con `ListaUsuariosView` + URL `/users/lista/`.
5. **`AdministradorPermisoMixin` implementado** (roles de administrador reales).
6. **Admin de User mejorado** (`UserAdminCustom`).
7. **Carpetas `static/` y `fixtures/` creadas** (con `.gitkeep`).
8. **Comentario muerto** de `procesors` eliminado de settings.
9. **Bootstrap eliminado** por completo → **Tailwind CSS** (Play CDN) en los 9 templates.
10. **Email duplicado** validado en el registro (evita 500).
11. **Lógica movida a `services.py`** (desacople vistas/servicios).
12. **Mensajes de éxito** en registro, login y cambio de contraseña.
13. **13 tests** escritos en `tests.py` (todos pasando).
14. **`__str__`, `get_full_name`, `get_short_name`** en el modelo `User`.
15. **`date_birth`** rechaza fechas futuras.
16. **`login_url` redundante** eliminado de vistas.
17. **Cabeceras de seguridad** de producción en `settings.py`.

---

## 10. Pendiente / mejoras futuras potenciales

- Migrar **Django 3.2 → versión soportada** (4.2 LTS o 5.x).
- Roles con `TextField`/`TextChoices` legibles (requiere migración de datos; hoy se usan
  strings `'0'..'3'`).
- Tailwind con **build (Node)** para producción en vez del Play CDN.

---

## 11. Comandos útiles

```bash
# Activar entorno
activar.bat                      # o .\venv\Scripts\activate

# Check de configuración
python manage.py check

# Ejecutar tests
python manage.py test applications.users -v 2

# Migraciones
python manage.py makemigrations
python manage.py migrate

# Servidor de desarrollo
python manage.py runserver
```