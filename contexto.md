# Contexto del proyecto: chatbot2026

Última actualización: 2026-09-15

## Resumen

Aplicación Django para usuarios, chat web y atención automatizada de WhatsApp mediante Meta WhatsApp Cloud API. La fuente activa del inventario son dos hojas públicas de Google Sheets y el proveedor de IA activo es OpenRouter.

Neonize no se utiliza. No hay QR ni sesión local de WhatsApp.

## Stack

- Python 3.12+
- Django 4.2.25 LTS
- PostgreSQL mediante `psycopg2-binary`
- `django-environ`
- Meta WhatsApp Cloud API
- OpenRouter con el modelo configurado en `OPENROUTER_MODEL`
- Google Sheets publicados como CSV
- Tailwind Play CDN para las plantillas actuales
- Zona horaria: `America/Lima`
- Idioma: `es-pe`

## Estructura relevante

```text
chatbot2026/
├── manage.py
├── requirements.txt
├── .env                         # local, no versionar
├── .envotro                     # referencia local, ignorado por Git
├── chatbot2026/                 # settings, URLs, ASGI y WSGI
├── applications/
│   ├── home/                    # página inicial pública
│   ├── users/                   # usuario custom, autenticación y roles
│   ├── chat/                    # chat web, OpenRouter e inventario
│   └── whatsapp/                # webhook Meta, procesamiento y Outbox
├── templates/
│   └── whatsapp/                # dashboard operativo básico
├── static/
└── fixtures/
```

## Configuración

La configuración principal está en `chatbot2026/settings.py`.

- `AUTH_USER_MODEL = 'users.User'`
- PostgreSQL usa `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST` y `DB_PORT` del `.env`.
- `SECRET_KEY`, `DEBUG` y `ALLOWED_HOSTS` permanecen en el `.env` actual.
- Las credenciales de Meta son `WHATSAPP_VERIFY_TOKEN`, `WHATSAPP_ACCESS_TOKEN`, `WHATSAPP_APP_SECRET` y `WHATSAPP_PHONE_NUMBER_ID`.
- `WHATSAPP_API_VERSION` define la versión de Graph API.
- OpenRouter usa `OPENROUTER_API_KEY`, `OPENROUTER_MODEL` y `SYSTEM_PROMPT`.
- Las variables necesarias también pueden leerse desde `.envotro`; solo se aceptan variables de OpenRouter, Google Sheets, historial, debounce e inventario. Las credenciales de base de datos y Neonize de ese archivo se ignoran.
- `.envotro` está incluido en `.gitignore` y no debe versionarse.

Variables de inventario y procesamiento:

```env
GOOGLE_SHEET_PRODUCTS_CSV_URL=https://...
GOOGLE_SHEET_COMBOS_CSV_URL=https://...
INVENTORY_SYNC_MINUTES=5
MAX_PRODUCTS_IN_CONTEXT=5
HISTORY_LIMIT=20
MESSAGE_DEBOUNCE_SECONDS=6
WHATSAPP_MAX_MESSAGE_LENGTH=2000
WHATSAPP_MAX_IMAGE_BYTES=10485760
```

## Usuarios y permisos

`applications.users.models.User` utiliza email como identificador de login.

- `'0'`: administrador
- `'1'`: usuario
- `'2'`: empleado
- `'3'`: otro
- El superusuario supera las comprobaciones de rol.
- El dashboard operativo de WhatsApp y sus APIs solo permiten administradores, empleados y superusuarios.

## URLs

- `/` - página inicial pública
- `/admin/` - administración Django
- `/users/register/` - registro público
- `/users/login/` - inicio de sesión
- `/users/logout/` - cierre de sesión mediante POST
- `/users/` - dashboard de usuario
- `/chat/` - interfaz pública del chatbot
- `/chat/send/` - endpoint POST del chat web
- `/whatsapp/` - dashboard operativo de WhatsApp
- `/whatsapp/webhook/` - verificación y recepción del webhook de Meta
- `/whatsapp/api/connection/status` - estado de configuración de Meta
- `/whatsapp/api/conversations` - listado de contactos/conversaciones
- `/whatsapp/api/conversations/<id>` - eliminación de una conversación
- `/whatsapp/api/messages/<id>` - historial de mensajes
- `/whatsapp/api/messages/<id>/send` - respuesta manual
- `/whatsapp/api/mode/<id>` - cambio entre IA y humano

