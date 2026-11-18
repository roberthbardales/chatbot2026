# Arquitectura del sistema

Revisión del código: 2026-11-17. Describe lo que hace el código actual, no certifica el estado de producción. Para instalación y comandos consulta [README.md](../README.md) y [CLAUDE.md](../CLAUDE.md); para flujos de negocio, [comofunciona.md](../comofunciona.md).

## 1. Visión general

Bot de atención al cliente por **WhatsApp** (Meta Cloud API) para un catálogo de productos. Responde consultas de precio, stock y características. Es asíncrono: colas en PostgreSQL y un worker independiente. Un panel de operadores (`/whatsapp/`) permite ver conversaciones, atender un contacto a mano y vigilar el sistema. No hay chat web.

Principio rector: **la IA nunca inventa datos comerciales**. Los productos se buscan textualmente en un catálogo local; la IA solo elige *qué* SKU y *qué* campos mostrar, y el servidor redacta la respuesta con los valores del inventario.

```text
        Google Sheets (CSV) ── sync_inventory (manual) ──► Product (PostgreSQL)
                                                                  ▲
WhatsApp ─► webhook ─► cola ─► worker whatsapp_worker ─► answer_sheet_message ─► OpenRouter
   ▲          (HMAC)             │                                  (selección JSON validada / visión)
   └── Meta API ◄── Outbox ◄─────┘
```

## 2. Stack y estructura

- Django 5.2, Python 3.12, **solo PostgreSQL** (sin SQLite ni pgvector). Sin build de frontend.
- Configuración con `django-environ` desde `.env` raíz (`SECRET_KEY`, `DB_*` obligatorias). `.envotro` solo aporta valores predeterminados de una lista permitida.
- Zona horaria `America/Lima`, idioma `es-pe`. Con `DEBUG=False` se fuerza HTTPS, HSTS y cookies seguras.

| Ruta | Responsabilidad |
| --- | --- |
| `chatbot2026/` | settings, urls raíz, wsgi/asgi |
| `applications/home` | página de inicio |
| `applications/users` | usuario por correo, roles, login/registro con límites |
| `applications/core` | motor sin vistas: catálogo, IA, respuestas, límites, sincronización |
| `applications/whatsapp` | webhook, cola entrante, outbox, worker |
| `deploy/` | servicios systemd, tareas Windows, respaldo previo al despliegue |
| `docs/` | documentación operativa por tema |

## 3. Aplicaciones

### 3.1 `users`

- `User` (`AbstractBaseUser`): se autentica por **correo**. Ocupación: `'0'` Administrador, `'1'` Usuario, `'2'` Empleado, `'3'` Otro.
- `mixins.py`: `AdministradorPermisoMixin` etc.; el superusuario siempre pasa.
- Login y registro aplican límites por correo e IP (`AUTH_*`) reutilizando `core/limits.enforce_rates`.

### 3.2 `core`

| Módulo | Función |
| --- | --- |
| `services.py` | `answer_sheet_message`: orquesta SKU explícito, elección de candidatos previos, categoría, búsqueda, imagen y selección por IA. `latest_inventory_context` lee el contexto del historial de un contacto. |
| `inventory.py` | Búsqueda textual sobre `Product` activos: normalización, sinónimos español/inglés, unidades de contenido neto (ml, g…), coincidencia por inclusión de todos los términos y caché en memoria de los términos de cada producto. |
| `inventory_source.py` | Descarga y valida el CSV (alias de cabeceras, `Decimal`, límites de tamaño/filas). |
| `inventory_sync.py` | Vista previa y aplicación con bloqueo y auditoría (`InventorySyncRun`). |
| `responses.py` | `InventoryAnswer` (texto + `inventory_context`), validación estricta del JSON de la IA y redacción con valores del inventario. |
| `providers.py` | Cliente OpenRouter (`urllib`), siempre dentro de `ai_admission`. |
| `limits.py` | Frecuencia, concurrencia y cupo diario de IA, todo en PostgreSQL; `client_address` para login/registro. |

Modelos: `Product`, `UsageBucket`, `UsageLease`, `AIUsageDay`, `InventorySyncLock`, `InventorySyncRun`. Comandos: `sync_inventory`, `cleanup_chat_limits`.

### 3.3 `whatsapp`

| Módulo | Función |
| --- | --- |
| `views.py` | Webhook (GET verificación, POST firmado) y vistas del panel (`manager_required`). |
| `panel.py` | Datos del panel en JSON: estado del sistema, contactos, mensajes y estado de entrega. |
| `services.py` | `enqueue_message`, `process_pending_messages`, `enqueue_outbox`, `process_outbox`, recuperación de mensajes abandonados. |
| `meta_api.py` | `send_text_message` y `download_media_data` contra Graph API; clasifica errores reintentables. |
| `worker_runtime.py`, `health.py` | Latido en hilo aparte y diagnóstico (`running`, `stalled`, `unresponsive`, `degraded`, `stopped`, `not_started`). |

