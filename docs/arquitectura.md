# Arquitectura del sistema

Revisión del código: 2026-11-15. Describe lo que hace el código actual, no certifica el estado de producción. Para instalación y comandos consulta [README.md](../README.md) y [CLAUDE.md](../CLAUDE.md); para flujos de negocio, [arquitectura.md](../arquitectura.md) (raíz).

## 1. Visión general

Chatbot de atención al cliente para un catálogo de productos. Responde consultas de precio, stock y características por dos canales:

- **Chat web** (`/chat/`), público, síncrono.
- **WhatsApp** (Meta Cloud API), asíncrono mediante colas en PostgreSQL y un worker independiente.

Principio rector: **la IA nunca inventa datos comerciales**. Los productos se buscan textualmente en un catálogo local; la IA solo elige *qué* SKU y *qué* campos mostrar, y el servidor redacta la respuesta con los valores del inventario.

```text
                    ┌────────────────────────── Google Sheets (CSV) ──────────────────────────┐
                    │                                                                          │
                    ▼  sync_inventory (manual)                                     │
Navegador ──► /chat/send/ ──┐                                                                  │
                            ├─► answer_sheet_message ──► Product (PostgreSQL, búsqueda textual)│
WhatsApp ─► webhook ─► cola ┘            │                                                     │
   ▲          (HMAC)    │                ▼                                                     │
   │                    ▼          OpenRouter (selección JSON validada / visión)               │
   │             worker whatsapp_worker                                                        │
   └── Meta API ◄── Outbox ◄───────────┘                                                       │
```

## 2. Stack y estructura

- Django 5.2, Python 3.12, **solo PostgreSQL** (sin SQLite ni pgvector). Plantillas Django + Tailwind por CDN; sin build de frontend.
- Configuración con `django-environ` desde `.env` raíz (`SECRET_KEY`, `DB_*` obligatorias). `.envotro` solo aporta valores predeterminados de una lista permitida.
- Zona horaria `America/Lima`, idioma `es-pe`. Con `DEBUG=False` se fuerza HTTPS, HSTS y cookies seguras.

| Ruta | Responsabilidad |
| --- | --- |
| `chatbot2026/` | settings, urls raíz, wsgi/asgi |
| `applications/home` | página de inicio |
| `applications/users` | usuario por correo, roles, login/registro con límites |
| `applications/chat` | conversaciones web, catálogo, IA, límites, sincronización |
| `applications/whatsapp` | webhook, cola entrante, outbox, entrega, panel, worker |
| `deploy/` | servicios systemd, tareas Windows, respaldo previo al despliegue |
| `docs/` | documentación operativa por tema |

## 3. Aplicaciones

### 3.1 `users`

- `User` (`AbstractBaseUser`): se autentica por **correo**. Ocupación: `'0'` Administrador, `'1'` Usuario, `'2'` Empleado, `'3'` Otro.
- `mixins.py`: `AdministradorPermisoMixin` etc.; el superusuario siempre pasa.
- Login y registro aplican límites por correo e IP (`AUTH_*`) reutilizando `chat/limits.enforce_rates`.

### 3.2 `chat`

| Módulo | Función |
| --- | --- |
| `views.py` | `ChatView` y `SendMessageView`. Pertenencia: anónimos por IDs en sesión (máx. 20), autenticados por `user`. |
| `services.py` | `answer_message` → `answer_sheet_message`: orquesta SKU explícito, elección de candidatos previos, categoría, búsqueda, imagen y selección por IA. |
| `inventory.py` | Búsqueda textual sobre `Product` activos: normalización, sinónimos, dimensiones (fracciones, mm, pulgadas), puntaje por inclusión de términos. |
| `inventory_source.py` | Descarga y valida el CSV (alias de cabeceras, `Decimal`, límites de tamaño/filas). |
| `inventory_sync.py` | Vista previa y aplicación con bloqueo y auditoría (`InventorySyncRun`). |
| `responses.py` | `InventoryAnswer` (texto + `inventory_context`), validación estricta del JSON de la IA y redacción con valores del inventario. |
| `providers.py` | Cliente OpenRouter (`urllib`), siempre dentro de `ai_admission`. |
| `limits.py` | Frecuencia, concurrencia y cupo diario de IA, todo en PostgreSQL. |

