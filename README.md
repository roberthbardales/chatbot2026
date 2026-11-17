# chatbot2026

Aplicación Django para gestión de usuarios y atención automatizada de WhatsApp mediante Meta WhatsApp Cloud API. Usa Google Sheets como inventario y OpenRouter como único proveedor de IA.

## Cómo funciona y qué puede hacer

- Responde consultas de clientes por WhatsApp.
- Consulta productos, precios y stock del catálogo local sincronizado desde Google Sheets.
- Analiza imágenes recibidas por WhatsApp y busca coincidencias reales del inventario; requiere un modelo de OpenRouter compatible con imágenes.
- Conserva el historial de cada contacto para recordar el producto elegido.
- Utiliza Python, Django 6.1.3, PostgreSQL y plantillas con Tailwind Play CDN; no requiere compilación frontend.

Para los flujos y diagramas, consulta [arquitectura.md](arquitectura.md). Para estructura del código, configuración y detalles técnicos, consulta [contexto.md](contexto.md).

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

Para desarrollo local por HTTP usa `DEBUG=True`. En producción usa `DEBUG=False`, HTTPS y los dominios correspondientes en `ALLOWED_HOSTS`. Las variables `SECRET_KEY`, `DB_NAME`, `DB_USER` y `DB_PASSWORD` son obligatorias también para los comandos de administración.

```text
SECRET_KEY=una-clave-secreta-larga
DEBUG=True
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
```

`sync_inventory` descarga la hoja, la valida y carga los productos y su stock en la
base de datos en un solo paso. Con `--dry-run` solo muestra altas, cambios y bajas sin
modificar nada. Si detecta bajas masivas se detiene y exige `--allow-large-drop`.
Después inicia el servidor con `python manage.py runserver`.
Los productos se actualizan por SKU; los ausentes se desactivan, sin borrar sus
IDs. La hoja puede ser el Excel subido tal cual (columnas `Producto`, `Marca`,
`Tipo`, `Precio`, `Precio anterior`, `Stock`, `URL`, `ID producto`; `Disponible` es opcional).
La URL de `GOOGLE_SHEET_PRODUCTS_CSV_URL` debe devolver CSV directo (en Google Sheets,
`.../d/<ID>/export?format=csv`). Consulta `docs/inventory-sync.md` para validación y bajas masivas.

Para WhatsApp, configura en Meta Developers una Callback URL pública HTTPS que
termine en `/whatsapp/webhook/`. El servidor valida y guarda cada mensaje;
un worker independiente es obligatorio para procesarlo y responder:

```powershell
python manage.py runserver 0.0.0.0:8000
```

El webhook valida la firma de Meta sobre el cuerpo original, guarda el mensaje
y confirma la recepción sin llamar a servicios externos. El worker consulta el
inventario local, usa OpenRouter y envía mediante Meta WhatsApp Cloud API. Las
imágenes se descargan temporalmente, se analizan para extraer palabras clave y
se comparan con los productos del inventario; no se guardan permanentemente.
Solo se devuelven coincidencias reales del catálogo. Una imagen de un producto
que no existe en Google Sheets puede ser identificada visualmente, pero se
responderá que no hay coincidencias.

En otra terminal o como servicio supervisado, ejecuta el worker continuo:

```powershell
python manage.py whatsapp_worker
```

**Advertencia:** `whatsapp_worker` y `whatsapp_worker --once` procesan y envían
mensajes reales; no son simulaciones. El worker ya no sincroniza inventario.
`sync_inventory` se ejecuta por separado y escribe en el catálogo (sin borrar
filas; usa `--dry-run` para solo mirar). No ejecutes el worker ni sincronices
como diagnóstico de solo lectura. Para comprobar el worker sin enviar mensajes,
usa `python manage.py whatsapp_healthcheck`.

No hay panel web: la salud del worker se comprueba con
`python manage.py whatsapp_healthcheck`. Consulta
`docs/whatsapp-worker.md` para configurar arranque, reinicio y monitorización en
Windows o Linux. Sin worker, los mensajes quedan pendientes aunque lleguen otros.
Gemini, Groq, DummyJSON y el RAG vectorial se eliminaron. El inventario
activo es Google Sheets y la búsqueda de productos es textual, sin embeddings.
El funcionamiento real depende de las credenciales, del modelo elegido y de la configuración HTTPS del despliegue; el código por sí solo no acredita el estado de producción.

## Pruebas

```powershell
python manage.py test
```

También puedes ejecutar `python manage.py check` y `python manage.py makemigrations --check --dry-run`. Las pruebas requieren PostgreSQL con permisos para crear la base de pruebas; no hay configuración alternativa con SQLite.

El registro público crea usuarios normales. Las cuentas administrativas deben crearse mediante un proceso controlado, por ejemplo `createsuperuser` o el panel de administración.
