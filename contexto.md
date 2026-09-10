# Contexto del proyecto: chatbot2026

Última actualización: 2026-09-09

## Resumen

Aplicación web Django para gestión de usuarios, autenticación y un MVP de chatbot basado en los artículos de una API pública externa. La base continúa preparada para evolucionar hacia un sistema de reservas tipo clínica.

## Stack

- Python 3.10+
- Django 3.2.25, actualmente fuera de soporte
- PostgreSQL mediante `psycopg2-binary`
- Configuración por variables de entorno con `django-environ`
- Plantillas HTML con Tailwind Play CDN
- Gemini API mediante `google-generativeai`, usando `gemini-2.5-flash-lite`
- Groq API mediante `groq`, usando `openai/gpt-oss-20b`
- Zona horaria: `America/Lima`
- Idioma: `es-pe`

## Estructura relevante

```text
chatbot2026/
├── manage.py
├── requirements.txt
├── .env                 # local, no versionar
├── chatbot2026/         # settings, urls, ASGI y WSGI
├── applications/
│   ├── home/            # página inicial pública
│   ├── users/           # usuario custom y autenticación
│   └── chat/            # chatbot, API del blog y conversaciones
├── templates/           # plantillas globales
├── static/              # archivos estáticos fuente
├── staticfiles/         # salida de collectstatic, ignorada
├── media/               # archivos subidos, ignorada
└── fixtures/
```

## Configuración principal

- `AUTH_USER_MODEL = 'users.User'`
- Base de datos PostgreSQL configurada desde `.env`
- `DEBUG`, `SECRET_KEY` y `ALLOWED_HOSTS` vienen del entorno
- `LOGIN_URL`: `app_users:login`
- `LOGIN_REDIRECT_URL`: `app_users:dashboard`
- `LOGOUT_REDIRECT_URL`: `app_users:login`
- `GEMINI_API_KEY` se carga desde `.env` y no se almacena en PostgreSQL.
- `GROQ_API_KEY` se carga desde `.env` y no se almacena en PostgreSQL.
- El modelo Gemini configurado es `gemini-2.5-flash-lite`.
- El modelo Groq configurado es `openai/gpt-oss-20b`.
- En producción se activa redirección HTTPS, HSTS, cookies seguras, `X_FRAME_OPTIONS = 'DENY'` y protección de contenido.
- Los archivos media solo se sirven automáticamente cuando `DEBUG=True`.

## Usuario y roles

El modelo `applications.users.models.User` hereda de `AbstractBaseUser` y `PermissionsMixin`.

- Login mediante email (`USERNAME_FIELD = 'email'`)
- Email único
- Campos: nombre, apellidos, ocupación, género, fecha de nacimiento y teléfono
- Roles por `occupation`:
  - `'0'`: administrador
  - `'1'`: usuario
  - `'2'`: empleado
  - `'3'`: otro
- Un superusuario supera las comprobaciones de rol.
- `UserManager` proporciona `create_user()` y `create_superuser()`.

## URLs

- `/` - página inicial (`applications.home`)
- `/admin/` - panel de administración
- `/users/register/` - registro público
- `/users/login/` - inicio de sesión
- `/users/logout/` - cierre de sesión mediante POST
- `/users/update/` - cambio de contraseña
- `/users/` - dashboard, requiere autenticación
- `/users/lista/` - listado de usuarios, solo administradores
- `/chat/` - interfaz del chatbot, requiere autenticación
- `/chat/send/` - endpoint POST para enviar mensajes del chatbot

## Reglas de negocio actuales

- El registro público excluye la opción administrativa y el servicio fuerza `occupation=User.USUARIO`.
- El email duplicado se rechaza con un mensaje amigable.
- Las contraseñas deben coincidir y cumplir los validadores de Django.
- La fecha de nacimiento no puede ser futura.
- El login muestra un mensaje genérico ante credenciales inválidas.
- Cambiar la contraseña exige la contraseña actual, impide reutilizarla, valida la nueva y cierra la sesión.
- El logout solo acepta POST y utiliza CSRF.
- El listado de usuarios devuelve todos los usuarios ordenados por nombre y está protegido por `AdministradorPermisoMixin`.

## Chatbot

