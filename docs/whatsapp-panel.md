# Panel de WhatsApp

Revisión: 2026-11-17. Panel web para que una persona vea las conversaciones, tome el control de un contacto y compruebe el estado del sistema. Está en `/whatsapp/` y en el enlace **Panel WhatsApp** del menú.

## Acceso

Solo entran el superusuario y las cuentas con ocupación Administrador (`'0'`) o Empleado (`'2'`). Iniciar sesión no basta: un usuario normal recibe 403 y un visitante sin sesión es enviado al login. Las acciones (cambiar de modo y enviar) exigen POST con token CSRF; solo el webhook está exento de CSRF. Las respuestas del panel llevan `never_cache`.

## Qué muestra

**Estado del sistema** (arriba, se actualiza solo):

| Tarjeta | Contenido |
| --- | --- |
| Worker | Estado (`Activo`, `No iniciado`, `Detenido`, `Sin latido`, `Sin progreso`, `Con errores`), último latido y último error. Es el mismo diagnóstico que `whatsapp_healthcheck`. |
| Colas | Mensajes por responder (pendientes y en proceso), envíos por enviar, envíos fallidos y mensajes fallidos. |
| Catálogo | Productos activos y totales, vigencia (`al día`, `desactualizado`, `sin sincronizar`) y fecha de la última sincronización (`inventory_status()`). |
| IA hoy | Consultas y tokens reservados del día frente a los límites (`AI_DAILY_REQUEST_LIMIT`, `AI_DAILY_TOKEN_LIMIT`). Son reservas locales, no facturación. |
| Atención humana | Cuántos contactos están en modo humano. |

**Conversaciones** (columna izquierda): contactos ordenados por último mensaje, con búsqueda por nombre o teléfono, paginación de 50 en 50, una etiqueta «Humano» y un contador de mensajes esperando respuesta.

**Conversación** (columna derecha): hasta los últimos 200 mensajes, con quién escribió cada uno (cliente, Bot u Operador) y su estado:

| Estado | Significado |
| --- | --- |
| en cola | Guardado en `Outbox`; el worker aún no lo envía. |
| reintentando | Meta falló de forma temporal; se reintenta con espera creciente. |
| enviando (n/m partes) | Respuesta larga: algunas partes ya fueron aceptadas. |
| enviado | Meta aceptó el mensaje. |
| entregado / leído | Meta lo notificó por el webhook (`statuses`). Para varias partes se muestra la menos avanzada. |
| falló: motivo | El envío o la entrega fallaron; se muestra el error de Meta. |

Los mensajes del cliente muestran «esperando respuesta» mientras están pendientes o en proceso y «no se pudo procesar» si fallaron. Las imágenes se indican como «[imagen]»: el panel no las descarga.

## Modo humano y envío manual

- **Atender yo** pasa el contacto a modo humano: el bot deja de responder y de consumir cupo de IA; los mensajes del cliente solo se guardan. **Devolver al bot** lo revierte.
- Cambiar a modo humano **no cancela** lo que ya estaba en la cola de salida: se avisa en el panel («Hay N envío(s) del bot ya en cola; se enviarán igual»).
- Si el cambio ocurre mientras el bot procesaba un mensaje (descargando la imagen o esperando a la IA), su respuesta se descarta y tampoco se envía el aviso de error.
- **Enviar** crea un mensaje del operador y lo encola en `Outbox`: la petición **no llama a Meta**. El worker lo envía. «En cola» no es «enviado». Admite hasta 4000 caracteres; los textos largos se parten en partes ordenadas.
- Meta solo permite texto libre dentro de las 24 horas posteriores al último mensaje del cliente. Fuera de esa ventana el envío falla y el panel muestra el motivo.
- Un mensaje manual interrumpe el contexto automático (el cliente tendrá que volver a elegir producto).

## Actualización automática

La página consulta el servidor cada 5 segundos (estado, lista de contactos y conversación abierta) mientras la pestaña está visible. No se pierde lo que escribes en la caja de envío ni la posición del scroll si estás leyendo mensajes anteriores. Si el servidor no responde se muestra un aviso y se reintenta.

## API

Todas las rutas cuelgan de `/whatsapp/` y exigen el mismo permiso.

| Ruta | Método | Función |
| --- | --- | --- |
| `api/status` | GET | Estado del sistema |
| `api/conversations?q=&offset=` | GET | Lista de contactos |
| `api/messages/<id>` | GET | Mensajes del contacto y estado de cada envío |
| `api/mode/<id>` | POST | `{"mode": "ai" \| "human"}` |
| `api/messages/<id>/send` | POST | `{"content": "…"}` |

## Implementación

- `views.py`: vistas y permiso (`manager_required`); `panel.py`: datos en JSON; `services.create_manual_message`.
- `templates/whatsapp/dashboard.html`: página con Tailwind por CDN y JavaScript propio dentro de la plantilla (sin compilación ni archivos estáticos). El contenido se inserta con `textContent`, nunca como HTML.
- Migración `whatsapp/0003_modo_humano_y_mensajes_manuales`: añade `WhatsAppContact.mode` y `WhatsAppMessage.is_manual`.
- Pruebas: `tests/test_panel.py` (permisos, datos y acciones) y `tests/test_human_mode.py` (el bot calla en modo humano).
