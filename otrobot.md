# Como funciona Chatbot2

## 1. Descripcion general

Chatbot2 es una aplicacion web para atender conversaciones de WhatsApp con
respuestas automaticas generadas por inteligencia artificial. El sistema usa
Django como backend y dashboard, Neonize como cliente de WhatsApp, PostgreSQL
como base de datos principal y OpenRouter como proveedor del modelo de IA.

Ademas de responder mensajes, el proyecto permite:

- Consultar conversaciones desde un dashboard protegido con login.
- Cambiar cada conversacion entre atencion automatica y atencion humana.
- Responder manualmente desde el dashboard.
- Mantener un historial de mensajes.
- Consultar productos, precios, stock y especificaciones.
- Sincronizar productos y combos desde Google Sheets.
- Analizar imagenes recibidas por WhatsApp.
- Evitar respuestas a mensajes antiguos, duplicados o enviados por el propio bot.
- Conservar los identificadores reales de WhatsApp, incluidos los JID `@lid`.
- Enviar respuestas mediante una cola de salida para evitar perdidas de mensajes.

El proyecto no usa WhatsApp Cloud API. La conexion se realiza mediante Neonize y
una sesion local vinculada por codigo QR.

## 2. Arquitectura general

```text
                         +------------------+
                         |    Dashboard     |
                         | Django + HTML    |
                         +--------+---------+
                                  |
                                  v
                         +------------------+
                         | API y modelos    |
                         | Django           |
                         +---+----------+---+
                             |          |
                             v          v
                     +-----------+  +-----------+
                     | PostgreSQL|  | OpenRouter|
                     +-----------+  +-----------+
                             ^          ^
                             |          |
WhatsApp <-> Neonize <-> Worker de Django
                             |
                             v
                   data/neonize-session.db
```

Hay dos procesos principales y cada uno cumple una responsabilidad distinta:

1. **Servidor web**: ejecuta Django, muestra el dashboard y atiende las API.
2. **Worker de WhatsApp**: mantiene la conexion con WhatsApp, procesa eventos,
   genera respuestas y envia mensajes.

No se debe ejecutar mas de un `whatsapp_worker` al mismo tiempo, porque dos
clientes usando la misma sesion pueden provocar desconexiones o el error
`StreamReplaced`.

## 3. Tecnologias utilizadas

- **Python**: lenguaje principal.
- **Django**: framework web, autenticacion, ORM, panel administrativo y comandos.
- **PostgreSQL**: almacena usuarios, conversaciones, mensajes, inventario y cola
  de salida.
- **Neonize**: conecta la aplicacion con WhatsApp y recibe sus eventos.
- **OpenRouter**: proporciona el modelo de lenguaje mediante el SDK de OpenAI.
- **Google Sheets**: fuente externa del inventario, publicada como CSV.
- **Gunicorn y Nginx**: componentes utilizados para produccion.

## 4. Estructura de carpetas

### `config/`

Contiene la configuracion global de Django:

- `settings.py`: base de datos, aplicaciones, variables de entorno, OpenRouter,
  inventario y parametros del worker.
- `urls.py`: rutas del administrador, login, logout y aplicacion WhatsApp.
- `wsgi.py`: punto de entrada para Gunicorn.

### `whatsapp/`

Es la aplicacion principal del proyecto:

- `models.py`: modelos persistentes.
- `views.py`: dashboard y endpoints de la API.
- `services.py`: estado de conexion, cola de mensajes y generacion de respuestas.
- `inventory.py`: sincronizacion y busqueda del inventario.
- `prompts.py`: prompt inicial del asistente.
- `admin.py`: configuracion del panel de Django Admin.
- `urls.py`: rutas de la aplicacion.
- `management/commands/`: comandos `whatsapp_worker` y `sync_inventory`.

### `integrations/providers/`

Contiene el adaptador de Neonize. Su objetivo es aislar los detalles de la
biblioteca de WhatsApp del resto del proyecto. Expone el cliente, eventos de
conexion, evento QR, evento de mensajes, conversion de JID y extraccion de texto.

### `templates/`

Contiene las plantillas HTML del login y del dashboard.

### `data/`

Contiene la sesion local de Neonize:

```text
data/neonize-session.db
```