Modelos: `Conversation` (UUID, `user` nulo si es anónima), `Message` (con `inventory_context` JSON), `Product`, `UsageBucket`, `UsageLease`, `AIUsageDay`, `InventorySyncLock`, `InventorySyncRun`.

### 3.3 `whatsapp`

| Módulo | Función |
| --- | --- |
| `views.py` | Webhook (GET verificación, POST firmado) y API JSON del panel. |
| `services.py` | `enqueue_message`, `process_pending_messages`, `enqueue_outbox`, `process_outbox`, recuperación de mensajes abandonados. |
| `meta_api.py` | `send_text_message` y `download_media_data` contra Graph API; clasifica errores reintentables. |
| `delivery.py` | Registro y aplicación de estados `sent/delivered/read/failed` de Meta. |
| `worker_runtime.py`, `health.py` | Latido en hilo aparte y diagnóstico (`running`, `stalled`, `unresponsive`, `degraded`, `stopped`, `not_started`). |
| `operations.py` | Reintento manual auditado de fallos y resumen operativo. |

Modelos: `WhatsAppContact` (modo `ai`/`human`), `WhatsAppMessage`, `Outbox`, `DeliveryEvent`, `OutboxRetryAudit`, `WorkerHeartbeat`.

## 4. Flujos principales

### 4.1 Consulta de catálogo (compartido por web y WhatsApp)

`answer_sheet_message(conversation, text, image_data_url=None)`:

1. **Contexto previo**: lee el `inventory_context` del último mensaje del asistente (`candidates` o `selected_sku`) para resolver respuestas como «la 2», «sí, esa» o «y la de bronce».
2. **Imagen**: si la hay, pide a la IA 3–8 palabras clave (o `desconocido`) y las usa como texto de búsqueda. Nunca se persiste la imagen.
3. **Resolución del producto** (en orden): SKU explícito → elección entre candidatos → producto seleccionado antes → categoría → búsqueda textual.
4. **Sin coincidencias** → mensaje fijo `No encontré ese producto…`. **Varias** → lista numerada de opciones y se guardan los SKU como `candidates`.
5. **Un producto o categoría** → prompt con los productos como *datos*; la IA debe devolver exactamente `{"productos":[{"sku":…,"campos":[…]}]}`. `_sheet_selection` rechaza cualquier clave, SKU o campo no permitido; si falla, se usa `precio` + `stock` por defecto.
6. **Redacción**: `_render_sheet_selection` escribe la frase con los valores de la base de datos (nunca de la IA).

### 4.2 Chat web

`POST /chat/send/` → valida longitud → `admit_web_request` (límites por sesión/IP/usuario) → `principal_slot` (1 consulta simultánea por principal) → `answer_message` → guarda `Conversation` y los dos `Message` en una transacción **después** de llamar a la IA (la llamada de red no mantiene bloqueos de BD). Un `LimitExceeded` devuelve 429 con `Retry-After`.

### 4.3 WhatsApp entrante

```text
Meta ─► POST /whatsapp/webhook/ ─► valida HMAC-SHA256 sobre el cuerpo original
                                 ─► filtra phone_number_id propio
                                 ─► statuses → DeliveryEvent
                                 ─► text/image → enqueue_message (dedup por message_id, status=pending)
                                 ─► 200 (sin IA, sin descargar imagen, sin enviar)
```

El worker (`whatsapp_worker`) ejecuta en bucle cada `WHATSAPP_WORKER_POLL_SECONDS`:

