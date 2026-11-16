# Cómo funciona el chatbot

Guía del flujo completo, desde que el catálogo entra al sistema hasta que el cliente recibe su respuesta. Está escrita a partir del código actual (revisión 2026-11-16); no certifica el estado de producción. Para el detalle técnico por módulo consulta [docs/arquitectura.md](docs/arquitectura.md).

## 1. La idea central

El chatbot responde preguntas de **precio, stock y características** de los productos de un catálogo, por dos canales: **chat web** y **WhatsApp**.

La regla que lo gobierna: **la IA nunca escribe precios ni stock**. Los productos se buscan en una base de datos local; la IA solo decide *cuáles* campos mostrar, y el servidor redacta la respuesta con los valores reales del inventario. Si la IA se equivoca o inventa algo, su respuesta se descarta.

## 2. Vista general

```text
 Google Sheets (CSV)
        │   sync_inventory  (manual)
        ▼
  Tabla Product (PostgreSQL)  ◄───────────────────────────────┐
        ▲                                                      │
        │ búsqueda textual                                     │
   answer_sheet_message  ──►  OpenRouter (IA)                  │
        ▲        │            · elige SKU y campos (JSON)      │
        │        │            · describe imágenes (WhatsApp)   │
        │        ▼
   respuesta redactada con valores de la base de datos
        ▲                                   │
  Chat web (/chat/)                  WhatsApp: webhook → cola → worker → Outbox → Meta
```

Son dos procesos que deben estar en marcha: el **servidor web** (Django) y, para WhatsApp, el **worker** (`whatsapp_worker`). Sin el worker, WhatsApp recibe mensajes pero no responde.

## 3. Paso 0: cómo entra el catálogo

El chat **no lee el CSV de Google en cada pregunta**. El catálogo se copia a la tabla `Product` mediante un comando manual:

La hoja se lee desde `GOOGLE_SHEET_PRODUCTS_CSV_URL`, que debe devolver CSV directo; para Google Sheets usa `https://docs.google.com/spreadsheets/d/<ID>/export?format=csv` (el enlace `/edit?usp=sharing` devuelve HTML y la sincronización falla con «Cabeceras CSV ausentes…»). Columnas reconocidas: `ID producto`/`SKU`, `Producto`, `Marca`, `Tipo`, `Precio`, `Stock`, `Disponible`, `Precio anterior` y `URL`; las demás se ignoran. Si hay `Stock`, la cantidad manda sobre `Disponible`. Antes de sincronizar, la base debe tener las migraciones al día (`python manage.py migrate`).

1. `python manage.py sync_inventory` descarga la hoja, la valida y aplica todo en una transacción, en un solo paso.
2. `python manage.py sync_inventory --dry-run` (opcional) hace lo mismo pero solo muestra la **vista previa** (productos nuevos, cambiados, desactivados) sin escribir nada.

Reglas que protegen el catálogo:

- Cada producto necesita **SKU y nombre**; los SKU duplicados hacen fallar la sincronización.
- Los precios se guardan como `Decimal`; un CSV inválido se rechaza completo.
- Los productos que desaparecen de la hoja se **desactivan**, no se borran.
- Si desaparecen demasiados a la vez (25 % por defecto), la baja masiva se bloquea salvo `--allow-large-drop`.

Consecuencia práctica: **si cambias un precio en la hoja y no sincronizas, el chat responde con el precio anterior**. Al terminar, `sync_inventory` indica cuántos productos hay en el catálogo.

## 4. Flujo A: chat web

Ruta pública `/chat/`. Cada mensaje es una petición `POST /chat/send/`.

```text
Cliente escribe ──► validación ──► límites ──► answer_message ──► guardar ──► respuesta
```

1. **Validación**: el texto no puede estar vacío ni superar 4000 caracteres.
2. **Límites de uso** (`limits.py`, todo en PostgreSQL):
   - Frecuencia: 20 mensajes por minuto por sesión, 30 por usuario autenticado, 120 por IP.
   - Concurrencia: una consulta a la vez por usuario/sesión.
   - Si se excede, el cliente recibe un 429 con el tiempo de espera.
