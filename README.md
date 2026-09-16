# chatbot2026

Aplicación Django para gestión de usuarios, chat web y atención automatizada de WhatsApp mediante Meta WhatsApp Cloud API. Usa Google Sheets como inventario y OpenRouter como único proveedor de IA.

## Requisitos

- Python 3.10+
- PostgreSQL

## Instalación

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Crea un archivo `.env` basándote en las variables necesarias del proyecto:

```text
SECRET_KEY=una-clave-secreta-larga
DEBUG=False
ALLOWED_HOSTS=localhost,127.0.0.1
DB_NAME=db_chatbot2026
DB_USER=usuario
DB_PASSWORD=contraseña
DB_HOST=localhost
DB_PORT=5432
OPENROUTER_API_KEY=tu-clave-de-openrouter
OPENROUTER_MODEL=openai/gpt-4o-mini
SYSTEM_PROMPT=Eres un asistente útil y profesional.
GOOGLE_SHEET_PRODUCTS_CSV_URL=https://docs.google.com/spreadsheets/d/.../export?format=csv
GOOGLE_SHEET_COMBOS_CSV_URL=https://docs.google.com/spreadsheets/d/.../export?format=csv
INVENTORY_SYNC_MINUTES=5
MAX_PRODUCTS_IN_CONTEXT=5
HISTORY_LIMIT=20
MESSAGE_DEBOUNCE_SECONDS=6
WHATSAPP_VERIFY_TOKEN=token-de-verificacion
WHATSAPP_ACCESS_TOKEN=token-de-meta
WHATSAPP_APP_SECRET=secreto-de-la-app
WHATSAPP_PHONE_NUMBER_ID=id-del-numero-de-whatsapp
WHATSAPP_API_VERSION=v26.0
```

## Ejecución

```powershell
python manage.py migrate
python manage.py sync_inventory
python manage.py runserver
```

Para WhatsApp, configura en Meta Developers una Callback URL pública HTTPS que
termine en `/whatsapp/webhook/`. El servidor procesa directamente cada mensaje;
no es necesario ejecutar el worker:

```powershell
python manage.py runserver 0.0.0.0:8000
```

El webhook valida la firma de Meta, guarda el mensaje, consulta Google Sheets,
usa OpenRouter y envía la respuesta mediante Meta WhatsApp Cloud API. Las
imágenes se descargan temporalmente, se analizan para extraer palabras clave y
se comparan con los productos del inventario; no se guardan permanentemente.
Solo se devuelven coincidencias reales del catálogo. Una imagen de un producto
que no existe en Google Sheets puede ser identificada visualmente, pero se
responderá que no hay coincidencias.

El worker queda disponible de forma opcional para reintentar mensajes pendientes:

```powershell
python manage.py whatsapp_worker --once
```

El dashboard operativo está disponible en `/whatsapp/` para usuarios autenticados.
Gemini, Groq, DummyJSON y el RAG vectorial están desactivados. El inventario
activo es Google Sheets y la búsqueda de productos es textual, sin embeddings.

## Pruebas

```powershell
python manage.py test
```

El registro público crea usuarios normales. Las cuentas administrativas deben crearse mediante un proceso controlado, por ejemplo `createsuperuser` o el panel de administración.