1. `process_pending_messages`
   - Recupera mensajes `processing` abandonados (timeout 600 s; tras 3 intentos → `failed` y se encola un aviso de error al cliente, salvo en modo humano).
   - Toma pendientes con *debounce* (`MESSAGE_DEBOUNCE_SECONDS`) agrupados **por contacto** con `select_for_update(skip_locked)` y un `processing_token`.
   - Si el contacto está en modo `human` o `is_active=False`, cierra sin responder. Aplica `WHATSAPP_RATE_PER_CONTACT` (aplaza con `retry_after_at` si se excede).
   - Descarga la imagen, llama a `answer_sheet_message` y cierra con `_finish_processing`, que revalida propiedad del token y el modo humano bajo bloqueo antes de crear el mensaje del asistente y el `Outbox`.
2. `process_outbox`: reconcilia eventos de entrega y envía hasta 20 partes por llamada. Se ejecuta también **entre un contacto y el siguiente** de la fase entrante, para que las respuestas ya generadas no esperen a toda la tanda; un fallo del outbox no detiene a los entrantes.

### 4.4 Outbox y entrega

- Los textos > 4096 caracteres se parten en «Parte i/n» con grupo (`group_id`, `part_number`); una parte no se envía hasta que las anteriores se aceptan; si una falla de forma definitiva, las siguientes se marcan fallidas.
- Cada envío cuenta el intento al reclamar (un bloqueo caducado no permite intentos ilimitados). Reintento con backoff exponencial (base 30 s, tope 3600 s, 5 intentos); errores no reintentables fallan de inmediato.
- «Encolado» ≠ «enviado» ≠ «entregado»: `sent` indica que Meta aceptó el mensaje; `delivery_status` (`accepted/sent/delivered/read/failed`) lo actualizan los *statuses* firmados. Los eventos que llegan antes de persistir el envío se concilian después.
- El panel puede reactivar una parte fallida (`retry_failed_group`) solo si Meta nunca la aceptó, con motivo obligatorio, confirmación de riesgo, enfriamiento y registro en `OutboxRetryAudit`.

### 4.5 Sincronización de inventario

```text
Google Sheet (CSV) ─► inventory_source (validar) ─► InventoryPlan
sync_inventory --dry-run     → vista previa, sin escrituras
sync_inventory [--allow-large-drop]
```

- Aplicar vuelve a descargar y validar el CSV y calcula el plan sobre el catálogo bloqueado.
- Todo ocurre en una transacción con `InventorySyncLock`. Conserva IDs por SKU, **desactiva** ausentes (no borra) y bloquea bajas mayores a `INVENTORY_MAX_DEACTIVATE_PERCENT` sin `--allow-large-drop`.
- El estado (`current`/`stale`/`unknown`, frente a `INVENTORY_STALE_HOURS`) sale en el panel de WhatsApp y al final de `sync_inventory`. El worker **no** sincroniza.

## 5. Límites y control de consumo

Todo en PostgreSQL con bloqueos de fila, compartido entre procesos web y worker (sin Redis):

| Mecanismo | Modelo | Efecto |
| --- | --- | --- |
| `enforce_rates` | `UsageBucket` | Ventana fija por sesión, usuario, IP (IPv6 por /64), contacto y autenticación. |
| `principal_slot` | `UsageLease` | Una consulta simultánea por principal. |
| `ai_admission` | `UsageLease`, `AIUsageDay` | Concurrencia global de IA, tope de tamaño de prompt, cupo diario de peticiones y tokens **reservados** (conservador, no es facturación) y alerta por umbral. |

Las identidades se guardan como HMAC salado (`identity()`), no en claro. `X-Forwarded-For` se ignora deliberadamente. Detalle en [usage-limits.md](usage-limits.md).

## 6. Seguridad

