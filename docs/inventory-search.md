# Búsqueda de inventario y seguimiento de consultas


La mejora se aplica a Google Sheets/inventario local en WhatsApp. No usa embeddings.

Esta guía describe el motor de plantillas (`answer_sheet_message`). El agente (`AI_AGENT_ENABLED=True`, ver [agente.md](agente.md)) usa la misma búsqueda de `inventory.py` mediante sus herramientas `buscar_productos`, `listar_categoria` y `ver_producto`, pero conversa con el historial, no usa listas numeradas ni elige con «la 2», y los precios y el stock salen de marcadores. El motor de plantillas sigue siendo su respaldo.

## Búsqueda

- Se normalizan mayúsculas, acentos y un conjunto explícito de sinónimos entre el
  español y el inglés que mezcla el catálogo (`_SEARCH_SYNONYMS` en `inventory.py`).
  Ejemplos: `bloqueador`/`sunscreen` → `protector`, `cleanser` → `limpiador`,
  `cream` → `crema`, `toner` → `tonico`, `labial` → `labios`, `ampollas` → `ampolla`.
  Se aplican por igual a la consulta y a los textos de los productos, así que basta
  con que ambos lados converjan al mismo término.
- Los alias adicionales del catálogo pueden registrarse en `keywords`, sin
  sincronizar la base de datos como parte de una prueba.
- El contenido neto se unifica: `500 ml`, `500ml` y `500 mililitros` son el mismo
  término, igual que `g`, `kg`, `l` y `oz`. Las marcas con iniciales con puntos
  (`e.l.f.`) son una sola palabra.
- Se buscan nombres, SKU, marca, modelo, palabras clave, categoría y
  especificaciones de productos activos. Todos los términos relevantes de una
  búsqueda textual deben estar presentes; ya no basta con coincidir en uno.
- **Respaldo por descripción:** si la búsqueda por nombre, marca y demás campos no encuentra
  nada, se repite incluyendo la descripción (`Product.description`): los términos pueden
  repartirse entre el nombre y la descripción («protector piel sensible»). Si el nombre ya
  da resultados, la descripción no interviene. Sus términos se guardan en memoria igual que
  los del nombre y solo se calculan cuando hace falta.
- Palabras como `sirve`, `funciona`, `usar`, `uso`, `ingredientes` y `beneficios` no cuentan
  como términos de búsqueda, para que «¿para qué sirve el X?» encuentre el producto X.
- Un SKU exacto tiene prioridad. Las referencias explícitas con `SKU`, `código`
  o `referencia` se consultan directamente, admitiendo puntos, guiones, barras y
  guiones bajos. Un SKU inexistente no se sustituye por otro parecido.

La búsqueda es conservadora: un modelo desconocido puede producir una petición
de más información en vez de resultados parciales. No hay corrección difusa de
errores tipográficos. Los términos de cada producto se guardan en memoria por
proceso y se renuevan solos cuando `sync_inventory` cambia el producto
(se compara `content_hash`).

## Confirmación y contexto

La búsqueda se hace entre todos los productos, pero al cliente se le muestran como máximo
`MAX_PRODUCTS_IN_CONTEXT` (5 por defecto). Si hay más, la respuesta añade «Hay N productos en
total y te muestro 5…» para que el cliente afine con la marca, el tipo o el tamaño. Solo los
productos mostrados se guardan como opciones elegibles.

Si una búsqueda ordinaria encuentra varias opciones, el servidor devuelve una
lista numerada de nombres y SKU y solicita una elección. No permite a la IA
elegir arbitrariamente un producto y no muestra cifras hasta la confirmación.
Las consultas explícitas por categoría pueden seguir mostrando una lista de
datos del catálogo; eso no confirma un producto para la siguiente pregunta.

Ejemplo:

```text
Cliente: Tienes protector solar?
Sistema: Encontré varios productos parecidos. Dime cuál es con el número de opción:
         1. Beauty of Joseon Relief Sun SPF50
         2. Skin1004 Centella Sun Cream 50ml
Cliente: La segunda.
Sistema: [Datos reales del segundo producto]
Cliente: ¿Y cuántas unidades hay?
Sistema: [Consulta otra vez el stock del segundo producto]
```

También se puede precisar `la de 50 ml`. Una precisión que no coincide
con las opciones no debe cambiar silenciosamente a un producto ajeno de otra
familia. Una consulta explícita por otro producto puede sustituir el contexto.

Las imágenes generan posibles coincidencias que requieren confirmación, incluso
si hay una sola. Para una única opción se acepta `sí`, `esa` o `correcto`, además
del SKU o número. La búsqueda visual no constituye identificación garantizada.

El contexto solo guarda `selected_sku` o una lista de SKU candidatos. Los precios,
stock y demás valores se vuelven a obtener del inventario activo en cada consulta.
Si un producto se elimina o desactiva, no se reutilizan sus valores anteriores.
Una búsqueda sin resultados limpia el contexto. Los saludos, agradecimientos y despedidas
se contestan con un texto fijo, sin buscar ni llamar a la IA, y conservan el contexto.

## Preguntas sobre la descripción

Cuando el cliente pregunta algo que la descripción responde («¿para qué sirve?», «¿es para
piel grasa?»), la IA elige el campo `descripcion` y el servidor hace una segunda llamada
(`_answer_from_description` en `services.py`):

- La IA ve **solo** el nombre y la descripción del producto y la pregunta. Debe contestar en
  3 frases como máximo, sin inventar y sin mencionar precio ni stock. La respuesta se recorta
  a 700 caracteres.
- Si la descripción no contiene lo preguntado, la IA responde `NO_DATO` y el cliente recibe
  «No tengo ese dato en la descripción del producto.».
- Si la IA falla, se muestra la descripción literal recortada a 500 caracteres.
- Solo se hace con un producto. En una lista de varios se muestra la descripción literal.
- Cuesta una llamada más a la IA (2 en total) y cuenta en el cupo diario.
- La primera llamada (elegir campos) ya no recibe la descripción completa, solo
  `tiene_descripcion`, para gastar menos tokens.
- **Seguimiento:** tras elegir un producto, una pregunta cuyas palabras no encuentran nada pero sí
  están en el nombre o la descripción del producto elegido («¿es para piel grasa?») se responde
  sobre ese producto. Si alguna palabra no aparece ahí («base mate»), se responde «No encontré…».
  Por eso «¿tiene retinol?» como seguimiento da «No encontré…» si «retinol» no figura en el producto.

## Persistencia y seguridad

Se añade `inventory_context` a los mensajes de WhatsApp. Se guarda
únicamente en la respuesta del asistente, dentro de la transacción existente,
respetando el token de propiedad del intento.
Los resultados especulativos de un intento antiguo no se guardan.

El contexto se consulta exclusivamente dentro del contacto actual. No se extrae de instrucciones libres escritas por el cliente ni de textos
de respuestas antiguas. Los registros anteriores empiezan sin contexto; no se
intenta inferir retrospectivamente un producto.

Se mantiene la protección comercial: la salida de la IA solo puede seleccionar
SKU recuperados y campos permitidos; los valores visibles los redacta el servidor. Los nombres de campo
en inglés que la IA toma del inventario (`brand`, `category`, `specs`…) se aceptan como equivalentes
de los españoles; cualquier otro nombre invalida la selección y se usan precio y stock.
Una salida inválida o un fallo del proveedor utiliza datos locales seguros.

## Activación

El contexto se incluye en la migración inicial (`whatsapp/0001_initial`); no requiere pasos adicionales. Comprueba con `python manage.py test applications.core applications.whatsapp`.
