# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Documentación adicional en español: `README.md`, `arquitectura.md` (flujos) y la carpeta `docs/`: `inventory-sync.md`, `inventory-search.md`, `usage-limits.md`, `whatsapp-worker.md` (supervisión), `whatsapp-outbox.md`, `whatsapp-delivery.md`, `whatsapp-operations.md`, `whatsapp-panel.md`, `recovery-validation.md`.

## Preparación y verificación

- Ejecuta los comandos desde la raíz. En Windows/PowerShell no hace falta activar el entorno: usa `.\venv\Scripts\python.exe` (abajo, `python`).
- `.env` raíz es obligatorio: `SECRET_KEY`, `DB_NAME`, `DB_USER` y `DB_PASSWORD` son necesarios incluso para comandos de administración. No hay configuración alternativa con SQLite ni ajustes específicos para pruebas.
- Para desarrollo local por HTTP hacen falta `DEBUG=True` y `ALLOWED_HOSTS` adecuados: el valor predeterminado `DEBUG=False` fuerza HTTPS y cookies seguras.
- Solo PostgreSQL. pgvector ya no es necesario (se eliminó con la migración `0013_remove_legacy_models`); las pruebas solo necesitan poder crear su base de datos.
- `.envotro` (si existe) solo aporta valores predeterminados de IA/inventario de una lista permitida (`_OTHER_ENV_KEYS` en settings): no admite todas las claves, no importa credenciales de base de datos ni del framework, y las variables del entorno/`.env` tienen prioridad.
- Instalación nueva: `python manage.py migrate`; revisar `python manage.py sync_inventory` (vista previa desde `GOOGLE_SHEET_PRODUCTS_CSV_URL`) y aplicar solo con `--apply --confirm HUELLA`; después `python manage.py runserver`.

```powershell
python -m pip install -r requirements.txt
python manage.py migrate
python manage.py runserver
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py test                                   # toda la suite
python manage.py test applications.whatsapp             # una app
python manage.py test applications.chat.test_limits     # un módulo de pruebas
python manage.py test applications.chat.tests.ChatFlowTests.test_anonymous_user_can_send_messages   # una prueba
```

- No hay linter ni build frontend: la interfaz usa plantillas Django y Tailwind vía CDN en `templates/base.html`; reutiliza la plantilla base.
- Las pruebas mockean integraciones externas con `unittest.mock.patch` y la configuración con `override_settings`. Las nuevas están separadas por tema en `test_*.py`: en `applications/whatsapp` (`test_worker`, `test_delivery`, `test_recovery`, `test_operations`, `test_migration_upgrade`) y en `applications/chat` (`test_limits`, `test_inventory_sync`, `test_backup_restore`); las antiguas siguen en el `tests.py` de cada app.
- El ensayo real de respaldo (`test_backup_restore`) exige herramientas PostgreSQL de la misma versión mayor y una plantilla sin tablas de aplicación. Usa únicamente bases `test_*` y destinos nuevos `test_rehearsal_*`; no eleves privilegios ni restaures sobre la base habitual.

## Operaciones sensibles (no ejecutar como diagnóstico)

- `sync_inventory` sin flags es vista previa sin escrituras. `--apply --confirm HUELLA` vuelve a descargar, valida el catálogo bloqueado y aplica en una transacción; un cambio de CSV/catálogo invalida la confirmación; las bajas masivas necesitan además `--allow-large-drop`. No descargues ni apliques el catálogo real por rutina: usa mocks e `inventory_status`.
- `whatsapp_worker --once` **no** es una simulación: procesa mensajes pendientes y **envía WhatsApps reales**.
- `cleanup_chat_limits` elimina registros caducados de límites de uso.
- `cleanup_whatsapp_data` elimina latidos de workers y eventos de entrega antiguos (`--dry-run` solo cuenta; no toca mensajes ni `Outbox`).
- Solo lectura: `inventory_status`, `whatsapp_healthcheck` (devuelve salida no exitosa si falta latido/progreso o hay errores), `whatsapp_operations_check`, `ai_usage` (cupo local de IA del día UTC; son reservas conservadoras, no facturación real).

## Arquitectura

Aplicaciones en `applications/`: `chat`, `whatsapp`, `users`, `home`.

- **Chat** (`applications/chat`): `views.py` gestiona pertenencia y persistencia de conversaciones web; `services.py::answer_message` delega en `answer_sheet_message` (búsqueda en el catálogo local + selección validada de la IA); `inventory.py` busca en el catálogo local; `inventory_source.py` lee y valida la hoja CSV; `inventory_sync.py` sincroniza (vista previa/aplicar); `responses.py` redacta y valida las respuestas; `providers.py` integra OpenRouter; `limits.py` aplica límites de uso y control de consumo de IA (modelos de uso en `models.py`; ver `docs/usage-limits.md`). OpenRouter y Google Sheets son las opciones predeterminadas activas.
- **Fuente única**: el catálogo sale solo de la hoja de Google (`GOOGLE_SHEET_PRODUCTS_CSV_URL`), que puede ser el Excel subido tal cual: acepta `ID producto`/`SKU`, `Producto`, `Marca`, `Tipo`, `Precio`, `Disponible`, `Precio anterior` y `URL` (ver `docs/inventory-sync.md`). El blog, DummyJSON, los combos y los embeddings se eliminaron (migración `0013`).
- **WhatsApp** (`applications/whatsapp`): reutiliza los servicios del chat vía `services.py`; `meta_api.py` habla con Meta. Arquitectura de cola: el webhook `/whatsapp/webhook/` valida el HMAC de Meta sobre el cuerpo original, deduplica y persiste antes de acusar recibo, sin descargar imágenes, llamar a IA ni enviar nada. Un worker independiente y obligatorio para responder (`whatsapp_worker`, `worker_runtime.py`, `delivery.py`) procesa mensajes y `Outbox` (`process_outbox()`); revisa las colas cada `WHATSAPP_WORKER_POLL_SECONDS` (2 s por defecto, `--poll-seconds`) y respeta debounce y límites. Recibir otro mensaje entrante ya no dispara el procesamiento. El worker no sincroniza inventario: su revisión/aplicación es una operación separada y confirmada. Supervisión en `docs/whatsapp-worker.md`.
- **Panel de WhatsApp**: el envío manual solo crea entradas `Outbox` y no llama a Meta en esa petición. «Encolado» ≠ «enviado».
- **Acceso**: `/chat/` es público; las conversaciones anónimas se limitan por IDs guardados en la sesión y las autenticadas por usuario; conserva ambas comprobaciones de pertenencia. `users.User` autentica por correo, no por nombre de usuario; el panel y la API de WhatsApp requieren superusuario u ocupación `'0'` (admin) / `'2'` (empleado): iniciar sesión no basta.

## Reglas del inventario

- Las respuestas del catálogo deben basarse en el inventario recuperado: conserva la validación de productos y cifras y el comportamiento sin coincidencias, también para reconocimiento de imágenes.
- La búsqueda es textual sobre productos locales activos, no vectorial. Los alias y la normalización de cabeceras CSV están definidos en `inventory_source.py`.
- Los productos requieren SKU y nombre; los SKU duplicados tras normalizar hacen fallar la sincronización. Los precios usan `Decimal` y los CSV inválidos se rechazan.
- La sincronización conserva IDs por SKU, desactiva ausentes sin borrar filas y bloquea bajas masivas sin `--allow-large-drop`.
