# chatbot2026

Aplicación Django para gestión de usuarios y autenticación.

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
```

## Ejecución

```powershell
python manage.py migrate
python manage.py sync_inventory
python manage.py runserver
```

Para recibir y enviar mensajes de WhatsApp, ejecuta el worker en otra terminal:

```powershell
python manage.py whatsapp_worker
```

El webhook de Meta solo guarda los mensajes y responde rápidamente. El worker
agrupa mensajes consecutivos, consulta Google Sheets, usa OpenRouter y procesa
la cola `Outbox` mediante Meta WhatsApp Cloud API. Las imágenes se descargan
temporalmente, se envían al modelo y no se guardan permanentemente.

El dashboard operativo está disponible en `/whatsapp/` para usuarios autenticados.
El código de DummyJSON permanece en el proyecto, pero no es una fuente activa.

## Pruebas

```powershell
python manage.py test
```

El registro público crea usuarios normales. Las cuentas administrativas deben crearse mediante un proceso controlado, por ejemplo `createsuperuser` o el panel de administración.
