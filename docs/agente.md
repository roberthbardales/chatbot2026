# Agente conversacional con herramientas

Revisión: 2026-11-19. Diseño y fases en [plan-agente.md](../plan-agente.md).

Con `AI_AGENT_ENABLED=True` el worker responde con un **agente**: la IA conversa con el historial reciente del contacto, pide herramientas de solo lectura sobre el catálogo y redacta la respuesta en lenguaje natural. Con `False` (valor por defecto) responde el motor de plantillas de siempre (`answer_sheet_message`). Si el agente falla, responde ese mismo motor como **respaldo**.

La regla de seguridad no cambia: **la IA nunca escribe precios ni stock**. Escribe marcadores y el servidor pone el valor real de la base de datos.

## Flujo de un mensaje

1. El worker agrupa los mensajes del contacto, comprueba modo humano y límites (como siempre).
2. Si trae imagen, `image_keywords` la convierte en palabras clave (como siempre).
3. `whatsapp/services.py::_generate_response` arma el historial (`_agent_history`) y llama a `core/agent.py::answer_with_agent`.
4. Bucle: la IA pide una herramienta, el servidor la ejecuta y devuelve el resultado, hasta que la IA redacta. Máximo `AI_AGENT_MAX_TOOL_CALLS` herramientas; la última vuelta se hace sin herramientas para forzar el texto.
5. `render_markers` valida el texto y sustituye los marcadores.
6. Si el validador lo rechaza, **un único reintento** con un aviso del sistema que explica el motivo. Si vuelve a fallar, se lanza `AgentRejected`.
7. Cualquier `AgentError` (proveedor caído, texto vacío, rechazo dos veces) o error inesperado hace que responda el motor de plantillas. El cupo agotado (`LimitExceeded`) **aplaza** el mensaje, como siempre; un `context_limit` (contexto demasiado grande) sí cae al motor de plantillas.

## Herramientas (solo lectura)

| Herramienta | Parámetros | Devuelve |
| --- | --- | --- |
| `buscar_productos` | `consulta`, `ordenar` (`precio_asc`/`precio_desc`), `precio_maximo` | Hasta `MAX_PRODUCTS_IN_CONTEXT` (5) productos, el total y una `nota` si hay más |
| `listar_categoria` | `categoria`, `ordenar`, `precio_maximo` | Igual, filtrado por categoría |
| `ver_producto` | `sku` o `nombre` | Ficha: marca, categoría, modelo, especificaciones y descripción |

- Cada producto trae `sku`, `nombre`, `marca`, `categoria`, `tiene_precio`, `estado_stock` (disponible, agotado o desconocido) y un resumen de 200 caracteres. **Nunca el precio ni la cantidad.**
- `ordenar` y `precio_maximo` los calcula el servidor con los precios reales; por eso «el más barato» y «de menos de 80» funcionan sin que la IA vea cifras.
- `ver_producto` acepta el nombre porque el historial solo guarda el texto de la conversación, no los SKU de los resultados. Si el nombre es ambiguo devuelve la lista para que la IA pregunte cuál.
- Los datos del catálogo se entregan como datos, no instrucciones.

## Marcadores

La IA escribe `{precio:SKU}`, `{stock:SKU}`, `{marca:SKU}` y `{categoria:SKU}`; el servidor los sustituye:

| Marcador | Resultado |
| --- | --- |
| `{precio:SKU}` | `S/ 69.52` (siempre dos decimales) o «precio no disponible» |
| `{stock:SKU}` | «hay 5 unidades», «hay 1 unidad», «está agotado», «está disponible; consulta la cantidad exacta» o «no tengo el stock» (es una frase completa con verbo) |
| `{marca:SKU}`, `{categoria:SKU}` | El texto de la base, o «sin marca registrada» / «sin categoría registrada» |

## Validación (`render_markers`)

Se rechaza la respuesta si:

1. Un marcador usa un SKU que ninguna herramienta devolvió **en este mensaje** (`unknown_sku`).
2. Quedan llaves sueltas o un tipo de marcador que no existe (`stray_braces`).
3. Hay cifras de precio o stock escritas a mano: `70.00`, `S/ 70`, `70 soles`, `5 unidades`, `quedan 3` (`hand_written_figure`). Se aceptan tamaños y factores (`50ml`, `FPS50+`, `0.25%`) y las cifras que ya aparecen en el mensaje del cliente o en la conversación previa.
4. Aparece un SKU o un identificador largo (`visible_sku`, `visible_identifier`).
5. El cliente pidió precio o stock y la respuesta no trae el marcador (`missing_requested_figure`). Se exime si la búsqueda no encontró ningún producto. Palabras detectadas: `precio`, `cuesta`, `barato`, `caro`, `stock`, `unidades`, `quedan`, `disponible`, `agotado`, `cantidad`…
6. Dice «agotado» o «disponible» de un producto y no coincide con su estado real (`wrong_stock_state`). Solo se comprueba en líneas que nombran, con su nombre completo, a un único producto.
7. El texto queda vacío o supera 1200 caracteres.

