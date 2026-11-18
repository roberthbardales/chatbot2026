# A corregir

Revisión del 2026-11-17, sobre el código tras simplificar el proyecto a un bot solo de WhatsApp (`chat` es ahora `core`). Estado base: `manage.py check` limpio, sin migraciones pendientes y 268 pruebas en verde (1 omitida; es el ensayo de `pg_dump`/`pg_restore`, que necesita las herramientas de PostgreSQL). Son problemas de funcionamiento, no roturas.

## Hecho

- **Marca y tipo del catálogo:** la hoja trae la marca real y 17 categorías en `Tipo`; `sync_inventory` carga 659 productos activos, con stock.
- **Catálogo con un solo comando:** `sync_inventory` carga la hoja (`--dry-run` solo muestra la vista previa).
- **Categorías sin tildes** (`inventory.py::category_products`): `categoría: serum` y `sérum` encuentran lo mismo.
- **Chat web eliminado:** ya no existen las vistas web, `Conversation` ni `Message`. Con ello desaparecieron los puntos antiguos sobre el límite de las conversaciones anónimas, los mensajes vacíos sin límite de frecuencia y el log de errores de la vista web.
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
- **Listados cortados en 5 sin avisar** (`services.py`): ahora se busca entre todos los productos y, si hay más de los que se muestran, el bot lo dice («Hay 64 productos en total y te muestro 5. Si me dices la marca, el tipo o el tamaño, afino la búsqueda»). Ver `docs/inventory-search.md`.
- **Panel de operadores** (`/whatsapp/`): conversaciones con estado de cada envío, salud del worker, colas, catálogo y consumo de IA, modo humano y envío manual, con actualización automática cada 5 s. Reintroduce `WhatsAppContact.mode` y `WhatsAppMessage.is_manual` (migración `0003`). Ver `docs/whatsapp-panel.md`.
- **Preguntas por marca, categoría o descripción** (`responses.py`): la IA devolvía `brand` o `category` y el servidor lo rechazaba, así que «y la marca?» respondía precio y stock. Ahora se aceptan esos nombres en inglés como equivalentes de los españoles.
- **Aviso de listados con número equivocado** (`services.py`): si la IA omitía un producto, el aviso decía «te muestro 5» aunque se veían 4. Ahora el aviso y las opciones guardadas corresponden a lo que se muestra.
- **Frases con vocabulario industrial** (`responses.py`, `services.py`): «te busco otra medida», «confírmame el modelo o la medida» y «tipo, marca o conexión» ahora hablan de producto y tamaño. Una prueba vigila que los textos del cliente no vuelvan a incluir palabras como medida, conexión, pulgada o válvula.
- **Documentación al día:** README (versión de Django, enlace roto a `contexto.md`, `createsuperuser`, `TRUSTED_PROXY_COUNT`), `usage-limits.md` (modo humano y panel) y `vps.md` (el worker es obligatorio: servicio systemd, panel y comprobación de salud). Nueva guía `docs/probar-el-bot.md`.
- **Pillow** quitado de `requirements.txt` (solo servía al panel).
- **Documentación** reescrita para el bot solo de WhatsApp.

## Medios

1. **Siempre se llama a la IA, aunque no haga falta.** Con un solo producto la IA solo elige qué campos mostrar y se le envía el producto entero. Opcional: resolver sin IA preguntas simples («¿precio?», «¿stock?») con los campos por defecto y ahorrar cupo y latencia.
2. **Mensajes no soportados sin respuesta.** El webhook descarta audio, ubicación, stickers y documentos sin contestar. Mejor responder «solo puedo leer texto e imágenes».

## Menores

3. `providers.py::generate_response`: el chequeo de `OPENROUTER_API_KEY` está duplicado (también en `_openrouter`) y `return handler(prompt)` al final nunca se ejecuta (solo existe OpenRouter).
4. `download_media_data` (`meta_api.py`) solo exige que la URL del medio empiece por `https://` y le envía el token de acceso. Restringir el dominio a los de Meta lo endurecería.
5. Las tablas `UsageBucket` y `UsageLease` crecen con cada identidad: programar `cleanup_chat_limits`. Lo mismo con `cleanup_whatsapp_data`.
6. Entrega «al menos una vez»: si el worker cae entre el envío a Meta y el registro (`_finish_outbox`), el bloqueo caduca y el mensaje se reenvía. Aceptable, pero documentado solo en `docs/arquitectura.md`.
7. Los tests de `whatsapp` sobrescriben `WHATSAPP_DEFAULT_SOURCE`, un ajuste que ya no existe en `settings.py`; el `override_settings` no hace nada y se puede quitar.
8. Código duplicado en `inventory_source.py`: `_available_stock` y `_availability` normalizan el valor de «Disponible» dos veces.
9. `services.py` importa funciones con guion bajo de `responses.py` (`_SHEET_FIELDS`, `_render_sheet_selection`, `_sheet_selection`). Se usan entre módulos: deberían ser públicas.
10. `inventory_source.py::_download_rows` usa `urlopen` con cualquier esquema (también `file://`). La URL sale del `.env`, riesgo bajo; convendría exigir `https://`.
11. El prompt a la IA incluye la `description` completa de cada producto (`services.py`). Con descripciones largas gasta más tokens de los necesarios; basta enviar los campos que la IA puede elegir.
12. Nombres que quedaron del módulo anterior: `cleanup_chat_limits`, `CHAT_*` en `settings.py` y el objeto `conversation` (`provider` + `messages`) que recibe `answer_sheet_message`. Renombrarlos a algo como `limits`/`AI_*`/`contact` aclararía el código, pero cambia comandos y variables de entorno.

## Orden sugerido

1. Puntos 1 y 2 según prioridad (IA innecesaria, tipos de mensaje).
2. Menores (3 a 12) cuando haya tiempo.

## Para activar lo nuevo

Las migraciones `whatsapp/0002_seguimiento_entrega` (seguimiento de entrega en `Outbox`) y `whatsapp/0003_modo_humano_y_mensajes_manuales` (modo humano del panel) hay que aplicarlas con `python manage.py migrate`. Si hay un proxy delante de Django, define `TRUSTED_PROXY_COUNT` en el `.env`. En Meta, el webhook ya recibe los `statuses` con la suscripción al campo `messages`.