Este archivo no reemplaza a PostgreSQL ni contiene las conversaciones del
dashboard. Solo conserva la autenticacion y sesion del cliente de WhatsApp.

## 5. Inicio de la conexion con WhatsApp

El comando `python manage.py whatsapp_worker` crea un cliente Neonize y utiliza
la sesion indicada por `NEONIZE_DATABASE_URL`. Si no se configura esa variable,
usa `data/neonize-session.db`.

El worker registra eventos para:

- Conexion exitosa: guarda el estado `connected`.
- Solicitud de QR: guarda el QR para que el dashboard pueda mostrarlo.
- Fin de sincronizacion inicial: comienza a aceptar mensajes nuevos.
- Mensajes entrantes: inicia el procesamiento de cada mensaje.

El QR se almacena temporalmente en `ConnectionState`. El servicio web lo
convierte en una imagen PNG codificada en Base64 para mostrarlo en el dashboard.
Al escanearlo desde WhatsApp, Neonize guarda la sesion local para no solicitar
un nuevo QR en cada arranque.

## 6. Flujo completo de un mensaje

### 6.1 Recepcion

Neonize emite un evento `MessageEv`. El worker extrae:

- El identificador del mensaje.
- El remitente o chat de origen.
- El JID real del contacto.
- El texto del mensaje.
- Una imagen, si el mensaje la contiene.

Las imagenes se descargan temporalmente mediante `download_any()`, se convierten
a Base64 y se envian al modelo. No se guardan permanentemente en PostgreSQL.

### 6.2 Validaciones de seguridad

Antes de guardar o responder, el worker descarta:

- Eventos recibidos durante la sincronizacion inicial del historial.
- Mensajes duplicados, identificados por `Info.ID`.
- Mensajes enviados por la propia cuenta del bot.
- Mensajes de grupos (`@g.us`).
- Eventos sin remitente, texto ni imagen.

Estas validaciones evitan responder a mensajes antiguos o generar respuestas
espontaneas durante el inicio de sesion.

### 6.3 Almacenamiento

El sistema busca o crea una `Conversation` usando el telefono o JID del contacto
y crea un `Message` con rol `user`. Tambien actualiza la fecha del ultimo
mensaje de la conversacion.

Las conversaciones nuevas comienzan en modo `AI`.

### 6.4 Agrupacion de mensajes

El worker aplica un debounce configurable mediante
`MESSAGE_DEBOUNCE_SECONDS`, cuyo valor predeterminado es 6 segundos.

Si una persona envia varios mensajes consecutivos rapidamente, la tarea anterior
se cancela y se espera el nuevo intervalo. Esto reduce respuestas fragmentadas y
permite que el modelo reciba el contexto mas completo posible.

### 6.5 Seleccion del modo

- En modo `AI`, el sistema continua hacia la generacion automatica.
- En modo `HUMAN`, guarda el mensaje, pero no genera respuesta automatica.

El operador puede cambiar este modo desde el dashboard.

### 6.6 Preparacion del contexto para la IA

`services.py` prepara una lista de mensajes para OpenRouter con:

1. El `SYSTEM_PROMPT` configurado.
2. El historial reciente de la conversacion.
3. El mensaje actual del usuario.
4. Los productos y combos relacionados, si existen.
5. La imagen recibida, si el mensaje es multimodal.

El historial se limita mediante `HISTORY_LIMIT`, que por defecto es 20 mensajes.

### 6.7 Generacion de la respuesta

El proyecto usa el SDK de OpenAI configurado con la URL de OpenRouter. El modelo
por defecto es `openai/gpt-4o-mini`, aunque puede cambiarse con
`OPENROUTER_MODEL`.

Si OpenRouter falla o no existe `OPENROUTER_API_KEY`, el error se registra y el
worker continua funcionando sin detener todo el servicio.

### 6.8 Cola de salida

La respuesta no se envia directamente al recibirla. Primero se crean:

- Un `Message` con rol `assistant`.
- Un registro `Outbox` con el telefono, contenido y estado `sent=False`.

Una tarea del worker revisa la cola aproximadamente cada dos segundos. Cuando
Neonize esta conectado, envia cada mensaje al JID original y cambia
`Outbox.sent` a `True`.

Este mecanismo separa la generacion de la respuesta del envio por WhatsApp y
permite controlar mejor los reintentos y errores de comunicacion.