- Solo los usuarios autenticados pueden acceder al chatbot.
- Las conversaciones y mensajes se guardan en PostgreSQL asociados al usuario.
- Los roles de mensaje son `user` y `assistant`.
- Cada conversación conserva el proveedor seleccionado (`gemini` o `groq`).
- Cada conversación conserva también la fuente seleccionada: `blog` o `dummyjson`; la fuente puede cambiar al enviar una nueva pregunta. La fuente inicial predeterminada es `dummyjson` (Productos).
- Cada respuesta del asistente guarda el proveedor que la generó.
- El selector ofrece Gemini 2.5 Flash-Lite y Groq GPT-OSS 20B; Groq es el proveedor predeterminado.
- No existe fallback automático entre proveedores.
- La fuente `blog` consulta `https://bardalesweb.duckdns.org/api/entries/entries/` mediante `applications/chat/blog_api.py`.
- Django busca localmente por título, resumen, categoría y tags porque no se comprobó búsqueda server-side.
- El contenido HTML se limpia y se limita antes de enviarlo a Gemini.
- Se usan los últimos mensajes para resolver referencias como “el segundo”.
- La lista de artículos se cachea localmente durante 5 minutos.
- La fuente `dummyjson` consulta `https://dummyjson.com/products` mediante `applications/chat/dummyjson_api.py`.
- DummyJSON permite buscar productos, filtrar por categoría, consultar por ID y buscar SKU recorriendo páginas.
- Precio y stock se leen del producto; las comparaciones por ID se responden de forma determinista.
- Los datos de productos y artículos no se mezclan: cada pregunta usa exclusivamente la fuente activa.
- Para productos, la coincidencia exacta usa el nombre normalizado y tiene prioridad absoluta. Las coincidencias parciales requieren puntuación mínima de `0.60`; si dos resultados quedan a menos de `0.10`, se consideran ambiguos y se pide aclaración. Estos valores reflejan el buscador actual: una coincidencia exacta obtiene `1.0`, mientras que nombres incompletos o con errores leves quedan normalmente entre `0.60` y `0.90`; por debajo de `0.60` se rechazan.
- Se normalizan mayúsculas, minúsculas, acentos y apóstrofes; por ejemplo, `Dior J adore` coincide con `Dior J'adore`.
- Las comparaciones aceptan IDs, nombres, conectores `con`, `entre`, listas separadas por comas y consultas con instrucciones adicionales.
- Las respuestas de productos son deterministas: el modelo IA no genera ni modifica precios, stock, SKU o resultados de comparación.
- No se usan embeddings, RAG vectorial, pgvector, Redis, Celery, WhatsApp ni WebSockets.
- Las respuestas de Gemini y Groq son síncronas y están sujetas a las cuotas de sus respectivos proyectos.

## Pruebas

Las pruebas principales están en `applications/users/tests.py` e incluyen registro, duplicados, contraseñas, login, cambio de contraseña, permisos del listado y logout.

Las pruebas del chatbot están en `applications/chat/tests.py` e incluyen limpieza de HTML, búsqueda de artículos, autenticación, creación de conversaciones, guardado de mensajes, selección de proveedor, rechazo de mensajes vacíos, errores transaccionales, UUID inválido, coincidencias exactas y parciales, errores ortográficos leves, ambigüedad, SKU, campos faltantes, errores de API y comparaciones por nombres e IDs. Actualmente contiene 24 pruebas.

Se ejecutaron 20 consultas complejas reales contra DummyJSON y el servicio del chatbot: 20/20 fueron correctas o rechazaron correctamente el producto inexistente. Los precios, stocks y SKU coincidieron con la API.

`applications/home/tests.py` todavía no contiene pruebas funcionales.

Ejecutar con el entorno virtual del proyecto:

```powershell
venv\Scripts\python.exe manage.py check
venv\Scripts\python.exe manage.py test
venv\Scripts\python.exe manage.py migrate
venv\Scripts\python.exe manage.py runserver
```

Las pruebas necesitan PostgreSQL y permisos para crear la base de datos temporal de tests.

## Convenciones de trabajo

- Mantener secretos únicamente en `.env`; no agregarlos al repositorio.
- Configurar `GEMINI_API_KEY` y `GROQ_API_KEY` antes de probar el chatbot; nunca escribir las claves directamente en el código.
- DummyJSON no requiere autenticación. Mantener su cliente separado de `blog_api.py` y `providers.py`.
- Preferir cambios pequeños y conservar la estructura actual de apps y servicios.
- La lógica de negocio de usuarios está centralizada en `applications/users/services.py`.
- Usar los mixins de permisos existentes para vistas protegidas por rol.
- Mantener los formularios responsables de validaciones de entrada y las vistas delgadas.
- Verificar el código relevante antes de asumir que este documento está actualizado.

## Riesgos y pendientes conocidos

- Migrar Django 3.2.25 a una versión soportada, preferiblemente 4.2 LTS o superior.
- Añadir validaciones estrictas en `UserManager.create_superuser()` para garantizar `is_staff=True` e `is_superuser=True`.
- Revisar `SECURE_PROXY_SSL_HEADER` según el proxy real del despliegue.
- Definir correctamente `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS` y demás ajustes de producción.
- Añadir pruebas para usuarios inactivos, panel admin, superusuarios, validaciones de campos y configuración de producción.
- Manejar específicamente errores de cuota `429 ResourceExhausted` de Gemini para mostrar un mensaje claro y evitar tracebacks innecesarios en los logs.
- Evaluar una caché compartida si el chatbot se despliega con varios procesos o servidores.
- Revisar la cuota y facturación de Gemini; el nivel gratuito puede limitar las pruebas diarias y por minuto.
- Revisar la cuota y disponibilidad del modelo en Groq; se necesita una cuenta y una API key de Groq.
- Sustituir Tailwind Play CDN por un proceso de build para producción.
- Actualizar este archivo cuando cambien modelos, URLs, dependencias, permisos o comandos.

## Documentación relacionada

- `base.md`: documentación extensa del proyecto y su historial.
- `revisar_futuro.md`: revisión técnica y hallazgos pendientes.
