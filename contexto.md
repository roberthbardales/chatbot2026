# Contexto del proyecto: chatbot2026

Última actualización: 2026-09-14

## Resumen

Aplicación web Django para gestión de usuarios, autenticación y un MVP de chatbot basado en los artículos de una API pública externa. La base continúa preparada para evolucionar hacia un sistema de reservas tipo clínica.

## Stack

- Python 3.12+
- Django 4.2.25 LTS
- PostgreSQL mediante `psycopg2-binary`
- `pgvector` para búsqueda vectorial en PostgreSQL
- Configuración por variables de entorno con `django-environ`
- Plantillas HTML con Tailwind Play CDN
- Gemini API mediante `google-generativeai`, usando `gemini-2.5-flash-lite`
- Embeddings mediante Gemini `models/gemini-embedding-001`
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
│   └── whatsapp/        # webhook, contactos y mensajes de WhatsApp
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
- `CHAT_MAX_MESSAGE_LENGTH` limita a 4000 caracteres los mensajes del chat web.
- `GEMINI_API_KEY` se carga desde `.env` y no se almacena en PostgreSQL.
- `GROQ_API_KEY` se carga desde `.env` y no se almacena en PostgreSQL.
- `WHATSAPP_PHONE_NUMBER_ID`, `WHATSAPP_VERIFY_TOKEN`, `WHATSAPP_ACCESS_TOKEN` y `WHATSAPP_APP_SECRET` se cargan desde `.env`.
- `WHATSAPP_API_VERSION` se carga desde `.env`; durante la validación actual está configurada como `v26.0`.
- `ALLOWED_HOSTS` incluye `localhost`, `127.0.0.1` y los dominios `ngrok-free.app` y `ngrok-free.dev` para pruebas locales.
- `BLOG_SOURCE_ENABLED` controla la disponibilidad de la fuente Blog y actualmente está desactivada por defecto.
- `EMBEDDING_MODEL` configura el modelo de embeddings; el valor predeterminado es `models/gemini-embedding-001`.
- `EMBEDDING_DIMENSIONS` configura la dimensión de los vectores y actualmente es `768`.
- `VECTOR_SEARCH_THRESHOLD` configura la similitud mínima y actualmente es `0.62`.
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
- `/chat/` - interfaz pública del chatbot
- `/chat/send/` - endpoint POST para enviar mensajes del chatbot
- `/whatsapp/webhook/` - verificación y recepción del webhook de Meta WhatsApp

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

- El chat web puede utilizarse sin autenticación; los usuarios autenticados conservan sus conversaciones asociadas a su cuenta.
- Las conversaciones anónimas se guardan en PostgreSQL sin usuario y se identifican mediante la sesión del navegador, con un máximo de 20 IDs recordados por sesión.
- Las conversaciones y mensajes autenticados se guardan en PostgreSQL asociados al usuario.
- La migración `0007_conversation_anonymous` permite que `Conversation.user` sea nulo.
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
- El RAG vectorial de DummyJSON utiliza el modelo `ProductDocument` y embeddings almacenados en PostgreSQL mediante `pgvector`.
- Cada producto se indexa inicialmente como un único documento vectorial.
- El comando `venv\Scripts\python.exe manage.py index_dummyjson` descarga los productos, calcula hashes y genera embeddings solo para productos nuevos o modificados.
- La búsqueda semántica usa similitud coseno como respaldo cuando la búsqueda textual no encuentra resultados.
- La extensión de servidor `vector` está instalada y activa en PostgreSQL; la migración `0006_productdocument` ya fue aplicada.
- Actualmente hay 194 productos de DummyJSON indexados en la base de datos local.
- Precio y stock se leen del producto; las comparaciones por ID se responden de forma determinista.
- Los datos de productos y artículos no se mezclan: cada pregunta usa exclusivamente la fuente activa.
- Para productos, la coincidencia exacta usa el nombre normalizado y tiene prioridad absoluta. Las coincidencias parciales requieren puntuación mínima de `0.60`; si dos resultados quedan a menos de `0.10`, se consideran ambiguos y se pide aclaración. Estos valores reflejan el buscador actual: una coincidencia exacta obtiene `1.0`, mientras que nombres incompletos o con errores leves quedan normalmente entre `0.60` y `0.90`; por debajo de `0.60` se rechazan.
- Se normalizan mayúsculas, minúsculas, acentos y apóstrofes; por ejemplo, `Dior J adore` coincide con `Dior J'adore`.
- Las comparaciones aceptan IDs, nombres, conectores `con`, `entre`, listas separadas por comas y consultas con instrucciones adicionales.
- Las respuestas de productos son deterministas: el modelo IA no genera ni modifica precios, stock, SKU o resultados de comparación.
- El Blog mantiene su RAG textual y está deshabilitado temporalmente mediante `BLOG_SOURCE_ENABLED=False`; su código, modelos, migraciones y pruebas no fueron eliminados.
- DummyJSON usa RAG vectorial para búsquedas semánticas, mientras que ID, SKU, precio, stock y comparaciones continúan siendo deterministas.
- Los mensajes cotidianos todavía no tienen una capa dedicada de intención: saludos como `hola amigo` pueden terminar tratándose como búsquedas de productos.
- El chat web rechaza mensajes vacíos y mensajes de más de `CHAT_MAX_MESSAGE_LENGTH` caracteres.
- El plan para hacer la conversación más natural está documentado en `natural.md`; contempla saludos, despedidas, agradecimientos, búsquedas amplias y aclaraciones usando el historial.
- No se usan Redis, Celery ni WebSockets.
- Las respuestas de Gemini y Groq son síncronas y están sujetas a las cuotas de sus respectivos proyectos.