3. **Conversación**: si el mensaje trae un ID de conversación, se comprueba que pertenezca al visitante (anónimo: IDs guardados en su sesión; autenticado: su usuario). Si no hay ID, se prepara una conversación nueva.
4. **Respuesta**: `answer_message` → `answer_sheet_message` (sección 6).
5. **Guardado**: *después* de tener la respuesta se abre una transacción y se guardan la conversación y los dos mensajes (usuario y asistente), este último con su `inventory_context`. La llamada a la IA nunca ocurre dentro de la transacción.
6. **Respuesta al navegador**: el ID de conversación y el texto.

## 5. Flujo B: WhatsApp

WhatsApp es asíncrono: recibir y responder son dos etapas separadas por una cola en la base de datos.

### 5.1 Entrada: el webhook

```text
Cliente ─► Meta ─► POST /whatsapp/webhook/ ─► firma HMAC ─► cola (status=pending) ─► 200 OK
```

- Meta firma cada petición; el sistema recalcula el HMAC-SHA256 sobre el cuerpo original y rechaza (403) lo que no coincide.
- Solo se aceptan eventos del número de teléfono configurado, y solo mensajes de **texto e imagen**.
- Cada mensaje se guarda como `WhatsAppMessage` en estado `pending`, **deduplicado por `message_id`** (Meta puede reenviar).
- El webhook **no** llama a la IA, no descarga imágenes y no envía nada. Responde 200 cuanto antes.
- Los avisos de estado de entrega (`sent`, `delivered`, `read`, `failed`) también llegan por aquí (sección 5.4).

### 5.2 Proceso: el worker

El worker (`whatsapp_worker`) repite cada 2 segundos:

**Fase de mensajes entrantes** (`process_pending_messages`):

1. **Recupera mensajes abandonados**: si un mensaje lleva más de 10 minutos en `processing` (el worker cayó), vuelve a `pending`. Tras 3 intentos pasa a `failed` y se encola un aviso de error al cliente (salvo modo humano).
2. **Debounce**: espera 6 segundos desde el mensaje, por si el cliente escribe varias líneas seguidas.
3. **Agrupa por contacto**: todos los mensajes pendientes de una misma persona se juntan en **una sola consulta** (los textos se unen con saltos de línea; si hubo imágenes se usa la última). Se "reclaman" con un token para que dos workers no procesen lo mismo.
4. **Modo humano / contacto inactivo**: si un operador tomó el control o el contacto está desactivado, el mensaje se cierra **sin responder**.
5. **Límite por contacto**: 20 consultas por minuto; si se excede, el mensaje se aplaza.
6. **Imagen**: si hay una, se descarga de Meta (tope de tamaño configurable) y se pasa a la IA como dato en memoria. **No se guarda**.
7. **Respuesta**: la misma lógica del chat web, `answer_sheet_message` (sección 6), usando el historial del contacto como contexto.
8. **Cierre**: se vuelve a comprobar, bajo bloqueo, que el intento sigue siendo dueño de los mensajes y que el contacto no pasó a modo humano mientras tanto. Si todo es válido, se guarda la respuesta del asistente y se crea el `Outbox`.

Entre un contacto y el siguiente, el worker **ejecuta también la fase de salida**, para que las respuestas ya listas no esperen a toda la tanda.

### 5.3 Salida: Outbox y envío

```text
respuesta ─► Outbox (1 o varias partes) ─► send_text_message ─► Meta ─► cliente
```

- Un mensaje de WhatsApp admite 4096 caracteres. Las respuestas más largas se parten en **«Parte i/n»**; cada parte es una fila del `Outbox`, enlazadas por un grupo. Una parte no sale hasta que las anteriores fueron aceptadas.
- El worker reclama cada parte con un bloqueo y **cuenta el intento al reclamarla**, así un fallo a mitad no permite reintentos infinitos.
- Si Meta falla de forma temporal, se reintenta con espera creciente (30 s, 60 s, 120 s… hasta 1 hora) y hasta 5 intentos. Si el error es permanente (por ejemplo, credenciales), la parte falla de inmediato y las siguientes del grupo se marcan como fallidas.
- Cuando Meta acepta el mensaje, la parte queda `sent=True`. **«Enviado» significa «aceptado por Meta», no «entregado».**

### 5.4 Confirmaciones de entrega

Meta avisa después, por el mismo webhook, si el mensaje se envió, se entregó o se leyó (o falló). Cada aviso se valida, se guarda como `DeliveryEvent` y se aplica al `Outbox` (`delivery_status`: `accepted → sent → delivered → read`, o `failed`). Si el aviso llega antes de que se guarde el envío, se concilia después.

