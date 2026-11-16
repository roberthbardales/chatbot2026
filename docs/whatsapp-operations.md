# Operación, alertas y recuperación manual


El panel `/whatsapp/` incorpora un resumen operativo para administradores,
empleados y superusuarios. Los endpoints conservan esos permisos y no almacenan
respuestas privadas en caché. No se llama a Meta ni a OpenRouter al consultar
el resumen o reactivar un envío.

## Resumen y alertas

Se muestran:
- Entradas pendientes, procesándose y elegibles ahora.
- Partes pendientes de salida, elegibles y con fallo local o de entrega.
- Edad del elemento elegible más antiguo en cada cola.
- Procesados/fallidos y aceptaciones de las últimas 24 horas.
- Media de recepción a procesamiento y de encolado a aceptación, en segundos.
- Llamadas y tokens reservados de IA del día UTC, límites y llamadas activas.
- Últimas diez reactivaciones, con identificador del operador, fecha y motivo.
- Conteos activos del inventario, última sincronización aplicada, resultado del
  último intento y avisos de catálogo vacío, sin historial o desactualizado.

Los tiempos incluyen debounce, espera de cola, reintentos y límites; **no son
duración pura de la IA ni latencia de entrega al destinatario**. Cuando no hay
muestras se informa `null`/«sin muestras», no cero inventado. El consumo de IA
sigue siendo reservado, no facturación real de OpenRouter.

Las alertas identifican:
- Worker ausente, detenido, sin progreso o con errores.
- Falta de token, identificador de teléfono o secreto del webhook.
- Fallos locales conocidos clasificados como configuración/permisos.
- Acumulación por número o antigüedad de elementos elegibles.
- Fallos de procesamiento, locales de envío y asíncronos de entrega.
- Cercanía o agotamiento del cupo local de IA.

No se intenta verificar un token contra Meta: una credencial incorrecta pero
presente se detecta cuando exista un error del proveedor registrado. Corregir
credenciales no demuestra por sí solo conectividad ni reactiva mensajes.

Configuración nueva del `.env` raíz:

| Variable | Predeterminado | Uso |
| --- | --- | --- |
| `WHATSAPP_QUEUE_ALERT_SECONDS` | 120 | Edad de cola elegible que genera aviso |
| `WHATSAPP_QUEUE_ALERT_COUNT` | 100 | Cantidad elegible que genera aviso |
| `WHATSAPP_MANUAL_RETRY_COOLDOWN_SECONDS` | 60 | Pausa mínima entre reactivaciones de una misma parte |

Los pendientes con reintento futuro no se cuentan como acumulación elegible.
Las partes bloqueadas por predecesoras tampoco se presentan como listas para
enviar. Las cifras de fallos locales/de entrega representan fallos actuales
guardados; no desaparecen por el mero paso del tiempo. El resumen se consulta
aproximadamente cada 30 segundos en una pestaña visible; la actualización manual
también lo recarga.

## Recuperación manual segura

En **Revisar fallos y reintentos auditados** aparecen páginas de 50 salidas
fallidas. Cada una permite ver la conversación, el contenido de referencia y
el error. Solo los fallos locales definitivos sin aceptación remota conocida
pueden reactivarse.

Flujo:
1. Revisar y corregir la causa; por ejemplo, credenciales vencidas.
2. Inspeccionar la conversación y la primera parte local fallida.
3. Pulsar **Reactivar grupo desde esta parte**.
4. Escribir un motivo de 5 a 500 caracteres y confirmar el riesgo de duplicados.
5. El servidor bloquea el grupo, revalida su estado y registra la auditoría.
6. Se reactivan las partes locales fallidas y dependientes sin aceptación conocida.
   Sus intentos del ciclo vuelven a cero; sus errores e intentos anteriores quedan
   conservados en la auditoría. Las partes aceptadas no se modifican.

**La petición no envía mensajes:** los deja pendientes para el worker. La
confirmación es obligatoria porque un timeout de red anterior puede ocultar
una aceptación de Meta que el servidor no llegó a conocer. No existe garantía
de entrega exactamente una vez.