## 7. Inteligencia artificial y prompt

El comportamiento del asistente se define mediante `SYSTEM_PROMPT`. Si no se
configura uno en `.env`, se usa el prompt predeterminado de `whatsapp/prompts.py`.

Adicionalmente, el sistema agrega reglas para que el modelo:

- Use solamente precios, stock y especificaciones existentes en el inventario.
- No invente productos ni cifras.
- Indique que confirmara disponibilidad cuando no encuentre un producto.
- Mencione combos relacionados cuando existan.

Cuando llega una imagen, el modelo puede describirla y ayudar a encontrar
productos similares. Si el texto no permite encontrar productos, el sistema
solicita primero entre 3 y 5 palabras clave visuales y vuelve a buscar en el
inventario.

## 8. Inventario y combos

El inventario se obtiene de dos hojas de Google Sheets publicadas como CSV:

- Hoja de productos: `GOOGLE_SHEET_PRODUCTS_CSV_URL`.
- Hoja de combos: `GOOGLE_SHEET_COMBOS_CSV_URL`.

La sincronizacion:

1. Descarga cada CSV.
2. Lee sus filas.
3. Reemplaza los productos y combos almacenados en PostgreSQL.
4. Ignora filas sin los campos obligatorios.
5. Conserva el inventario anterior si la hoja de productos llega vacia o invalida.

Se ejecuta al iniciar el worker, periodicamente según `INVENTORY_SYNC_MINUTES`
y manualmente con:

```powershell
python manage.py sync_inventory
```

Cada producto puede contener:

- SKU.
- Nombre.
- Categoria.
- Marca.
- Modelo.
- Especificaciones.
- Precio.
- Stock y unidad.
- Palabras clave.
- Descripcion.
- Estado activo o inactivo.

La busqueda normaliza mayusculas y tildes, elimina palabras comunes y pondera
mas fuertemente el nombre y las palabras clave. Se incorporan como maximo
`MAX_PRODUCTS_IN_CONTEXT` productos al prompt, cinco por defecto.

Los combos contienen un nombre, descripcion, precio opcional y una lista de SKUs.
Se muestran cuando comparten al menos un producto con los resultados de busqueda.

## 9. Modelos de datos

### `Conversation`

Representa una conversacion con un contacto.

- `phone`: JID o identificador real de WhatsApp.
- `name`: nombre opcional.
- `mode`: `AI` o `HUMAN`.
- `last_message_at`: fecha del ultimo mensaje.

### `Message`

Guarda cada mensaje de una conversacion.

- `role=user`: mensaje recibido del cliente.
- `role=assistant`: respuesta generada por la IA.
- `role=human`: respuesta escrita por un operador.
- `content`: contenido textual.
- `created_at`: fecha de creacion.

### `ConnectionState`

Guarda el estado de Neonize, el QR actual y el telefono conectado.

### `Outbox`

Representa un mensaje pendiente de envio.

- `phone`: destino.
- `content`: texto a enviar.
- `sent`: indica si ya fue enviado.

### `Product` y `Combo`

Representan el inventario sincronizado y los paquetes comerciales relacionados.

## 10. Dashboard y API

Todas las vistas del dashboard requieren autenticacion Django.

### Paginas

- `/login/`: inicio de sesion.
- `/logout/`: cierre de sesion.
- `/`: dashboard principal.
- `/admin/`: panel administrativo de Django.

### Endpoints

- `GET /api/connection/status`: devuelve estado, QR y telefono conectado.
- `POST /api/connection/disconnect`: solicita el reinicio de la conexion.
- `GET /api/conversations`: lista conversaciones ordenadas por actividad.
- `DELETE /api/conversations/<id>`: elimina una conversacion.
- `GET /api/messages/<id>`: obtiene mensajes de una conversacion.
- `POST /api/messages/<id>`: crea un mensaje manual y lo agrega a `Outbox`.
- `POST /api/mode/<id>`: cambia el modo entre `AI` y `HUMAN`.

El dashboard consulta los mensajes inicialmente y puede pedir mensajes nuevos a
partir de un identificador usando el parametro `sinceId`.

## 11. Configuracion mediante `.env`

Las variables mas importantes son:

