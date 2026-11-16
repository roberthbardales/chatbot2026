# Validación operativa y recuperación


## Estado del trabajo

Se ampliaron las pruebas, no el despliegue real. **La activación del worker y las
migraciones de la base habitual continúan pendientes para el final**, por
indicación del usuario. No se instaló un servicio, se recibió tráfico real de
Meta ni se hicieron envíos reales para comprobar estos escenarios.

No se añadió una migración de funcionalidad en esta etapa. Las pruebas de
migración sí revierten/reaplican el esquema de su base aislada, nunca el habitual.

## Workers y fallos simulados

`applications/whatsapp/test_recovery.py` usa PostgreSQL y conexiones independientes,
con proveedores simulados. Comprueba:

- Dos procesadores sobre el mismo grupo pendiente: una respuesta y una salida.
- Un mensaje nuevo mientras el contacto está ocupado: se difiere, no se pierde.
- Dos procesadores de salida: cada parte independiente se envía una vez en el
  caso normal con confirmación guardada.
- Una parte en vuelo: otro procesador no adelanta las siguientes.
- Caída después de reclamar entrada: recuperación al vencer el intento.
- Interrupción dentro de la transacción de respuesta: rollback sin asistente o
  cola parciales; recuperación posterior con una sola respuesta.
- Reinicio después de confirmar la respuesta: no se vuelve a generar.
- Webhooks duplicados simultáneos: una persistencia, sin procesamiento síncrono.

Se inyecta una excepción especial en puntos controlados, no se mata un worker
real del sistema. Los plazos vencidos se simulan sobre filas de pruebas, sin
esperar diez minutos ni modificar relojes del servidor. Otras pruebas ya cubren
tokens antiguos, límites, eventos de entrega simultáneos y fuera de orden,
reactivaciones concurrentes y sincronizaciones concurrentes del catálogo.

### Límite demostrado: aceptación remota sin confirmación local

Una prueba simula que Meta acepta un mensaje y el proceso cae antes de guardar
su ID. El bloqueo impide repetirlo inmediatamente; después de vencer, un
reintento puede generar una segunda aceptación remota. La prueba registra dos
aceptaciones simuladas de forma explícita.

**No hay garantía de entrega exactamente una vez.** Un token protege la propiedad
local, no cancela una operación ya aceptada por Meta. El seguimiento de eventos
ayuda cuando el ID es conocido, pero no reconstruye mágicamente una respuesta
de envío que nunca se guardó. Esta etapa documenta y comprueba ese riesgo; no
lo presenta como eliminado.

## Migraciones con datos antiguos

`applications/whatsapp/test_migration_upgrade.py` crea en la base de pruebas un
esquema anterior a estas mejoras: `chat/0009` y `whatsapp/0003`. Inserta catálogo,
combos, mensajes en procesamiento y salidas pendientes/aceptadas; luego aplica
todas las migraciones actuales.

Verifica precios exactos, IDs conservados, grupos distintos para salidas antiguas,
contextos vacíos compatibles y recuperación de mensajes abandonados. Las salidas
ya aceptadas siguen aceptadas, sin inventar IDs remotos, entrega o lectura.
El esquema actual se repone incluso si la prueba falla, antes del vaciado de la
base de pruebas que hace Django.

## Ensayo real de pg_dump y pg_restore, solo aislado

`applications/chat/test_backup_restore.py` ejecuta herramientas reales de
PostgreSQL sobre datos sintéticos:

1. Exige fuente `test_*`, distinta de `DB_NAME` habitual, y conexión en autocommit.
2. Comprueba que `pg_dump`, `pg_restore` y el servidor tienen la misma versión mayor.
3. Crea fixtures de usuarios/sesiones, chat, catálogo, contexto, cuotas, latidos,
   cola pendiente/fallida/aceptada, eventos y auditoría.
4. Genera un archivo custom temporal de la base de pruebas completa.
5. Crea una base nueva con nombre aleatorio `test_rehearsal_<uuid>` y verifica
   que su plantilla no contenga tablas de aplicación.
6. Restaura allí, nunca sobre la fuente. Verifica IDs, precios Decimal, JSON,
   sesiones, estados de salida, auditoría, relaciones,
   historial de migraciones, secuencias y restricciones FK.
7. Cierra conexiones y elimina únicamente la base generada que creó el ensayo,
   además de su archivo temporal. No usa `DROP ... FORCE` ni termina sesiones ajenas.

