# WhatsApp en segundo plano

## Arquitectura

El webhook `/whatsapp/webhook/` valida el HMAC del cuerpo original, guarda los
mensajes en PostgreSQL y confirma la recepción. No descarga imágenes, consulta
OpenRouter ni envía la cola. Si la persistencia falla, devuelve error para no
confirmar un mensaje perdido. Los duplicados de Meta se deduplican por
`message_id`; no se necesita una cola externa adicional.

Un proceso independiente `whatsapp_worker` consulta la cola, respeta debounce,
límites, tokens y reintentos, genera respuestas y envía `Outbox`. Sin este proceso,
los mensajes entrantes y las respuestas manuales permanecen pendientes. Recibir
otra notificación de Meta ya no dispara el procesamiento.

Entre un contacto y el siguiente, el worker ejecuta `process_outbox`, de modo que
las respuestas ya generadas no esperan a que termine toda la tanda entrante. Un
fallo del envío se registra en `last_error` pero no detiene el procesamiento de
entrantes. Si un mensaje abandonado agota `WHATSAPP_PROCESSING_MAX_ATTEMPTS`,
pasa a `failed` y se encola el aviso de error al cliente.

```powershell
.\venv\Scripts\python.exe manage.py whatsapp_worker
```

**Este comando procesa y envía WhatsApps reales.** `--once` también, aunque solo
hace una iteración. No son comandos de diagnóstico ni simulación. No se arrancó
el worker real durante esta implementación.

## Inventario separado

El worker ya no llama a `sync_inventory`. `INVENTORY_SYNC_MINUTES` dejó de
controlar su ejecución. La sincronización se hace explícitamente con el comando
existente `sync_inventory`, manualmente o con una tarea separada si se desea.

La sincronización carga el catálogo directamente (`--dry-run` para solo ver los cambios). Actualiza por SKU/nombre, conserva IDs y desactiva ausentes. Revisa `docs/inventory-sync.md` antes de cualquier
automatización: no hay aplicación automática sin revisión en el worker. No se
creó ni ejecutó ninguna sincronización automática durante estas tareas.

## Estado y alertas

Cada proceso registra un `WorkerHeartbeat` con identificador propio:
- `heartbeat_at`: latido de un hilo separado, aunque la consulta de IA esté ocupada.
- `last_progress_at`: progreso del bucle principal entre grupos y envíos.
- `last_success_at`: última iteración completada sin errores de fase.
- `phase`, `last_error` y `stopped_at`: fase, tipo de error sin detalles privados
  y parada controlada.

Un latido reciente no oculta un bucle atascado. `worker_health()` distingue los
estados: activo, detenido, no iniciado, sin latido, sin progreso y con errores;
se consulta con `whatsapp_healthcheck` y se muestra en el panel de `/whatsapp/`.

Comprobación de solo lectura:

```powershell
.\venv\Scripts\python.exe manage.py whatsapp_healthcheck
```

Devuelve JSON y código 0 si existe al menos un worker saludable. Si no, escribe
un error en logs y termina con código no cero. También falla si no se puede
consultar PostgreSQL. No procesa colas ni llama a proveedores.

El worker no puede avisar por sí mismo después de morir: ejecuta esta comprobación
desde un supervisor independiente, por ejemplo cada minuto. Las plantillas generan
errores en el journal o en el resultado de la tarea. Para avisos por correo u otro
canal, conecta esos fallos a tu plataforma de monitorización; no se implementó
un servicio externo de notificaciones.

## Configuración

| Variable | Predeterminado | Función |
| --- | --- | --- |
| `WHATSAPP_WORKER_POLL_SECONDS` | 2 | Espera entre iteraciones; también `--poll-seconds` |
| `WHATSAPP_WORKER_HEARTBEAT_SECONDS` | 10 | Intervalo del latido |
| `WHATSAPP_WORKER_HEALTH_SECONDS` | 45 | Antigüedad máxima del latido; al menos tres intervalos |
| `WHATSAPP_WORKER_STALL_SECONDS` | 300 | Antigüedad máxima del progreso; al menos tres timeouts de IA más 60 s |
| `WHATSAPP_WORKER_MAX_CONTACTS` | 20 | Contactos máximos de entrada por iteración |