## Chat web

- El chat web puede usarse sin autenticación.
- Las conversaciones autenticadas se asocian al usuario; las anónimas se identifican mediante sesión.
- El único proveedor activo es OpenRouter. Gemini y Groq están desactivados y no se ofrecen como proveedores.
- La fuente activa del inventario es `google_sheets`.
- La fuente Blog continúa desactivada por defecto mediante `BLOG_SOURCE_ENABLED=False`.
- DummyJSON y el RAG vectorial están desactivados; el flujo activo usa búsquedas textuales en Google Sheets.
- El historial usado para OpenRouter está limitado por `HISTORY_LIMIT`.
- El chat web continúa procesando la respuesta de forma síncrona y la devuelve en la misma solicitud.

## OpenRouter

`applications/chat/providers.py` llama a `https://openrouter.ai/api/v1/chat/completions`.

- Se envían `SYSTEM_PROMPT`, historial, pregunta actual y contexto de inventario.
- Las imágenes se envían como `data URL` en formato multimodal.
- Si OpenRouter falla durante WhatsApp, se envía inmediatamente al cliente:

```text
No pude procesar tu mensaje en este momento. Por favor, inténtalo nuevamente en unos minutos.
```

- El detalle técnico queda en los logs y el mensaje original se marca como fallido.
- No se muestran claves API ni detalles internos al cliente.

## Google Sheets e inventario

La sincronización está en `applications/chat/inventory.py` y el comando en `sync_inventory`.

Hoja de productos:

```text
sku, name, category, brand, model, specs, price, stock,
unit, keywords, description, active
```

Hoja de combos:

```text
name, description, product_skus, combo_price, active
```

Características:

- Las hojas son públicas y se descargan como CSV sin credenciales de Google.
- Se aceptan nombres de columnas equivalentes con diferencias de mayúsculas, tildes y traducción básica.
- Los productos se guardan en `applications.chat.models.Product`.
- Los combos se guardan en `applications.chat.models.Combo`.
- Se validan SKU, nombre, duplicados e inventario vacío antes de reemplazar los datos.
- Si la descarga o validación falla, la transacción no reemplaza el inventario anterior.
- Los combos relacionados se agregan al contexto cuando comparten SKU con productos encontrados.
- La sincronización manual del inventario se ejecuta con `sync_inventory`; el worker opcional también puede sincronizarlo según `INVENTORY_SYNC_MINUTES`.
- La sincronización manual se ejecuta con:

```powershell
venv\Scripts\python.exe manage.py sync_inventory
```

Después de la última sincronización verificada había 21 productos y 5 combos.

## WhatsApp con Meta Cloud API

El flujo activo es:

```text
WhatsApp -> Meta Webhook -> Django encola y procesa -> OpenRouter -> Outbox -> Meta API
```

El webhook valida:

- `hub.verify_token` durante la verificación inicial.
- Firma `X-Hub-Signature-256` en los POST.
- `phone_number_id` para ignorar payloads de otra aplicación.
- Mensajes de tipo texto e imagen.
- Duplicados mediante el `message_id` único.

Los mensajes se almacenan en `WhatsAppMessage`. El webhook procesa inmediatamente los mensajes nuevos y el texto se limita mediante `WHATSAPP_MAX_MESSAGE_LENGTH`.

Las imágenes se descargan temporalmente desde Meta, se convierten a Base64 y se envían a OpenRouter para extraer palabras clave del producto visible. Esas palabras se usan para buscar coincidencias en Google Sheets y la imagen vuelve a acompañar la respuesta final. No se guardan permanentemente; la descarga respeta `WHATSAPP_MAX_IMAGE_BYTES` y conserva el caption.

## Procesamiento y cola