Las contraseñas se pasan al subproceso por entorno, no por argumentos ni logs.
Los archivos temporales se crean en un directorio privado del usuario y se
eliminan al terminar. No se copia la base habitual ni se restaura nada sobre ella.
Si se mata externamente el proceso de pruebas, puede interrumpirse la limpieza:
revisa nombre exacto, propietario y origen antes de retirar un resto de ensayo;
no borres bases mediante comodines ni termines sesiones ajenas.

Comando específico:

```powershell
.\venv\Scripts\python.exe manage.py test applications.chat.test_backup_restore --noinput --verbosity 2
```

Se pueden indicar ejecutables con `TEST_PG_DUMP` y `TEST_PG_RESTORE` si no están
en PATH. Si faltan o su versión no coincide, la prueba se marca como omitida y
**eso no demuestra recuperación**: revisa las omisiones en CI. Otros fallos de
copia/restauración se consideran fallos de prueba, no éxito silencioso.

El ensayo restaura sin propietarios, privilegios ni comentarios de objetos,
para no intentar modificar metadatos de la extensión preinstalada propiedad del
administrador. Valida el esquema funcional, los datos y las restricciones; no
reproduce roles/grants del servidor, comentarios, secretos, archivos ni toda la
infraestructura. Esos elementos requieren su propio procedimiento de recuperación.

## Procedimiento para una copia real futura

No se ejecutó este apartado contra datos reales. Durante el despliegue final,
define responsables, retención, cifrado, ubicación externa y objetivos de pérdida
de datos (RPO) y tiempo de recuperación (RTO).

1. Usa `pg_dump` compatible con el servidor para obtener una copia consistente.
   Para una recuperación integral conviene conservar en el archivo sus
   metadatos originales y decidir propietarios/grants al restaurar, no asumir
   que las opciones reducidas del ensayo reproducen la seguridad de producción.
2. Las credenciales deben proceder de un mecanismo seguro, por ejemplo un
   archivo de servicio/contraseñas de PostgreSQL con permisos restrictivos o
   entrada interactiva. Nunca pongas contraseñas en el historial del shell.
3. Protege por separado `.env`, `SECRET_KEY`, acceso a Meta/OpenRouter, certificados,
   configuración del proxy/supervisor y archivos persistidos si los hubiera.
   Los roles de PostgreSQL no son los usuarios de Django y necesitan respaldo
   administrado independiente. No incluyas secretos en Git ni en informes.
4. Cifra el respaldo y prueba regularmente su lectura y restauración en una
   máquina/base desechable. Un archivo creado sin error no acredita recuperación.
5. Usa una cuenta y un destino claramente aislados; bloquea salida hacia Meta e
   IA y no ejecutes workers o tareas del sistema en la restauración de ensayo.

Ejemplo de copia manual, solo después de sustituir todos los marcadores:

```text
pg_dump --host HOST --port PUERTO --username USUARIO --format=custom --file COPIA_NUEVA.dump --dbname DB_ORIGEN
```

No sobrescribas un archivo existente sin revisar su retención. Este documento
no ofrece un comando de restauración sobre la base habitual: primero restaura
en un destino nuevo, provisionado y validado por el responsable de PostgreSQL.

## Antes de reactivar tras una restauración real

- Mantén webhooks/procesos de escritura controlados y el worker detenido hasta
  completar la validación del destino y su configuración.
- Comprueba versión, extensión, migraciones, IDs, integridad, catálogo, auditoría,
  usuarios, sesiones y cuotas, además de permisos y secretos.
- Revisa toda salida pendiente/fallida recuperada. Una copia anterior puede
  decir «pendiente» de un mensaje que Meta aceptó después de esa copia.
- **No arranques el worker a ciegas:** restaurar la base no deshace mensajes ya
  enviados ni gastos remotos. Reconciliar/revisar esos estados es necesario
  para no reenviar a clientes ni subestimar el consumo de IA.
- Autoriza por separado el cambio de conexión y el arranque supervisado. Haz
  primero pruebas con destinatario controlado y presupuesto limitado.

El arranque y supervisión descritos en `docs/whatsapp-worker.md` permanecen
pendientes para el final; este ensayo no los activa.

## Resultado de la verificación

La suite completa pasó 302 pruebas, incluidas 11 nuevas de esta etapa, sin
omisiones en este entorno. El ensayo real de respaldo/restauración terminó
correctamente y eliminó su destino temporal. Django, la comprobación de
migraciones y `git diff --check` finalizaron sin problemas. No se aplicaron
migraciones a la base habitual ni se enviaron WhatsApps reales.
