# A corregir

Revisión del 2026-11-17, sobre el código tras simplificar el proyecto a un bot solo de WhatsApp (`chat` es ahora `core`). Estado base: `manage.py check` limpio, sin migraciones pendientes y 216 pruebas en verde (1 omitida). Son problemas de funcionamiento, no roturas.

## Hecho

- **Marca y tipo del catálogo:** la hoja trae la marca real y 17 categorías en `Tipo`; `sync_inventory` carga 659 productos activos, con stock.
- **Catálogo con un solo comando:** `sync_inventory` carga la hoja (`--dry-run` solo muestra la vista previa).
- **Categorías sin tildes** (`inventory.py::category_products`): `categoría: serum` y `sérum` encuentran lo mismo.
- **Chat web y panel eliminados:** ya no existen `views.py`, `Conversation`, `Message`, el dashboard ni el modo humano. Con ello desaparecieron los puntos antiguos sobre el límite de las conversaciones anónimas, los mensajes vacíos sin límite de frecuencia y el log de errores de la vista web.
- **`hasattr(history.model, 'role')`** (`services.py`): `latest_inventory_context` solo atiende el historial de WhatsApp.
- **Pillow** quitado de `requirements.txt` (solo servía al panel).
- **Documentación** reescrita para el bot solo de WhatsApp.

## Altos

1. **Límite por IP detrás de un proxy** (`limits.py::client_address`). Ignora `X-Forwarded-For` a propósito. Detrás de Nginx o Cloudflare todos los usuarios comparten la IP del proxy y los límites de login y registro (`AUTH_LOGIN_PER_IP`, `AUTH_REGISTER_PER_IP`) afectan a todos juntos. WhatsApp no se ve afectado. Hace falta decidir cómo obtener la IP real (proxy de confianza).
2. **Estado de entrega de Meta.** El webhook ignora los `statuses` (entregado, leído, fallido). `sent=True` solo significa «aceptado por Meta»: un fallo posterior, por ejemplo por la ventana de 24 horas, nunca se registra.
3. **Orden de las partes en reintentos** (`whatsapp/services.py::process_outbox`). Elige por `created_at` y salta lo que tiene `next_attempt_at` futuro: si la parte 1 de una respuesta larga falla y se reintenta después, la parte 2 puede llegar antes. Habría que bloquear las partes siguientes del mismo `group_id`.
4. **Saludos y mensajes sin producto** (`services.py`). «Hola» o «gracias» no tienen términos de búsqueda y reciben «No encontré ese producto…». Falta una respuesta de saludo o ayuda.
5. **Resto del dominio anterior (válvulas y tuberías).**
   - `services.py:69`: el prompt de visión dice «inventario industrial».
   - `inventory.py:36-41` y `:64`: sinónimos `válvula`, `codo`, `acople` y `llave de paso → valvula`; el comentario de `:80` habla de catálogo industrial.
   - `services.py:131`: `modifiers` con `bronce`, `npt`, `bsp`…
   - Para un catálogo de belleza no aportan y pueden confundir. Solución: prompt de cosmética y quitar los sinónimos.

## Medios

6. **Búsqueda ineficiente** (`inventory.py`). `search_products` carga todos los productos activos en cada pregunta (unos 659) y `_score` recalcula `search_terms(query)` y tokeniza el texto de cada producto cada vez. Hay que calcular la consulta una vez y guardar los tokens por producto. No hay ranking por relevancia: ordena por nombre.
7. **Siempre se llama a la IA, aunque no haga falta.** Con un solo producto la IA solo elige qué campos mostrar y se le envía el producto entero. Opcional: resolver sin IA preguntas simples («¿precio?», «¿stock?») con los campos por defecto y ahorrar cupo y latencia.
8. **Los listados se cortan en 5 sin avisar.** `MAX_PRODUCTS_IN_CONTEXT` recorta la lista y el cliente no sabe que hay más.
9. **Mensajes no soportados sin respuesta.** El webhook descarta audio, ubicación, stickers y documentos sin contestar. Mejor responder «solo puedo leer texto e imágenes».
10. **Sin panel ni modo humano.** No hay forma de intervenir en una conversación ni de reactivar un envío fallido desde la aplicación. Es una decisión de simplificación; documentarla o reconsiderarla según el uso real.

## Menores

11. `providers.py::generate_response`: el chequeo de `OPENROUTER_API_KEY` está duplicado (también en `_openrouter`) y `return handler(prompt)` al final nunca se ejecuta (solo existe OpenRouter).
12. `download_media_data` (`meta_api.py`) solo exige que la URL del medio empiece por `https://` y le envía el token de acceso. Restringir el dominio a los de Meta lo endurecería.
13. Las tablas `UsageBucket` y `UsageLease` crecen con cada identidad: programar `cleanup_chat_limits`. Lo mismo con `cleanup_whatsapp_data`.
14. Entrega «al menos una vez»: si el worker cae entre el envío a Meta y el registro (`_finish_outbox`), el bloqueo caduca y el mensaje se reenvía. Aceptable, pero documentado solo en `docs/arquitectura.md`.
15. Los tests de `whatsapp` sobrescriben `WHATSAPP_DEFAULT_SOURCE`, un ajuste que ya no existe en `settings.py`; el `override_settings` no hace nada y se puede quitar.
16. Nombres que quedaron del módulo anterior: `cleanup_chat_limits`, `CHAT_*` en `settings.py` y el objeto `conversation` (`provider` + `messages`) que recibe `answer_sheet_message`. Renombrarlos a algo como `limits`/`AI_*`/`contact` aclararía el código, pero cambia comandos y variables de entorno.

## Orden sugerido

1. Punto 5 (limpiar el dominio anterior) y punto 4 (saludos): cambios pequeños con efecto visible para el cliente.
2. Puntos 1 a 3, según el despliegue real (proxy, seguimiento de entrega, orden de partes).
3. Puntos 6 a 9 según prioridad (búsqueda, IA, listados, tipos de mensaje).
4. Menores (11 a 16) cuando haya tiempo.