El webhook procesa directamente los mensajes en `applications/whatsapp/views.py`, por lo que el worker no es necesario para el funcionamiento normal de WhatsApp.

`Outbox` conserva la respuesta antes de enviarla a Meta. El worker permanece disponible como mecanismo opcional de recuperación de mensajes pendientes.

```powershell
venv\Scripts\python.exe manage.py whatsapp_worker --once  # opcional, reintenta pendientes
```

Para una iteración:

```powershell
venv\Scripts\python.exe manage.py whatsapp_worker --once
```

Responsabilidades del procesamiento directo:

- Validar y deduplicar el webhook de Meta.
- Respetar el modo IA/humano.
- Generar respuestas con OpenRouter.
- Crear y enviar respuestas mediante `Outbox`.
- Registrar intentos y errores de salida.

Si Meta rechaza un envío, se conserva como no enviado. Puede reintentarse ejecutando opcionalmente el worker.

## Modo humano

Cada `WhatsAppContact` tiene modo `ai` o `human`.

- `ai`: el webhook genera respuestas automáticas.
- `human`: los mensajes entrantes se almacenan, pero no se consulta OpenRouter.
- Las respuestas manuales se agregan a `Outbox` desde el dashboard.

## Base de datos y migraciones

La base de datos fue reiniciada durante esta adaptación. Se conservaron las migraciones y se aplicaron las nuevas migraciones de productos, combos, proveedor OpenRouter, cola, modos y multimedia.

Modelos principales:

- `Conversation` y `Message`: chat web.
- `Product` y `Combo`: inventario de Google Sheets.
- `ProductDocument`: código histórico de DummyJSON y pgvector, inactivo.
- `WhatsAppContact`: contacto, modo y última actividad.
- `WhatsAppMessage`: mensajes entrantes y respuestas locales.
- `Outbox`: cola de salida de Meta.

## Comandos habituales

```powershell
venv\Scripts\python.exe manage.py check
venv\Scripts\python.exe manage.py migrate
venv\Scripts\python.exe manage.py sync_inventory
venv\Scripts\python.exe manage.py runserver
venv\Scripts\python.exe manage.py whatsapp_worker --once  # opcional, reintenta pendientes
venv\Scripts\python.exe manage.py test
```

Durante desarrollo, Meta necesita una Callback URL pública de ngrok que termine en `/whatsapp/webhook/`. Para el flujo normal solo debe ejecutarse el servidor web; el worker es opcional para reintentos.

## Pruebas verificadas

- `manage.py check`: correcto.
- `manage.py makemigrations --check`: sin cambios pendientes.
- `manage.py test`: verificado en la última ejecución del proyecto.
- Sincronización real de Google Sheets: 21 productos y 5 combos.
- Flujo real de WhatsApp con texto: webhook, OpenRouter y envío mediante Meta verificados correctamente sin ejecutar el worker.

## Pendientes y riesgos

- Probar el flujo completo con una imagen real desde Meta después de esta adaptación visual.
- Construir una interfaz JavaScript completa para el dashboard; actualmente existen el endpoint y una plantilla operativa básica.
- El envío exactamente una sola vez no puede garantizarse si el proceso cae después de que Meta acepta el mensaje y antes de actualizar `Outbox`.
- El procesamiento síncrono del webhook depende de que Google Sheets, OpenRouter y Meta respondan dentro del tiempo permitido por Meta; para mayor robustez puede añadirse una cola supervisada.
- Revisar rotación de la clave de OpenRouter y credenciales que hayan estado expuestas en archivos locales.
- Desactivar `DEBUG` y revisar `ALLOWED_HOSTS`, HTTPS, CSRF y proxy inverso antes del despliegue.

## Convenciones

- No versionar `.env`, `.envotro` ni credenciales.
- Mantener Meta Cloud API como transporte; no agregar Neonize salvo decisión explícita.
- No reactivar DummyJSON como fuente sin cambiar expresamente la configuración y las pruebas.
- Ejecutar `check`, migraciones y tests después de modificar modelos o configuración.