- **Webhook**: verificación de token en GET; HMAC-SHA256 con `compare_digest` en POST; descarte de `phone_number_id` ajeno; el trabajo pesado nunca ocurre en la petición.
- **Panel/API de WhatsApp**: `login_required` + superusuario u ocupación `'0'`/`'2'`; `never_cache`; CSRF activo (solo el webhook está exento).
- **Chat**: comprobación de pertenencia por sesión o usuario; 404 uniforme ante IDs ajenos.
- **IA**: los datos del inventario se pasan como datos, no instrucciones; la salida se valida contra esquema cerrado y los valores salen siempre de la BD.
- **Privacidad**: teléfonos enmascarados en logs (`***1234`); imágenes de clientes solo en memoria; secretos únicamente por entorno.
- **Producción**: HTTPS forzado, HSTS, cookies seguras, `X_FRAME_OPTIONS=DENY`.

## 7. Operación y despliegue

- **Procesos**: servidor web (WSGI) + `whatsapp_worker` obligatorio para responder. Plantillas en `deploy/systemd/` (servicio del worker y temporizador de salud) y `deploy/windows/` (tareas programadas).
- **Salud**: el latido se actualiza en un hilo propio; el progreso, en el bucle principal, de modo que un bucle colgado se distingue de un proceso muerto. `whatsapp_healthcheck` falla si no hay latido/progreso o hay errores.
- **Comandos de solo lectura**: `whatsapp_healthcheck`, `whatsapp_operations_check`, `ai_usage`.
- **Comandos sensibles**: `whatsapp_worker --once` (envía mensajes reales), `sync_inventory`, `cleanup_chat_limits` (borra registros caducados).
- **Respaldo**: `deploy/predeploy_backup.py` y ensayo en `test_backup_restore`; ver [recovery-validation.md](recovery-validation.md).

## 8. Pruebas

`python manage.py test` ejecuta toda la suite con PostgreSQL. Las integraciones externas se simulan con `unittest.mock.patch`. Módulos por tema: `chat/test_limits`, `test_inventory_sync`, `test_backup_restore`; `whatsapp/test_worker`, `test_delivery`, `test_recovery`, `test_operations`, `test_migration_upgrade`; además los `tests.py` históricos de cada app.

## 9. Decisiones y límites conocidos

- **Búsqueda textual, no vectorial**: predecible y verificable; no entiende sinónimos fuera de la lista de `inventory.py`.
- **Cola en PostgreSQL**: sin broker adicional; el rendimiento depende del sondeo (2 s) y de `WHATSAPP_WORKER_MAX_CONTACTS`.
- **Un solo proveedor de IA** (OpenRouter); el esquema `PROVIDER_CHOICES` permite añadir otros.
- **Sincronización manual y confirmada**: no hay sincronización automática; el catálogo puede quedar desactualizado (`stale`).
- **Reintento en dos etapas** (automático y manual) evita duplicados pero exige revisión humana cuando Meta ya aceptó parte del grupo.
- Los límites de IA son reservas locales, no consumo real facturado.
- **Orden de envío**: solo se garantiza dentro de un grupo; un envío en reintento no bloquea otros mensajes del mismo contacto (ver [whatsapp-outbox.md](whatsapp-outbox.md)).
- **Mantenimiento**: `cleanup_whatsapp_data` purga latidos y eventos de entrega antiguos; el borrado de conversaciones exige confirmación y se rechaza con trabajo pendiente.

## 10. Documentos relacionados

[inventory-sync.md](inventory-sync.md) · [inventory-search.md](inventory-search.md) · [usage-limits.md](usage-limits.md) · [whatsapp-worker.md](whatsapp-worker.md) · [whatsapp-outbox.md](whatsapp-outbox.md) · [whatsapp-delivery.md](whatsapp-delivery.md) · [whatsapp-operations.md](whatsapp-operations.md) · [whatsapp-panel.md](whatsapp-panel.md) · [recovery-validation.md](recovery-validation.md)
