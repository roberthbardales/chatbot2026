# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Documentación adicional en español: `README.md`, `arquitectura.md` (flujos) y la carpeta `docs/`: `inventory-sync.md`, `inventory-search.md`, `usage-limits.md`, `whatsapp-worker.md` (supervisión), `whatsapp-outbox.md`, `whatsapp-panel.md`, `recovery-validation.md`, `agente.md` (agente con herramientas; el plan está en `plan-agente.md`).

## Preparación y verificación

- Ejecuta los comandos desde la raíz. En Windows/PowerShell no hace falta activar el entorno: usa `.\venv\Scripts\python.exe` (abajo, `python`).
- `.env` raíz es obligatorio: `SECRET_KEY`, `DB_NAME`, `DB_USER` y `DB_PASSWORD` son necesarios incluso para comandos de administración. No hay configuración alternativa con SQLite ni ajustes específicos para pruebas.
- Para desarrollo local por HTTP hacen falta `DEBUG=True` y `ALLOWED_HOSTS` adecuados: el valor predeterminado `DEBUG=False` fuerza HTTPS y cookies seguras.
- Solo PostgreSQL. pgvector ya no es necesario (las migraciones se reiniciaron: `core` tiene `0001_initial` y el modelo ya no incluye tablas heredadas); las pruebas solo necesitan poder crear su base de datos.
- `.envotro` (si existe) solo aporta valores predeterminados de IA/inventario de una lista permitida (`_OTHER_ENV_KEYS` en settings): no admite todas las claves, no importa credenciales de base de datos ni del framework, y las variables del entorno/`.env` tienen prioridad.
- `GOOGLE_SHEET_PRODUCTS_CSV_URL` debe devolver CSV directo: para Google Sheets usa `https://docs.google.com/spreadsheets/d/<ID>/export?format=csv` (hoja compartida con enlace). Los enlaces `/edit?usp=sharing` o `drive.google.com/file/.../view` devuelven HTML y `sync_inventory` falla con «Cabeceras CSV ausentes, duplicadas o incompletas».
- Si tras actualizar el código `sync_inventory` falla con «no existe la columna …» (p. ej. `core_product.available`), faltan migraciones: revisa con `python manage.py showmigrations` y aplica `python manage.py migrate`.
- Instalación nueva: `python manage.py migrate`; `python manage.py sync_inventory` descarga `GOOGLE_SHEET_PRODUCTS_CSV_URL` y carga el catálogo (usa `--dry-run` para solo ver la vista previa); después `python manage.py runserver`.

```powershell
python -m pip install -r requirements.txt
python manage.py migrate
python manage.py runserver
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py test                                   # toda la suite
python manage.py test applications.whatsapp             # una app
python manage.py test applications.core.tests.test_limits     # un módulo de pruebas
python manage.py test applications.core.tests.test_answers.InventoryConversationTests.test_customer_can_choose_option_and_then_follow_up   # una prueba
```

- No hay linter ni build frontend: la interfaz usa plantillas Django y Tailwind vía CDN en `templates/base.html`; reutiliza la plantilla base.
- Las pruebas mockean integraciones externas con `unittest.mock.patch` y la configuración con `override_settings`. Están separadas por tema en la carpeta `tests/` de cada app: `applications/whatsapp/tests/` (`test_worker`, `test_recovery`, `test_housekeeping`, `test_processing`, `test_outbox`, `test_panel`, `test_human_mode`) y `applications/core/tests/` (`test_limits`, `test_inventory_sync`, `test_backup_restore`, `test_answers`, `test_search`, `test_search_units`, `test_availability`, `test_display`).
- El ensayo real de respaldo (`test_backup_restore`) exige herramientas PostgreSQL de la misma versión mayor y una plantilla sin tablas de aplicación. Usa únicamente bases `test_*` y destinos nuevos `test_rehearsal_*`; no eleves privilegios ni restaures sobre la base habitual.

## Operaciones sensibles (no ejecutar como diagnóstico)

- `sync_inventory` sin flags **escribe**: descarga, valida el catálogo bloqueado y aplica en una transacción (sin huella ni confirmación previa); `--dry-run` es la vista previa sin escrituras; las bajas masivas necesitan además `--allow-large-drop`. No descargues ni apliques el catálogo real por rutina: usa mocks y las funciones `preview_inventory()`/`inventory_status()` en pruebas.
- `whatsapp_worker --once` **no** es una simulación: procesa mensajes pendientes y **envía WhatsApps reales**. Con `AI_AGENT_ENABLED=True` en el `.env` responde con el agente.
- Las pruebas del worker fijan `AI_AGENT_ENABLED=False` (`setUpModule`) y las del agente simulan `chat_with_tools`: ninguna debe llamar a OpenRouter ni depender del `.env`. Al añadir pruebas que procesen mensajes, hazlo igual.
- `cleanup_chat_limits` (en `core`) elimina registros caducados de límites de uso.
- `cleanup_whatsapp_data` elimina latidos de workers antiguos (`--dry-run` solo cuenta; no toca mensajes ni `Outbox`).
- `probar_agente` es un chat interactivo que **llama a la IA real** (gasta cupo); no escribe en la base ni envía WhatsApps.
- Solo lectura: `sync_inventory --dry-run`, `whatsapp_healthcheck` (devuelve salida no exitosa si falta latido/progreso o hay errores).