La fase de salida sigue intentando como máximo 20 envíos por iteración. Los
errores de entrada no impiden intentar las salidas ya existentes. Los errores de
fase se registran; una iteración buena posterior limpia el estado de error.
`--once` devuelve código no cero si alguna fase falló. El límite de contactos
evita que una entrada grande monopolice indefinidamente las salidas; la espera
real depende de la carga y los proveedores, no es una garantía de dos segundos.

## Despliegue seguro

1. Detén los procesos anteriores para no mezclar versiones de webhook y worker.
2. Aplica las migraciones:

   ```powershell
   .\venv\Scripts\python.exe manage.py migrate
   ```

3. Publica los estáticos si tu despliegue lo requiere y reinicia el servidor web.
4. Configura el worker continuo bajo supervisión usando una de las opciones siguientes.
5. Comprueba `whatsapp_healthcheck`. Una prueba real de WhatsApp
   requiere autorización y un destinatario controlado: no se realizó aquí.

El `.env` raíz debe estar disponible para la cuenta del servicio, con PostgreSQL,
credenciales y permisos correctos. No incluyas secretos en argumentos de tareas
o archivos unit. No se instaló ni activó ninguna tarea o servicio del sistema.

### Supervisión

El repositorio ya no incluye plantillas de servicio. Usa el supervisor de tu sistema (systemd en Linux, el
Programador de tareas en Windows, o el que prefieras) con estas pautas:

- Ejecuta `python manage.py whatsapp_worker` de forma continua, con la cuenta que pueda leer el `.env` y acceder a PostgreSQL.
- Reinicia el proceso si falla (`Restart=on-failure` en systemd) y no arranques una segunda instancia.
- Usa SIGINT para detenerlo de forma controlada; si se termina a la fuerza, los tokens y plazos existentes
  permiten recuperar el trabajo abandonado.
- Ejecuta `python manage.py whatsapp_healthcheck` cada minuto: su código de salida no es cero si el worker no está
  saludable. Vigila esos fallos aparte del servicio del worker.
- Un proceso vivo pero atascado no lo reinicia nadie por sí solo: define una política de reinicio si lo necesitas.

Iniciar el worker **arranca el envío real**: hazlo solo durante un despliegue autorizado.

### Agente de IA

Con `AI_AGENT_ENABLED=True` el worker responde con el agente conversacional y usa el motor de plantillas como respaldo (ver [agente.md](agente.md)). Cada mensaje usa de 2 a 6 llamadas a la IA y tarda más (el debounce de 6 s más las llamadas), así que revisa el cupo diario (`AI_DAILY_*`). Cada respuesta deja una línea en el log sin contenido (`Agent answered: calls=… tools=… retried=… seconds=…`) y cada rechazo del validador otra (`Agent answer rejected: reason=…`). Si el agente falla de forma persistente, apágalo con `AI_AGENT_ENABLED=False` y reinicia el worker.

## Límites

- No hay garantía de entrega exactamente una vez ante una respuesta perdida de Meta.
- Un worker activo no garantiza credenciales válidas, catálogo actualizado o entrega
  al destinatario: revisa también los errores de mensajes y las partes de salida.
- Las métricas de latido no reemplazan la monitorización de PostgreSQL o del servidor.
- Los registros históricos de latidos se conservan para diagnóstico; en despliegues
  con muchos reinicios, define una política de retención de los registros detenidos.

## Verificación de esta implementación

La suite completa pasó 220 pruebas, incluidas 22 nuevas del webhook, worker y
salud. `manage.py check`, `makemigrations --check --dry-run` y `git diff --check`
terminaron sin problemas. Se validó la sintaxis del JavaScript y de los wrappers
PowerShell sin ejecutarlos. No se inició el worker real ni se instalaron servicios,
tareas o temporizadores; tampoco se sincronizó inventario ni se enviaron WhatsApps.
