# A corregir

Revisión del 2026-11-17, sobre el código tras simplificar el proyecto a un bot solo de WhatsApp (`chat` es ahora `core`). Estado base: `manage.py check` limpio, sin migraciones pendientes y 234 pruebas en verde (1 omitida; es el ensayo de `pg_dump`/`pg_restore`, que necesita las herramientas de PostgreSQL). Son problemas de funcionamiento, no roturas.

## Hecho

- **Marca y tipo del catálogo:** la hoja trae la marca real y 17 categorías en `Tipo`; `sync_inventory` carga 659 productos activos, con stock.
- **Catálogo con un solo comando:** `sync_inventory` carga la hoja (`--dry-run` solo muestra la vista previa).
- **Categorías sin tildes** (`inventory.py::category_products`): `categoría: serum` y `sérum` encuentran lo mismo.
- **Chat web y panel eliminados:** ya no existen `views.py`, `Conversation`, `Message`, el dashboard ni el modo humano. Con ello desaparecieron los puntos antiguos sobre el límite de las conversaciones anónimas, los mensajes vacíos sin límite de frecuencia y el log de errores de la vista web.
- **`hasattr(history.model, 'role')`** (`services.py`): `latest_inventory_context` solo atiende el historial de WhatsApp.
- **«hay 1 unidades»** (`responses.py`): con stock 1 y unidad «unidades» ahora dice «hay 1 unidad» (prueba `test_single_unit_is_singular`).
- **IP detrás de un proxy** (`limits.py::client_address`): nuevo ajuste `TRUSTED_PROXY_COUNT` (0 por defecto). Con proxies propios delante de Django se toma la entrada correcta de `X-Forwarded-For`, contando desde la derecha; sin él se sigue usando solo `REMOTE_ADDR`. Ver `docs/usage-limits.md`.
- **Estados de entrega de Meta** (`whatsapp/views.py`, `services.py`): el webhook ya procesa `statuses`. `Outbox` guarda `meta_message_id`, `delivery_status` (sent, delivered, read, failed), `delivery_updated_at` y `delivery_error`. Solo avanzan; un `failed` no pisa un mensaje leído.
- **Orden de las partes** (`process_outbox`): una parte espera a que la anterior del grupo sea aceptada; si una falla definitivamente, las siguientes se descartan en vez de llegar incompletas.
- **Saludos** (`services.py::_smalltalk_reply`): «hola», «gracias» y «chao» reciben una respuesta fija, sin buscar ni llamar a la IA, y conservan el contexto.
- **Dominio industrial** (`inventory.py`, `services.py`): se quitaron los sinónimos de válvulas y tuberías, `llave de paso`, el prompt de «inventario industrial» y los modificadores `bronce`/`npt`/`bsp`. También la lógica de pulgadas, fracciones y milímetros (se conservan `ml`, `g`, `oz`…). El prompt de imágenes ahora habla de cosmética y un tamaño suelto («la de 50 ml») afina las opciones.
- **Sinónimos de cosmética** (`_SEARCH_SYNONYMS`): `bloqueador`/`sunscreen` → `protector`, `cleanser` → `limpiador`, `cream` → `crema`, `labial` → `labios`, `ampollas` → `ampolla`, etc. Salieron del catálogo real; «bloqueador solar» pasó de 0 a decenas de resultados.
- **Búsqueda más rápida** (`inventory.py`): la consulta se procesa una vez y los términos de cada producto se guardan en memoria, renovados por `content_hash` tras `sync_inventory`. Con el catálogo real, unos 15 ms por búsqueda. (No hay ranking por relevancia: se conserva el orden por nombre.)
- **Código duplicado y envoltorios:** `services.py` usa `normalize_text` de `inventory.py`; `inventory.py` ya no tiene `preview_inventory`/`sync_inventory` y el comando importa de `inventory_sync`.
- **Pillow** quitado de `requirements.txt` (solo servía al panel).
- **Documentación** reescrita para el bot solo de WhatsApp.

## Medios

1. **Siempre se llama a la IA, aunque no haga falta.** Con un solo producto la IA solo elige qué campos mostrar y se le envía el producto entero. Opcional: resolver sin IA preguntas simples («¿precio?», «¿stock?») con los campos por defecto y ahorrar cupo y latencia.
2. **Los listados se cortan en 5 sin avisar.** `MAX_PRODUCTS_IN_CONTEXT` recorta la lista y el cliente no sabe que hay más.
3. **Mensajes no soportados sin respuesta.** El webhook descarta audio, ubicación, stickers y documentos sin contestar. Mejor responder «solo puedo leer texto e imágenes».
4. **Sin panel ni modo humano.** No hay forma de intervenir en una conversación ni de reactivar un envío fallido desde la aplicación. Es una decisión de simplificación; documentarla o reconsiderarla según el uso real.

## Menores

5. `providers.py::generate_response`: el chequeo de `OPENROUTER_API_KEY` está duplicado (también en `_openrouter`) y `return handler(prompt)` al final nunca se ejecuta (solo existe OpenRouter).
6. `download_media_data` (`meta_api.py`) solo exige que la URL del medio empiece por `https://` y le envía el token de acceso. Restringir el dominio a los de Meta lo endurecería.
7. Las tablas `UsageBucket` y `UsageLease` crecen con cada identidad: programar `cleanup_chat_limits`. Lo mismo con `cleanup_whatsapp_data`.
8. Entrega «al menos una vez»: si el worker cae entre el envío a Meta y el registro (`_finish_outbox`), el bloqueo caduca y el mensaje se reenvía. Aceptable, pero documentado solo en `docs/arquitectura.md`.
9. Los tests de `whatsapp` sobrescriben `WHATSAPP_DEFAULT_SOURCE`, un ajuste que ya no existe en `settings.py`; el `override_settings` no hace nada y se puede quitar.
10. Código duplicado en `inventory_source.py`: `_available_stock` y `_availability` normalizan el valor de «Disponible» dos veces.
11. `services.py` importa funciones con guion bajo de `responses.py` (`_SHEET_FIELDS`, `_render_sheet_selection`, `_sheet_selection`). Se usan entre módulos: deberían ser públicas.
12. `inventory_source.py::_download_rows` usa `urlopen` con cualquier esquema (también `file://`). La URL sale del `.env`, riesgo bajo; convendría exigir `https://`.
13. El prompt a la IA incluye la `description` completa de cada producto (`services.py`). Con descripciones largas gasta más tokens de los necesarios; basta enviar los campos que la IA puede elegir.
14. Nombres que quedaron del módulo anterior: `cleanup_chat_limits`, `CHAT_*` en `settings.py` y el objeto `conversation` (`provider` + `messages`) que recibe `answer_sheet_message`. Renombrarlos a algo como `limits`/`AI_*`/`contact` aclararía el código, pero cambia comandos y variables de entorno.

## Orden sugerido

1. Puntos 1 a 3 según prioridad (IA innecesaria, listados cortados, tipos de mensaje).
2. Punto 4 es una decisión de diseño por revisar.
3. Menores (5 a 14) cuando haya tiempo.

## Para activar lo nuevo

La migración `whatsapp/0002_seguimiento_entrega` añade los campos de seguimiento al `Outbox`; hay que aplicarla con `python manage.py migrate`. Si hay un proxy delante de Django, define `TRUSTED_PROXY_COUNT` en el `.env`. En Meta, el webhook ya recibe los `statuses` con la suscripción al campo `messages`.