## Arquitectura

Aplicaciones en `applications/`: `core`, `whatsapp`, `users`, `home`. El producto es un bot de WhatsApp con un panel de operadores; ya no hay chat web.

- **Core** (`applications/core`, motor sin vistas ni URLs): `agent.py::answer_with_agent` (agente conversacional con herramientas de solo lectura y marcadores `{precio:SKU}`/`{stock:SKU}` validados; apagado por defecto con `AI_AGENT_ENABLED`; ver `docs/agente.md`); `services.py::answer_sheet_message` (motor de plantillas y respaldo del agente: búsqueda en el catálogo local + selección validada de la IA); `inventory.py` busca en el catálogo local; `inventory_source.py` lee y valida la hoja CSV; `inventory_sync.py` sincroniza (vista previa/aplicar); `responses.py` redacta y valida las respuestas; `providers.py` integra OpenRouter; `limits.py` aplica límites de uso y control de consumo de IA (modelos de uso en `models.py`; ver `docs/usage-limits.md`). OpenRouter y Google Sheets son las opciones predeterminadas activas.
- **Fuente única**: el catálogo sale solo de la hoja de Google (`GOOGLE_SHEET_PRODUCTS_CSV_URL`), que puede ser el Excel subido tal cual: acepta `ID producto`/`SKU`, `Producto`, `Marca`, `Tipo`, `Precio`, `Stock` (o `Inventario`/`Existencias`), `Disponible`, `Precio anterior` y `URL` (ver `docs/inventory-sync.md`). Si hay `Stock`, la cantidad manda sobre `Disponible` (>0 disponible, 0 agotado); sin `Stock`, «No» equivale a stock 0 y «Sí» marca `Product.available` sin cifra. Las imágenes y demás columnas se ignoran. El blog, DummyJSON, los combos y los embeddings se eliminaron.
- **WhatsApp** (`applications/whatsapp`): reutiliza los servicios de `core` vía `services.py`; `meta_api.py` habla con Meta. Arquitectura de cola: el webhook `/whatsapp/webhook/` valida el HMAC de Meta sobre el cuerpo original, deduplica y persiste antes de acusar recibo, sin descargar imágenes, llamar a IA ni enviar nada. Un worker independiente y obligatorio para responder (`whatsapp_worker`, `worker_runtime.py`) procesa mensajes y `Outbox` (`process_outbox()`); revisa las colas cada `WHATSAPP_WORKER_POLL_SECONDS` (2 s por defecto, `--poll-seconds`) y respeta debounce y límites. Recibir otro mensaje entrante ya no dispara el procesamiento. El worker no sincroniza inventario: cargar el catálogo (`sync_inventory`) es una operación separada. Supervisión en `docs/whatsapp-worker.md`.
- **Panel de operadores** (`/whatsapp/`, `views.py` + `panel.py` + `templates/whatsapp/dashboard.html`, ver `docs/whatsapp-panel.md`): conversaciones, estado de cada envío, salud del worker, colas, catálogo y consumo de IA. Permite el **modo humano** (`WhatsAppContact.mode`: el bot no responde ni consume cupo) y el envío manual, que solo crea entradas `Outbox` y no llama a Meta en esa petición. «Encolado» ≠ «enviado».
- **Acceso**: el webhook firmado con HMAC es la única entrada pública de WhatsApp; el panel y su API exigen superusuario u ocupación `'0'` (admin) / `'2'` (empleado): iniciar sesión no basta, y las acciones llevan CSRF. `users.User` autentica por correo, no por nombre de usuario; `users` reutiliza `core.limits` para limitar los intentos de login.

## Reglas del inventario

- Las respuestas del catálogo deben basarse en el inventario recuperado: conserva la validación de productos y cifras y el comportamiento sin coincidencias, también para reconocimiento de imágenes.
- La búsqueda es textual sobre productos locales activos, no vectorial. Los alias y la normalización de cabeceras CSV están definidos en `inventory_source.py`.
- Los productos requieren SKU y nombre; los SKU duplicados tras normalizar hacen fallar la sincronización. Los precios usan `Decimal` y los CSV inválidos se rechazan.
- La sincronización conserva IDs por SKU, desactiva ausentes sin borrar filas y bloquea bajas masivas sin `--allow-large-drop`.