Los `**` se corrigen a `*` (negrita de WhatsApp) sin rechazar. Si la lista es parcial (hay más productos de los mostrados) y la respuesta no dice el total, el servidor añade «Hay N productos en total y te muestro M…».

Es una **validación heurística**: reduce mucho el riesgo, pero no es una prueba formal. No detecta cifras escritas con palabras («setenta soles») ni atribuciones erróneas de una descripción a otro producto; eso lo controlan el prompt y la revisión de conversaciones reales.

## Historial

`_agent_history` toma los últimos `AI_HISTORY_MESSAGES` (10) mensajes anteriores al lote actual, ordenados por `pk` (no por `created_at`: dos mensajes seguidos pueden compartir marca de tiempo en Windows). Los del operador cuentan como del asistente. No incluye mensajes pendientes ni los avisos de error. Las imágenes antiguas aparecen como «[El cliente envió una imagen]». No hay tablas nuevas.

El agente guarda en `inventory_context` el producto o la lista que consultó, para que el motor de respaldo siga entendiendo «la 2» o «y el stock?». Tras un saludo conserva el contexto anterior.

## Ajustes

| Variable | Predeterminado | Función |
| --- | --- | --- |
| `AI_AGENT_ENABLED` | `False` | Enciende o apaga el agente |
| `AI_AGENT_MAX_TOOL_CALLS` | `4` | Herramientas por mensaje |
| `AI_AGENT_TEMPERATURE` | `0.3` | Creatividad |
| `AI_HISTORY_MESSAGES` | `10` | Mensajes de historial; `0` lo desactiva |
| `AI_DAILY_REQUEST_LIMIT` | `3000` | Antes 1000: cada mensaje usa de 2 a 6 llamadas |
| `AI_DAILY_TOKEN_LIMIT` | `6000000` | Antes 2 000 000 |

El modelo es `OPENROUTER_MODEL` (`openai/gpt-4o-mini` por defecto). El bloqueo por contacto se amplía con el agente activo: `OPENROUTER_TIMEOUT × (AI_AGENT_MAX_TOOL_CALLS + 8) + 60` segundos.

## Costo y latencia

- Un mensaje usa **2 llamadas a la IA** en el caso simple (herramienta + respuesta) y hasta unas 6 o más con un reintento y varias fichas. Los saludos usan 1.
- Cada respuesta del agente deja una línea en el log, sin contenido: `Agent answered: calls=2 tools=1 retried=False seconds=4.2`. Los rechazos dejan `Agent answer rejected: reason=… retried=… calls=…`. Úsalas para medir el costo real, la tasa de reintentos y el uso del respaldo.
- La respuesta tarda el debounce (6 s) más las llamadas: cuenta con 10 a 20 s.
- Lo medido en pruebas locales con IA real son conversaciones de 6 a 8 mensajes; **no hay mediciones con tráfico real**.

## Probar sin Meta

`python manage.py probar_agente` abre un chat en la terminal (usa la IA real y el catálogo local; no guarda nada ni envía WhatsApps, pero gasta cupo de IA). Muestra las herramientas usadas y, si el validador rechaza algo, el motivo y el texto. Ver `docs/probar-el-bot.md`.

Las pruebas automáticas no llaman a OpenRouter: simulan `chat_with_tools`, y las del worker fijan `AI_AGENT_ENABLED=False` para no depender del `.env`.

## Qué no hace

- Solo habla del catálogo: no hay horarios, envíos ni devoluciones (fase futura: `consultar_politicas`).
- No toma pedidos, reserva ni cobra, y no deriva a una persona por su cuenta (fase futura: `pasar_a_humano`).
- No compara productos con una herramienta propia ni usa embeddings.
- No hay prueba real con Meta todavía.

## Apagarlo

`AI_AGENT_ENABLED=False` y reiniciar el worker. No hay migraciones ni datos que revertir.