## WhatsApp

- La aplicación `applications/whatsapp` recibe mensajes mediante `/whatsapp/webhook/`.
- La migración inicial de WhatsApp ya fue aplicada.
- Se almacenan contactos en `WhatsAppContact` y mensajes recibidos en `WhatsAppMessage`.
- El webhook valida el `hub.verify_token` durante la configuración de Meta y la firma `X-Hub-Signature-256` en las notificaciones.
- El envío de respuestas utiliza la API de WhatsApp Cloud mediante `WHATSAPP_ACCESS_TOKEN` y `WHATSAPP_PHONE_NUMBER_ID`.
- La carga de credenciales se realiza desde `D:\Proyectos GIT\chatbot2026\.env` mediante `django-environ` en `chatbot2026/settings.py`.
- La URL de envío utiliza la versión `v26.0` de Graph API durante la validación actual.
- Django se ejecuta localmente en `127.0.0.1:8000`.
- Ngrok se utiliza para exponer `http://localhost:8000`; la URL pública puede cambiar al reiniciar el túnel.
- La Callback URL de Meta debe coincidir con la URL pública activa de ngrok y terminar en `/whatsapp/webhook/`.
- El webhook ya fue verificado y el campo `messages` aparece suscrito en Meta.
- Los mensajes reales llegan al webhook, el chatbot genera la respuesta y Meta acepta el envío con `HTTP 200`.
- El envío fue validado con un mensaje real (`hola`) desde el número autorizado `51960174778`; Meta devolvió un ID de mensaje saliente.
- Un token anterior produjo `HTTP 401` con `OAuthException` código `190`; el token actualizado permite enviar respuestas correctamente.
- Se añadieron logs temporales seguros en `applications/whatsapp/services.py` y `applications/whatsapp/meta_api.py` para registrar recepción, generación y respuesta de Meta sin exponer tokens.
- El webhook ignora correctamente los payloads ficticios de Meta cuyo `phone_number_id` no coincide con `WHATSAPP_PHONE_NUMBER_ID`.
- Los eventos de estado adicionales de Meta pueden generar varios `POST /whatsapp/webhook/` con respuesta `HTTP 200` después del mensaje original.
- El warning de `google.api_core` indica que Python 3.10 dejará de recibir soporte en nuevas versiones después del 2026-10-04; conviene migrar a Python 3.11 o superior.
- La página de privacidad fue retirada temporalmente: ya no existe la ruta `/privacidad/` ni el enlace correspondiente en la cabecera.

## Pruebas

Las pruebas principales están en `applications/users/tests.py` e incluyen registro, duplicados, contraseñas, login, cambio de contraseña, permisos del listado y logout.

Las pruebas del chatbot están en `applications/chat/tests.py` e incluyen acceso anónimo, creación de conversaciones, guardado de mensajes, selección de proveedor, límite de longitud, rechazo de mensajes vacíos, limpieza de HTML, búsqueda de artículos, errores transaccionales, UUID inválido, coincidencias exactas y parciales, errores ortográficos leves, ambigüedad, SKU, campos faltantes, errores de API y comparaciones por nombres e IDs.

Se ejecutaron 20 consultas complejas reales contra DummyJSON y el servicio del chatbot: 20/20 fueron correctas o rechazaron correctamente el producto inexistente. Los precios, stocks y SKU coincidieron con la API.

`applications/home/tests.py` todavía no contiene pruebas funcionales.

Ejecutar con el entorno virtual del proyecto:

```powershell
venv\Scripts\python.exe manage.py check
venv\Scripts\python.exe manage.py test
venv\Scripts\python.exe manage.py migrate
venv\Scripts\python.exe manage.py index_dummyjson
venv\Scripts\python.exe manage.py runserver
```

Las pruebas necesitan PostgreSQL y permisos para crear la base de datos temporal de tests. La migración vectorial requiere además permisos suficientes para crear la extensión `vector` en la base temporal.

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
- Optimizar el indexador para usar embeddings por lotes si el catálogo de DummyJSON crece; actualmente genera embeddings individualmente.
- Configurar una base de tests con `pgvector` habilitado o permisos adecuados para ejecutar todas las pruebas.
- Implementar la capa de conversación natural descrita en `natural.md`, antes de ejecutar búsquedas textuales o vectoriales.
- Sustituir Tailwind Play CDN por un proceso de build para producción.
- Actualizar este archivo cuando cambien modelos, URLs, dependencias, permisos o comandos.
- Python 3.12 y Django 4.2.25 se usan como objetivo de la migración de dependencias; debe verificarse la instalación del entorno nuevo antes de retirar el entorno anterior.

## Documentación relacionada

- `base.md`: documentación extensa del proyecto y su historial.
- `revisar_futuro.md`: revisión técnica y hallazgos pendientes.
- `natural.md`: plan para detectar conversación cotidiana, búsquedas amplias y aclaraciones.