Modelos: `WhatsAppContact` (con `mode`), `WhatsAppMessage` (con `is_manual`), `Outbox`, `WorkerHeartbeat`. Comandos: `whatsapp_worker`, `whatsapp_healthcheck`, `cleanup_whatsapp_data`.

## 4. Flujos principales

### 4.1 Consulta de catálogo

`answer_sheet_message(conversation, text, image_data_url=None)` (`conversation` es un objeto con `provider` y `messages`, el historial del contacto):

1. **Contexto previo**: lee el `inventory_context` del último mensaje del asistente (`candidates` o `selected_sku`) para resolver respuestas como «la 2», «sí, esa» o «y la de bronce».
2. **Imagen**: si la hay, pide a la IA 3–8 palabras clave (o `desconocido`) y las usa como texto de búsqueda. Nunca se persiste la imagen.
3. **Resolución del producto** (en orden): SKU explícito → elección entre candidatos → producto seleccionado antes → categoría → búsqueda textual.
4. **Sin coincidencias** → mensaje fijo `No encontré ese producto…`. **Varias** → lista numerada de opciones y se guardan los SKU como `candidates`.
5. **Un producto o categoría** → prompt con los productos como *datos*; la IA debe devolver exactamente `{"productos":[{"sku":…,"campos":[…]}]}`. `_sheet_selection` rechaza cualquier clave, SKU o campo no permitido; si falla, se usa `precio` + `stock` por defecto.
6. **Redacción**: `_render_sheet_selection` escribe la frase con los valores de la base de datos (nunca de la IA).

### 4.2 WhatsApp entrante

```text
Meta ─► POST /whatsapp/webhook/ ─► valida HMAC-SHA256 sobre el cuerpo original
                                 ─► filtra phone_number_id propio
                                 ─► text/image → enqueue_message (dedup por message_id, status=pending)
                                 ─► 200 (sin IA, sin descargar imagen, sin enviar)
```

El worker (`whatsapp_worker`) ejecuta en bucle cada `WHATSAPP_WORKER_POLL_SECONDS`:

1. `process_pending_messages`
   - Recupera mensajes `processing` abandonados (timeout 600 s; tras 3 intentos → `failed` y se encola un aviso de error al cliente).
   - Toma pendientes con *debounce* (`MESSAGE_DEBOUNCE_SECONDS`) agrupados **por contacto** con `select_for_update(skip_locked)` y un `processing_token`.
   - Aplica `WHATSAPP_RATE_PER_CONTACT` (aplaza con `retry_after_at` si se excede).
   - Descarga la imagen, llama a `answer_sheet_message` y cierra con `_finish_processing`, que revalida propiedad del token bajo bloqueo antes de crear el mensaje del asistente y el `Outbox`.
2. `process_outbox`: envía hasta 20 partes por llamada. Se ejecuta también **entre un contacto y el siguiente** de la fase entrante, para que las respuestas ya generadas no esperen a toda la tanda; un fallo del outbox no detiene a los entrantes.

### 4.3 Outbox y entrega

- Los textos > 4096 caracteres se parten en «Parte i/n» con grupo (`group_id`, `part_number`); las partes de un grupo se envían en orden: cada una espera a la anterior y, si una falla definitivamente, las siguientes se descartan (ver [whatsapp-outbox.md](whatsapp-outbox.md)).
- Cada envío cuenta el intento al reclamar (un bloqueo caducado no permite intentos ilimitados). Reintento con backoff exponencial (base 30 s, tope 3600 s, 5 intentos); errores no reintentables fallan de inmediato.
- «Encolado» ≠ «enviado»: `sent` indica que Meta aceptó el mensaje y se guarda su `meta_message_id`. Los *statuses* del webhook (`delivered`, `read`, `failed`) actualizan `Outbox.delivery_status` y `delivery_error`; solo avanzan, nunca retroceden.

### 4.4 Sincronización de inventario

```text
Google Sheet (CSV) ─► inventory_source (validar) ─► InventoryPlan
sync_inventory --dry-run     → vista previa, sin escrituras
sync_inventory [--allow-large-drop]
```

- Aplicar vuelve a descargar y validar el CSV y calcula el plan sobre el catálogo bloqueado.
- Todo ocurre en una transacción con `InventorySyncLock`. Conserva IDs por SKU, **desactiva** ausentes (no borra) y bloquea bajas mayores a `INVENTORY_MAX_DEACTIVATE_PERCENT` sin `--allow-large-drop`.
- El estado (`current`/`stale`/`unknown`, frente a `INVENTORY_STALE_HOURS`) sale al final de `sync_inventory`. El worker **no** sincroniza.