Se rechaza la reactivación si:
- Ya hay aceptación, ID remoto o evidencia de envío, entrega o lectura.
- El objetivo no es un fallo definitivo o ya fue reactivado por otro operador.
- Hay un bloqueo vigente en el grupo.
- Existen partes anteriores aún no aceptadas: se debe empezar por la primera.
- Hay un fallo asíncrono de Meta o un estado remoto inconsistente que exige revisión.
- No transcurrió el plazo mínimo desde la reactivación anterior.

Los fallos asíncronos de un envío aceptado no se reenvían con esta función.
Los fallos de procesamiento entrante se muestran como métrica/alerta, pero no se
reprocesan automáticamente por esta acción. El operador puede atender el caso
manualmente después de revisar la conversación.

## Auditoría

`OutboxRetryAudit` conserva operador, identificador de operador, fecha, grupo,
motivo y una instantánea de IDs, partes, intentos y errores previos. La escritura
de auditoría y la reactivación son una sola transacción; si falla, no se reactivan
partes. Dos operadores simultáneos no pueden reactivar el mismo fallo dos veces.

La auditoría permanece si se borra el contacto/salida o la cuenta del operador;
los vínculos pasan a `null` y se conserva el identificador y la instantánea.
Define retención y permisos acordes con tu política de privacidad: los motivos
y errores pueden contener información sensible. No se añadieron endpoints para
editar/borrar auditorías ni se realizó limpieza de datos durante esta tarea.

## Monitorización externa, sin enviar mensajes

```powershell
.\venv\Scripts\python.exe manage.py whatsapp_operations_check
```

El comando es de solo lectura. Devuelve JSON sin motivos ni identidades de
auditoría, registra códigos de alerta en logs y termina con error si hay alertas
críticas. Para que también los avisos produzcan código no cero:

```powershell
.\venv\Scripts\python.exe manage.py whatsapp_operations_check --warnings-as-errors
```

Puede programarse en el supervisor externo, sustituyendo o complementando el
`whatsapp_healthcheck` más limitado a liveness/progreso. Los avisos se registran
en cada comprobación; el monitor debe deduplicarlos si se configura correo u
otras notificaciones. No se configuró un servicio externo de notificaciones,
un correo ni una tarea del sistema.

## API

- `GET /whatsapp/api/operations`: resumen, métricas, alertas y últimas auditorías.
- `GET /whatsapp/api/outbox/failures?offset=...`: fallos y elegibilidad de reactivación.
- `POST /whatsapp/api/outbox/<id>/retry`: JSON con `reason` y `confirm_risk: true`,
  protegido por autenticación, rol y CSRF. Devuelve `queued`, `audit_id` y cantidad
  de partes reactivadas. Los conflictos devuelven 409 sin cambios.

## Activación pendiente

Se añadió `whatsapp/0011_operations_retry_audit`. Por indicación del usuario,
las migraciones en la base habitual y la activación/supervisión del worker
siguen pendientes para el final. La implementación se verifica con proveedores
simulados y base de datos de pruebas; no se enviaron WhatsApps ni se arrancó
el worker real.

## Verificación

La suite completa pasó 266 pruebas, incluidas 23 nuevas de operación y
recuperación. Se comprobaron permisos, CSRF, auditoría transaccional,
reactivaciones concurrentes, métricas, alertas y protección de envíos con
aceptación conocida. Django, migraciones, sintaxis de JavaScript y
`git diff --check` finalizaron sin problemas. La apariencia de esta ampliación
del panel no se verificó en un navegador.

## Mantenimiento de datos y borrado de conversaciones

- `cleanup_whatsapp_data` elimina latidos de workers sin actividad desde hace más
  de `--heartbeat-days` (7 por defecto, conservando siempre el más reciente) y
  eventos de entrega recibidos hace más de `--event-days` (90). No toca mensajes,
  `Outbox` ni auditorías. Con `--dry-run` solo informa los conteos.
- `DELETE /whatsapp/api/conversations/<id>` exige el cuerpo `{"confirm": true}`,
  responde 409 si el contacto tiene mensajes pendientes/en proceso o envíos sin
  resolver, y registra quién lo borró (teléfono enmascarado). El borrado es
  irreversible y elimina también sus mensajes y envíos.
- Un contacto con `is_active=False` queda silenciado igual que en modo humano.
- Los textos entrantes que superan `WHATSAPP_MAX_MESSAGE_LENGTH` se truncan y
  se registra una advertencia. De varias imágenes en un mismo grupo solo se analiza
  la última.
