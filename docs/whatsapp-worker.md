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
pasa a `failed` y se encola el aviso de error al cliente (salvo en modo humano).

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

La sincronización posterior muestra una vista previa por defecto y exige una
huella para aplicar. Actualiza por SKU/nombre, conserva IDs y desactiva ausentes. Revisa `docs/inventory-sync.md` antes de cualquier
automatización: no hay aplicación automática sin revisión en el worker. No se
creó ni ejecutó ninguna sincronización automática durante estas tareas.

## Estado y alertas

Cada proceso registra un `WorkerHeartbeat` con identificador propio:
- `heartbeat_at`: latido de un hilo separado, aunque la consulta de IA esté ocupada.
- `last_progress_at`: progreso del bucle principal entre grupos y envíos.
- `last_success_at`: última iteración completada sin errores de fase.
- `phase`, `last_error` y `stopped_at`: fase, tipo de error sin detalles privados
  y parada controlada.

Un latido reciente no oculta un bucle atascado. El panel `/whatsapp/` muestra los
estados: activo, detenido, no iniciado, sin latido, sin progreso y con errores.
La consulta está protegida por los permisos de administrador/empleado.

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

Para incluir también acumulación de colas, fallos de configuración y cupo de
IA, se añadió posteriormente `whatsapp_operations_check --warnings-as-errors`.
Puede sustituir o complementar la comprobación del supervisor al hacer la
activación final; consulta `docs/whatsapp-operations.md`.

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
2. Aplica las migraciones, incluida `whatsapp/0009_worker_heartbeat`:

   ```powershell
   .\venv\Scripts\python.exe manage.py migrate
   ```

3. Publica los estáticos si tu despliegue lo requiere y reinicia el servidor web.
4. Configura el worker continuo bajo supervisión usando una de las opciones siguientes.
5. Comprueba `whatsapp_healthcheck` y revisa el panel. Una prueba real de WhatsApp
   requiere autorización y un destinatario controlado: no se realizó aquí.

La migración crea solo la tabla de latidos; no inicia procesos ni envía mensajes.
El `.env` raíz debe estar disponible para la cuenta del servicio, con PostgreSQL,
credenciales y permisos correctos. No incluyas secretos en argumentos de tareas
o archivos unit. No se instaló ni activó ninguna tarea o servicio del sistema.

### Windows: Programador de tareas

Se incluyen wrappers:
- `deploy/windows/start-whatsapp-worker.ps1`: inicia el worker real y propaga su salida.
- `deploy/windows/check-whatsapp-worker.ps1`: comprueba salud y propaga su salida.

Configura una tarea para el primero:
1. Desencadenador **Al iniciar el sistema**.
2. Cuenta de servicio con permisos de lectura del proyecto/`.env`, acceso a
   PostgreSQL y red; ejecutar aunque no haya sesión interactiva.
3. Programa `powershell.exe`; argumentos de ejemplo, ajustando la ruta:

   ```text
   -NoProfile -NonInteractive -File "D:\Proyectos GIT\chatbot2026\deploy\windows\start-whatsapp-worker.ps1"
   ```

4. Reiniciar si falla, por ejemplo cada minuto; no iniciar una segunda instancia
   si ya está en ejecución. Desactiva el límite de duración de la tarea continua.
5. Configura captura/retención de logs en tu entorno; el Programador de tareas
   no conserva por sí solo todo el stdout/stderr del proceso.

Crea otra tarea periódica cada minuto para `check-whatsapp-worker.ps1`. Su último
resultado será no cero si el worker no está saludable. Revisa/monitoriza esos
fallos independientemente de la tarea del worker. Un bloqueo con proceso vivo
requiere intervención o una política explícita del supervisor para reiniciarlo.

### Linux: systemd

Las plantillas están en `deploy/systemd/`:
- `chatbot-whatsapp-worker.service`: arranque y reinicio ante salida con error.
- `chatbot-whatsapp-health.service` y `.timer`: comprobación independiente cada minuto.

Antes de instalarlas, cambia `User`, `WorkingDirectory` y la ruta del intérprete
por los valores de tu servidor. La cuenta debe tener acceso al `.env` y a la base
de datos. Una vez revisadas, los comandos de instalación son:

```bash
sudo cp deploy/systemd/chatbot-whatsapp-* /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now chatbot-whatsapp-worker.service
sudo systemctl enable --now chatbot-whatsapp-health.timer
```

Estos comandos **arrancan el envío real** y deben ejecutarse solo durante el
despliegue autorizado. Consulta los logs con `journalctl -u
chatbot-whatsapp-worker.service` y `journalctl -u chatbot-whatsapp-health.service`.
El worker usa SIGINT para parada controlada; si debe ser terminado a la fuerza,
los tokens y plazos existentes permiten recuperar el trabajo abandonado.

`Restart=on-failure` recupera caídas del proceso, no detecta por sí solo un proceso
vivo atascado. El temporizador detecta la falta de progreso y registra fallo;
conecta ese estado a alertas y define una política de reinicio si lo necesitas.

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