## 6. El cerebro compartido: `answer_sheet_message`

Es la única lógica que entiende la pregunta. La usan los dos canales.

**Paso 1: contexto previo.** Mira el último mensaje del asistente y su `inventory_context`, que puede traer una lista de `candidates` (opciones ofrecidas) o un `selected_sku` (producto ya elegido). Así entiende «la 2» o «y el stock?».

**Paso 2: imagen (solo WhatsApp).** Si hay imagen, se le pide a la IA entre 3 y 8 palabras clave visibles (o `desconocido`), y se usan como texto de búsqueda. Una imagen nunca confirma un producto por sí sola: siempre se piden confirmaciones.

**Paso 3: decidir de qué producto se habla**, en este orden:

| Orden | Caso | Ejemplo |
| --- | --- | --- |
| 1 | SKU explícito | «sku ABC-123» |
| 2 | Elección entre opciones ofrecidas antes | «la 2», «primera», «sí, esa» |
| 3 | Sin términos de búsqueda → producto o lista del contexto | «y el stock?» |
| 4 | Categoría | «categoría: válvulas» |
| 5 | Búsqueda textual | «válvula de bronce 1/2"» |

**La búsqueda textual** (`inventory.py`) trabaja sobre los productos activos:

- Quita tildes y palabras vacías («precio», «tienes», «quiero»…).
- Aplica sinónimos (`válvulas → válvula`, `inox → inoxidable`).
- Normaliza medidas: `1/2"`, `½`, «media pulgada» y `1/2 pulgada` se tratan igual; también el contenido neto (`500 ml`, `500ml`, `55 g`) y las marcas con iniciales con puntos (`e.l.f.` = `elf`).
- Un producto coincide solo si **todos** los términos de la consulta están en su nombre, SKU, marca, modelo, palabras clave, categoría o especificaciones.
- Si el texto contiene un SKU exacto de un producto, ese gana.

**Paso 4: según el resultado**

- **Ninguno** → «No encontré ese producto en el inventario disponible…» y pide marca o una característica más.
- **Varios** → lista numerada («1. Nombre del producto»), y los SKU se guardan como `candidates`. Los SKU legibles (p. ej. `VAL-01`) se muestran; los identificadores internos de 32 o más caracteres hexadecimales no, porque el cliente no puede usarlos.
- **Uno** (o una categoría) → pasa al paso 5.

**Paso 5: la IA elige qué mostrar.** Se le envía el producto (como *datos*, no instrucciones) y se le exige exactamente este JSON:

```json
{"productos": [{"sku": "…", "campos": ["precio", "stock"]}]}
```

El servidor rechaza cualquier otra cosa: claves extra, SKU que no estaba entre los recuperados, SKU repetido, campos no permitidos (los permitidos son precio, stock, marca, modelo, categoría, especificaciones y descripción). Si la IA falla, no hay clave o responde algo inválido, se usan **precio y stock** por defecto.

**Paso 6: redacción.** El servidor escribe la frase («…: está a 12.50 y hay 40 unidades.») con los valores de la base de datos. Si falta un dato, dice «no tengo el precio en el inventario». El stock sale de la columna `Stock` de la hoja (p. ej. «hay 13 unidades») y tiene cuatro formas: «hay 8 unidades», «está agotado» (stock 0 o `Disponible = No`), «está disponible; consulta la cantidad exacta» (`Disponible = Sí` sin cantidad) y «no tengo el stock en el inventario» (sin ningún dato). Se guarda `selected_sku` en el contexto para la siguiente pregunta.

## 7. Ejemplo completo

| Turno | Cliente | Qué hace el sistema | Contexto guardado |
| --- | --- | --- | --- |
| 1 | «tienes válvulas?» | Sin SKU ni contexto; búsqueda: `valvula` coincide con varios productos → lista numerada | `candidates: [A1, B2, C3]` |
| 2 | «la 2» | Reconoce el ordinal → producto B2 (confirmado) → la IA elige campos → respuesta | `selected_sku: B2` |
| 3 | «y el stock?» | Todas las palabras son vacías → usa `selected_sku` = B2 → respuesta con el stock | `selected_sku: B2` |
| 4 | «sku NOEXISTE999» | SKU no encontrado → «No encontré ese producto…» | vacío |

