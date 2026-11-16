# Seguimiento de entrega de WhatsApp


## Estados y partes

Cada parte de una respuesta guarda el identificador `messages[0].id` devuelto por
Meta en `Outbox.meta_message_id`. La respuesta de envío se valida: una lista sin
un identificador válido no se considera una respuesta normal de aceptación.

Se mantienen separados:

| Estado | Evidencia |
| --- | --- |
| Pendiente/procesando | Estado de la cola local |
| Aceptado por Meta | Respuesta HTTP de envío válida; `sent=True` conserva este significado |
| Enviado | Evento firmado `sent` |
| Entregado | Evento firmado `delivered` |
| Leído | Evento firmado `read` |
| Fallo de entrega | Evento firmado `failed`, distinto del fallo local al intentar enviar |

Se guardan fechas separadas: `provider_sent_at`, `delivered_at`, `read_at` y
`delivery_failed_at`, además de `delivery_error`. `sent_at` sigue siendo la fecha
local de aceptación, no se reutiliza como fecha de entrega.

El panel muestra estado y fechas por parte y contadores por respuesta. Una parte
leída cuenta como entregada/enviada en el resumen, pero no se inventan fechas
para eventos intermedios que Meta no haya enviado. Los contadores de aceptación
se conservan aunque posteriormente se notifique un fallo de entrega.

## Recepción y validación

Los eventos se reciben en el mismo webhook, dentro de `value.statuses`. Se valida
el HMAC sobre el cuerpo original antes de procesarlos. Para estados se exige el
`metadata.phone_number_id` de la configuración actual. El identificador remoto
debe corresponder a un `Outbox` y el destinatario al contacto asociado; no se
actualiza un registro solo porque coincida el teléfono.

Se ignoran estados, identificadores o timestamps inválidos. Los estados se
guardan en `DeliveryEvent`, con una huella única por mensaje, destinatario,
cuenta, estado y fecha del proveedor. Los duplicados son idempotentes. No se
almacena el cuerpo completo del webhook en este registro.

La escritura del evento y la actualización asociada son transaccionales y se
serializan por salida. Un fallo de persistencia devuelve HTTP 500 para que no se
acuse como guardado un evento perdido. El webhook no descarga archivos, consulta
IA, arranca el worker ni envía mensajes.

## Eventos atrasados, simultáneos y anticipados

- `read` no retrocede a `delivered` o `sent` por recibirlos después.
- Una entrega/lectura confirmada prevalece sobre un fallo contradictorio; el
  evento de fallo se conserva para diagnóstico.
- `sent` o la aceptación no borran un fallo de entrega conocido.
- Las fechas de éxito conservan el primer timestamp recibido para ese estado;
  los fallos conservan su fecha/detalle más recientes.
- Los eventos sin salida asociada aún se conservan. Al guardar el ID de Meta
  se intenta aplicarlos, y el worker hace reconciliación acotada al procesar la
  cola para cubrir la carrera entre el callback y la confirmación de envío.
- Los eventos desconocidos o de otro destinatario no monopolizan la reconciliación.

## Reintentos y compatibilidad

Un fallo asíncrono **no reactiva automáticamente un envío ya aceptado**. Se
muestra al operador para revisión; reenviar sin distinguir la causa puede
duplicar mensajes o incumplir restricciones del destinatario. El orden entre
partes sigue basándose en la aceptación de las anteriores, no en esperar su
lectura. No se pueden retirar partes que Meta ya aceptó.

Las salidas antiguas sin identificador remoto permanecen como aceptadas si ese
era su estado local, sin atribuirles entrega o lectura. No se intentan recuperar
IDs por teléfono ni se reenvían para conseguirlos. Los errores de intentos viejos
siguen sin poder sobrescribir un propietario nuevo de la salida.

La API del historial conserva sus campos y añade:
- En `sending`: `sent_parts`, `delivered_parts` y `read_parts`; `failed_parts`
  incluye tanto fallos locales como el estado actual de fallo asíncrono.
- En `delivery_parts`: ID de Meta, estado confirmado y fechas de cada evento.

Los callbacks dependen de la suscripción y configuración de Meta. Si no llega
confirmación, el panel mantiene el estado que conoce: no interpreta la ausencia
de lectura como un fallo ni garantiza que todos los clientes notifiquen lectura.

## Migración y activación pendiente

Se añadió `whatsapp/0010_delivery_tracking`. No se aplicó a la base habitual ni
se realizaron envíos reales durante esta tarea. La migración añade campos y la
tabla de eventos; no inicia procesos ni reenvía mensajes.

**Por indicación del usuario, la activación del worker y la supervisión se dejan
para el final.** Al hacer ese despliegue, aplicar las migraciones pendientes y
seguir `docs/whatsapp-worker.md`. Mientras esté apagado, no se generan envíos
nuevos, aunque el webhook puede registrar estados de identificadores conocidos.

Los eventos no asociados se conservan para reconciliación y diagnóstico; define
una política de retención adecuada a tu operación y privacidad. Al borrar una
salida se eliminan sus eventos asociados; los no asociados necesitan una política
propia. No se creó una tarea de limpieza de eventos ni se ejecutó ningún borrado.

## Verificación

La suite completa pasó 243 pruebas, incluidas 23 nuevas de seguimiento. Cubren
callbacks simultáneos con conexiones independientes, duplicados, desorden,
errores de persistencia, validación de cuenta/destinatario y reconciliación.
Las comprobaciones de Django y migraciones, la sintaxis de JavaScript y
`git diff --check` finalizaron sin errores. No se arrancó el worker ni se
enviaron mensajes reales.
