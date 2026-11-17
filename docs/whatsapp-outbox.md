# Reintentos de la cola de WhatsApp

Antes de desplegar, detén los procesos web/worker anteriores, ejecuta
`.\venv\Scripts\python.exe manage.py migrate` y reinicia los servicios.

## Configuración del `.env` raíz

| Variable | Predeterminado | Uso |
| --- | --- | --- |
| `WHATSAPP_OUTBOX_MAX_ATTEMPTS` | `5` | Máximo de intentos, incluido el primero |
| `WHATSAPP_OUTBOX_RETRY_BASE_SECONDS` | `30` | Espera inicial entre intentos |
| `WHATSAPP_OUTBOX_RETRY_MAX_SECONDS` | `3600` | Límite de la espera progresiva |
| `WHATSAPP_OUTBOX_LOCK_SECONDS` | `600` | Plazo para recuperar un bloqueo abandonado |

Con los valores predeterminados, los errores temporales o desconocidos esperan
30, 60, 120 y 240 segundos; el quinto fallo es definitivo. Los intervalos son
mínimos: hace falta que `process_outbox()` vuelva a ejecutarse para reintentar.
El worker independiente debe ejecutarlo; el webhook ya no procesa las colas.

## Estados y errores

- `sent=True`: Meta aceptó el envío; no confirma entrega ni lectura.
- `failed_at` informado: fuera de los reintentos automáticos.
- `next_attempt_at` futuro: esperando el próximo intento.
- `locked_at` y `lock_token`: un intento posee el envío actualmente.
- `last_error`: categoría, HTTP, código de Meta y detalle del último fallo.
  Se limpia si el envío termina correctamente.

La clasificación utiliza códigos de Meta, no el texto del error ni HTTP 400
por sí solo. Los fallos conocidos de parámetros, destinatario o configuración
se apartan sin insistir. Los desconocidos se reintentan con límite. Los fallos
de credenciales requieren intervención; corregir las credenciales no reactiva
automáticamente los registros ya marcados como fallidos.

Los registros antiguos enviados no se tocan. Los pendientes que ya alcanzaron
el máximo se apartan en la siguiente ejecución cuando no tengan un bloqueo
vigente. Los bloqueos antiguos vencidos pueden recuperarse. Los intentos se
cuentan al reclamar el envío, de modo que una interrupción también consume un
intento y no permite recuperaciones ilimitadas.

El token evita que un intento antiguo cambie el estado de un propietario nuevo.
No garantiza entrega exactamente una vez: si Meta aceptó un mensaje y se perdió
la respuesta, un reintento puede duplicarlo.

## Mensajes de más de 4096 caracteres

Los mensajes del bot se dividen en partes numeradas, incluyendo
el encabezado en el límite de 4096 caracteres. Se conservan los separadores y
el texto completo en el mensaje original. Cada parte tiene su propio `Outbox`,
con `group_id` compartido y `part_number` consecutivo.

Las partes se envían en orden de creación y son independientes: un fallo temporal
de una parte se reintenta más tarde y las demás siguen, por lo que pueden llegar
desordenadas. No se repiten las partes ya aceptadas. Cada ejecución procesa como máximo 20
intentos; las partes restantes necesitan otra ejecución.

**Límite conocido:** el orden solo se garantiza dentro de un grupo. Si una
respuesta está en espera de reintento y se encola otra para el mismo contacto
(por ejemplo, la respuesta a un mensaje posterior del cliente), la segunda puede salir antes.
Es una decisión deliberada: priorizar que la cola nunca quede atascada detrás de
un envío que falla.

Las entradas antiguas largas pendientes se dividen al reclamarlas, respetando
los bloqueos vigentes y sus intentos previos. Las enviadas no se vuelven a enviar
y las fallidas definitivas no se reactivan. La llamada directa a Meta rechaza
textos demasiado largos en lugar de truncarlos silenciosamente.

La aceptación no implica entrega: `sent=True` solo indica que Meta aceptó el mensaje.
El estado de cada parte está en la tabla `Outbox` (`sent`, `failed_at`, `attempts`, `last_error`).

Referencia de códigos:
https://developers.facebook.com/docs/whatsapp/cloud-api/support/error-codes/