## 5. Límites y control de consumo

Todo en PostgreSQL con bloqueos de fila, compartido entre procesos web y worker (sin Redis):

| Mecanismo | Modelo | Efecto |
| --- | --- | --- |
| `enforce_rates` | `UsageBucket` | Ventana fija por contacto de WhatsApp y por correo/IP en login y registro (IPv6 por /64). |
| `principal_slot` | `UsageLease` | Una consulta simultánea por contacto. |
| `ai_admission` | `UsageLease`, `AIUsageDay` | Concurrencia global de IA, tope de tamaño de prompt, cupo diario de peticiones y tokens **reservados** (conservador, no es facturación) y alerta por umbral. |

Las identidades se guardan como HMAC salado (`identity()`), no en claro. `X-Forwarded-For` se ignora salvo que definas `TRUSTED_PROXY_COUNT` (proxies propios delante de Django). Detalle en [usage-limits.md](usage-limits.md).

## 6. Seguridad

- **Webhook**: verificación de token en GET; HMAC-SHA256 con `compare_digest` en POST; descarte de `phone_number_id` ajeno; el trabajo pesado nunca ocurre en la petición. Es la única vista exenta de CSRF.
- **IA**: los datos del inventario se pasan como datos, no instrucciones; la salida se valida contra esquema cerrado y los valores salen siempre de la BD.
- **Privacidad**: teléfonos enmascarados en logs (`***1234`); imágenes de clientes solo en memoria; secretos únicamente por entorno.
- **Producción**: HTTPS forzado, HSTS, cookies seguras, `X_FRAME_OPTIONS=DENY`.

## 7. Operación y despliegue

- **Procesos**: servidor web (WSGI, para el webhook) + `whatsapp_worker` obligatorio para responder. Plantillas en `deploy/systemd/` (servicio del worker y temporizador de salud) y `deploy/windows/` (tareas programadas).
- **Salud**: el latido se actualiza en un hilo propio; el progreso, en el bucle principal, de modo que un bucle colgado se distingue de un proceso muerto. `whatsapp_healthcheck` falla si no hay latido/progreso o hay errores. El panel muestra el mismo diagnóstico.
- **Comandos de solo lectura**: `whatsapp_healthcheck`, `sync_inventory --dry-run`.
- **Comandos sensibles**: `whatsapp_worker --once` (envía mensajes reales), `sync_inventory`, `cleanup_chat_limits` (borra registros caducados), `cleanup_whatsapp_data` (borra latidos antiguos).
- **Respaldo**: `deploy/predeploy_backup.py` y ensayo en `core/tests/test_backup_restore`; ver [recovery-validation.md](recovery-validation.md).

## 8. Pruebas

`python manage.py test` ejecuta toda la suite con PostgreSQL. Las integraciones externas se simulan con `unittest.mock.patch`. Módulos por tema en `applications/<app>/tests/`: `core` (`test_limits`, `test_inventory_sync`, `test_backup_restore`, `test_answers`, `test_search`…) y `whatsapp` (`test_worker`, `test_recovery`, `test_housekeeping`, `test_processing`, `test_outbox`, `test_panel`, `test_human_mode`).

## 9. Decisiones y límites conocidos

- **Búsqueda textual, no vectorial**: predecible y verificable; no entiende sinónimos fuera de la lista de `inventory.py`.
- **Cola en PostgreSQL**: sin broker adicional; el rendimiento depende del sondeo (2 s) y de `WHATSAPP_WORKER_MAX_CONTACTS`.
- **Un solo proveedor de IA** (OpenRouter).
- **Sincronización manual**: no hay sincronización automática; el catálogo puede quedar desactualizado (`stale`).
- **Modo humano**: un operador puede silenciar al bot por contacto y escribir desde el panel; no se pueden reactivar envíos fallidos desde la aplicación.
- **Entrega al menos una vez**: si Meta acepta el envío y la base de datos falla antes de registrarlo, puede haber un duplicado.
- Los límites de IA son reservas locales, no consumo real facturado.
- **Orden de envío**: se garantiza dentro de un grupo (partes de una misma respuesta); entre grupos distintos, un envío en reintento no bloquea otros mensajes del mismo contacto (ver [whatsapp-outbox.md](whatsapp-outbox.md)).
- **Mantenimiento**: `cleanup_whatsapp_data` purga latidos antiguos.

## 10. Documentos relacionados

[inventory-sync.md](inventory-sync.md) · [inventory-search.md](inventory-search.md) · [usage-limits.md](usage-limits.md) · [whatsapp-worker.md](whatsapp-worker.md) · [whatsapp-outbox.md](whatsapp-outbox.md) · [whatsapp-panel.md](whatsapp-panel.md) · [recovery-validation.md](recovery-validation.md)