```env
DATABASE_URL=postgresql://usuario:clave@host/base
DJANGO_SECRET_KEY=una-clave-segura
DJANGO_DEBUG=1
DJANGO_ALLOWED_HOSTS=127.0.0.1,localhost
OPENROUTER_API_KEY=sk-...
OPENROUTER_MODEL=openai/gpt-4o-mini
SYSTEM_PROMPT=Eres un asistente util y profesional.
GOOGLE_SHEET_PRODUCTS_CSV_URL=https://.../productos.csv
GOOGLE_SHEET_COMBOS_CSV_URL=https://.../combos.csv
INVENTORY_SYNC_MINUTES=5
MAX_PRODUCTS_IN_CONTEXT=5
HISTORY_LIMIT=20
MESSAGE_DEBOUNCE_SECONDS=6
NEONIZE_DATABASE_URL=
```

`DATABASE_URL` es para PostgreSQL y no debe utilizarse como base de datos de
Neonize. Neonize necesita su propio archivo de sesion local.

## 12. Ejecucion local

Instalacion inicial:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

Preparacion de Django:

```powershell
python manage.py migrate
python manage.py createsuperuser
```

Arranque normal, en dos terminales separadas:

```powershell
python manage.py runserver
```

```powershell
python manage.py whatsapp_worker
```

Luego se accede a `http://127.0.0.1:8000/login/`, se inicia sesion y se escanea
el QR si Neonize lo solicita.

## 13. Despliegue en produccion

La arquitectura de produccion recomendada es:

```text
Internet -> Nginx -> Gunicorn -> Django -> PostgreSQL

WhatsApp -> chatbot-worker.service -> Neonize/OpenRouter/PostgreSQL
```

Gunicorn sirve la aplicacion web y un servicio independiente ejecuta el worker.
La sesion de Neonize debe permanecer en una ruta persistente y pertenecer al
usuario que ejecuta el worker. Nginx funciona como proxy inverso y puede
terminar HTTPS.

El detalle de servicios, Nginx y Gunicorn se encuentra en `deploy.md`.

## 14. Diagnostico y errores frecuentes

- **`StreamReplaced`**: hay otro worker o sesion duplicada usando WhatsApp.
- **`device removed`**: cerrar sesiones vinculadas, eliminar
  `data/neonize-session.db` y volver a escanear el QR.
- **`HTTP 401` de OpenRouter**: revisar `OPENROUTER_API_KEY`.
- **No hay productos**: revisar las URLs CSV y ejecutar `sync_inventory`.
- **`Failed to delete history sync media`**: suele ser una advertencia de
  WhatsApp y normalmente no impide enviar mensajes.
- **Respuestas a mensajes antiguos**: comprobar que aparezca
  `Sincronizacion inicial terminada; se aceptan mensajes nuevos`.
- **No se envia una respuesta**: revisar el estado de `Outbox`, la conexion de
  Neonize y que solo exista un worker activo.

Mensajes utiles en los logs:

```text
Successfully authenticated
Evento MessageEv recibido
Mensaje de WhatsApp recibido de ...
HTTP/1.1 200 OK
Sincronizacion inicial terminada; se aceptan mensajes nuevos
```

## 15. Limitaciones actuales

- No existe una base de conocimiento general independiente del inventario y del
  prompt configurado.
- No utiliza la API oficial de WhatsApp Cloud.
- Las imagenes se procesan temporalmente y no se conserva un archivo historico.
- La calidad de las respuestas depende del modelo seleccionado y de la calidad
  del `SYSTEM_PROMPT`.
- El worker de WhatsApp debe mantenerse activo para recibir y enviar mensajes.

## 16. Resumen del ciclo completo

```text
1. Se inicia el servidor Django.
2. Se inicia un unico worker de WhatsApp.
3. Neonize restaura o solicita una sesion mediante QR.
4. El worker sincroniza el inventario.
5. WhatsApp entrega un mensaje nuevo.
6. El worker valida y guarda el mensaje.
7. Se espera el debounce configurado.
8. Se consulta el inventario y el historial.
9. OpenRouter genera la respuesta.
10. La respuesta se guarda en Message y Outbox.
11. Neonize envia el texto al contacto original.
12. Outbox se marca como enviado.
13. El operador puede supervisar o tomar el control desde el dashboard.
```