## 8. Cuando algo falla

| Situación | Qué ocurre |
| --- | --- |
| Producto no encontrado | Mensaje fijo pidiendo más datos; la IA no inventa |
| IA caída o sin clave | Se responde con precio y stock del inventario; con imágenes, «desconocido» |
| Cupo diario de IA agotado o IA ocupada | Web: error 429 con tiempo de espera. WhatsApp: el mensaje se aplaza y se reintenta |
| Error al descargar la imagen o fallo inesperado | Se avisa al cliente con un mensaje de disculpa; el contexto de opciones previas se conserva |
| Meta rechaza el envío | Reintentos con espera creciente; si es permanente, la parte falla y queda visible en el panel |
| El worker se cae a mitad | El mensaje se recupera a los 10 minutos; tras 3 intentos fallidos se avisa al cliente |
| Un operador toma el control mientras se procesa | La respuesta del bot se descarta; no se duplica con la humana |

## 9. Control humano y panel

El panel `/whatsapp/` (solo superusuario u ocupación Administrador/Empleado) permite:

- Ver conversaciones e historial, con el estado de cada parte (pendiente, enviada, entregada, leída, fallida).
- **Cambiar un contacto entre modo IA y modo humano.** En modo humano el bot no responde.
- **Enviar un mensaje manual**: solo se crea una entrada en el `Outbox`; el worker lo envía. «Encolado» no es «enviado».
- Ver fallos de envío y **reactivarlos** con motivo obligatorio y confirmación de riesgo. Solo se permite si Meta nunca aceptó la parte, hay un enfriamiento entre reintentos y queda registro de auditoría.
- Ver el estado del worker, colas, consumo de IA y alertas.
- Borrar una conversación: exige confirmación explícita y se rechaza si hay mensajes pendientes de procesar o enviar.

## 10. Límites y seguridad

- **Frecuencia y concurrencia** por sesión, usuario, IP y contacto; **cupo diario de IA** (1000 peticiones y 2 000 000 de tokens *reservados* por defecto; son reservas locales, no facturación). Todo se guarda en PostgreSQL, sin Redis.
- **Webhook** protegido por token de verificación (obligatorio) y firma HMAC.
- **Privacidad**: los teléfonos aparecen enmascarados en los logs (`***1234`); las imágenes de clientes solo viven en memoria; las credenciales van por variables de entorno.
- **Producción**: con `DEBUG=False` se fuerza HTTPS y cookies seguras.

## 11. Qué debe estar corriendo y cómo comprobarlo

```powershell
python manage.py runserver                 # servidor web
python manage.py whatsapp_worker           # worker (ENVÍA WhatsApps reales)
```

Comprobaciones de solo lectura:

```powershell
python manage.py whatsapp_healthcheck      # worker vivo y progresando
python manage.py whatsapp_operations_check # colas, fallos y alertas
python manage.py ai_usage                  # consumo local de IA del día
```

Mantenimiento: `cleanup_chat_limits` y `cleanup_whatsapp_data` (con `--dry-run` solo cuenta).

## 12. Límites conocidos

- La búsqueda exige que **todos** los términos coincidan: un adjetivo que el producto no tenga anula el resultado.
- Los listados por categoría muestran hasta 5 productos sin indicar si hay más.
- El orden de envío solo se garantiza dentro de una misma respuesta; un mensaje manual puede salir antes que una respuesta del bot que está reintentando.
- De varias imágenes en un grupo de mensajes solo se analiza la última.
- No hay sincronización automática del catálogo: depende del comando manual.

## Más información

[docs/arquitectura.md](docs/arquitectura.md) · [docs/inventory-sync.md](docs/inventory-sync.md) · [docs/inventory-search.md](docs/inventory-search.md) · [docs/usage-limits.md](docs/usage-limits.md) · [docs/whatsapp-worker.md](docs/whatsapp-worker.md) · [docs/whatsapp-outbox.md](docs/whatsapp-outbox.md) · [docs/whatsapp-delivery.md](docs/whatsapp-delivery.md) · [docs/whatsapp-operations.md](docs/whatsapp-operations.md) · [docs/whatsapp-panel.md](docs/whatsapp-panel.md)
