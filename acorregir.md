# A corregir

Revisión del 2026-11-17, sobre el código tras simplificar el proyecto a un bot solo de WhatsApp (`chat` es ahora `core`). Estado base: `manage.py check` limpio, sin migraciones pendientes y 233 pruebas en verde (1 omitida; es el ensayo de `pg_dump`/`pg_restore`, que necesita las herramientas de PostgreSQL). Son problemas de funcionamiento, no roturas.

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
- **Pillow** quitado de `requirements.txt` (solo servía al panel).
- **Documentación** reescrita para el bot solo de WhatsApp.

## Alto

1. **Resto del dominio anterior (válvulas y tuberías).**
   - `services.py:69`: el prompt de visión dice «inventario industrial».
   - `inventory.py:36-41` y `:64`: sinónimos `válvula`, `codo`, `acople` y `llave de paso → valvula`; el comentario de `:80` habla de catálogo industrial.
   - `services.py:131`: `modifiers` con `bronce`, `npt`, `bsp`…
   - Para un catálogo de belleza no aportan y pueden confundir. Solución: prompt de cosmética y quitar los sinónimos.

## Medios

2. **Búsqueda ineficiente** (`inventory.py`). `search_products` carga todos los productos activos en cada pregunta (unos 659) y `_score` recalcula `search_terms(query)` y tokeniza el texto de cada producto cada vez. Hay que calcular la consulta una vez y guardar los tokens por producto. No hay ranking por relevancia: ordena por nombre.
3. **Siempre se llama a la IA, aunque no haga falta.** Con un solo producto la IA solo elige qué campos mostrar y se le envía el producto entero. Opcional: resolver sin IA preguntas simples («¿precio?», «¿stock?») con los campos por defecto y ahorrar cupo y latencia.
4. **Los listados se cortan en 5 sin avisar.** `MAX_PRODUCTS_IN_CONTEXT` recorta la lista y el cliente no sabe que hay más.
5. **Mensajes no soportados sin respuesta.** El webhook descarta audio, ubicación, stickers y documentos sin contestar. Mejor responder «solo puedo leer texto e imágenes».
6. **Sin panel ni modo humano.** No hay forma de intervenir en una conversación ni de reactivar un envío fallido desde la aplicación. Es una decisión de simplificación; documentarla o reconsiderarla según el uso real.

## Menores

7. `providers.py::generate_response`: el chequeo de `OPENROUTER_API_KEY` está duplicado (también en `_openrouter`) y `return handler(prompt)` al final nunca se ejecuta (solo existe OpenRouter).
8. `download_media_data` (`meta_api.py`) solo exige que la URL del medio empiece por `https://` y le envía el token de acceso. Restringir el dominio a los de Meta lo endurecería.
9. Las tablas `UsageBucket` y `UsageLease` crecen con cada identidad: programar `cleanup_chat_limits`. Lo mismo con `cleanup_whatsapp_data`.
10. Entrega «al menos una vez»: si el worker cae entre el envío a Meta y el registro (`_finish_outbox`), el bloqueo caduca y el mensaje se reenvía. Aceptable, pero documentado solo en `docs/arquitectura.md`.
11. Los tests de `whatsapp` sobrescriben `WHATSAPP_DEFAULT_SOURCE`, un ajuste que ya no existe en `settings.py`; el `override_settings` no hace nada y se puede quitar.
12. Código duplicado en `core`: `_normalize_product_text` (`services.py`) repite a `_normalize` (`inventory.py`), y `_available_stock` y `_availability` (`inventory_source.py`) normalizan el valor de «Disponible» dos veces.
13. `services.py` importa funciones con guion bajo de `responses.py` (`_SHEET_FIELDS`, `_render_sheet_selection`, `_sheet_selection`). Se usan entre módulos: deberían ser públicas.
14. `inventory.py` tiene `sync_inventory` y `preview_inventory` que solo envuelven a `inventory_sync`, y reexporta `InventoryError` con `noqa`. Los comandos pueden importar de `inventory_sync` directamente.
15. `inventory_source.py::_download_rows` usa `urlopen` con cualquier esquema (también `file://`). La URL sale del `.env`, riesgo bajo; convendría exigir `https://`.
16. El prompt a la IA incluye la `description` completa de cada producto (`services.py`). Con descripciones largas gasta más tokens de los necesarios; basta enviar los campos que la IA puede elegir.
17. Nombres que quedaron del módulo anterior: `cleanup_chat_limits`, `CHAT_*` en `settings.py` y el objeto `conversation` (`provider` + `messages`) que recibe `answer_sheet_message`. Renombrarlos a algo como `limits`/`AI_*`/`contact` aclararía el código, pero cambia comandos y variables de entorno.

## Orden sugerido

1. Punto 1 (limpiar el dominio anterior): cambio pequeño con efecto visible para el cliente.
2. Puntos 2 a 5 según prioridad (búsqueda, IA, listados, tipos de mensaje).
3. Menores (7 a 17) cuando haya tiempo.

## Para activar lo nuevo

La migración `whatsapp/0002_seguimiento_entrega` añade los campos de seguimiento al `Outbox`; hay que aplicarla con `python manage.py migrate`. Si hay un proxy delante de Django, define `TRUSTED_PROXY_COUNT` en el `.env`. En Meta, el webhook ya recibe los `statuses` con la suscripción al campo `messages`.
