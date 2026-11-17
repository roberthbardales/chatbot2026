# Búsqueda de inventario y seguimiento de consultas


La mejora se aplica a Google Sheets/inventario local en WhatsApp. No usa embeddings.

## Búsqueda

- Se normalizan mayúsculas, acentos y un conjunto explícito de sinónimos.
  Ejemplos: `válvulas`/`valvula`, `valve`/`válvula`, `elbow`/`codo`,
  `inox`/`inoxidable`, `tubería`/`tubo` y `llave de paso`/`válvula`.
- Los alias adicionales del catálogo pueden registrarse en `keywords`, sin
  sincronizar la base de datos como parte de una prueba.
- `½`, `1/2`, `media pulgada` y `0.5 pulgadas` se reconocen como la misma medida.
  Se admiten fracciones mixtas como `1½` y `1 1/2`.
- Las unidades se mantienen separadas: 15 mm no equivale a 15 pulgadas. Una
  fracción sin unidad se interpreta como pulgadas, convención del catálogo
  industrial; para milímetros hay que escribir la unidad.
- Se conservan las diferencias entre NPT/BSP y macho/hembra. No se presume que
  dos conexiones sean compatibles.
- Se buscan nombres, SKU, marca, modelo, palabras clave, categoría y
  especificaciones de productos activos. Todos los términos relevantes de una
  búsqueda textual deben estar presentes; ya no basta con coincidir en uno.
- Un SKU exacto tiene prioridad. Las referencias explícitas con `SKU`, `código`
  o `referencia` se consultan directamente, admitiendo puntos, guiones, barras y
  guiones bajos. Un SKU inexistente no se sustituye por otro parecido.

La búsqueda es conservadora: un modelo desconocido puede producir una petición
de más información en vez de resultados parciales. No se añadió corrección
difusa de errores tipográficos ni un diccionario industrial universal.

## Confirmación y contexto

Si una búsqueda ordinaria encuentra varias opciones, el servidor devuelve una
lista numerada de nombres y SKU y solicita una elección. No permite a la IA
elegir arbitrariamente un producto y no muestra cifras hasta la confirmación.
Las consultas explícitas por categoría pueden seguir mostrando una lista de
datos del catálogo; eso no confirma un producto para la siguiente pregunta.

Ejemplo:

```text
Cliente: Busco una válvula de bronce.
Sistema: Encontré varios productos parecidos. Confirma el SKU o número de opción:
         1. Válvula de bronce 1/2 pulgada — SKU VAL-01
         2. Válvula de bronce 3/4 pulgada — SKU VAL-02
Cliente: La segunda.
Sistema: [Datos reales de VAL-02]
Cliente: ¿Y cuántas unidades hay?
Sistema: [Consulta otra vez el stock de VAL-02]
```

También se puede precisar `la de media pulgada`. Una precisión que no coincide
con las opciones no debe cambiar silenciosamente a un producto ajeno de otra
familia. Una consulta explícita por otro producto puede sustituir el contexto.

Las imágenes generan posibles coincidencias que requieren confirmación, incluso
si hay una sola. Para una única opción se acepta `sí`, `esa` o `correcto`, además
del SKU o número. La búsqueda visual no constituye identificación garantizada.

El contexto solo guarda `selected_sku` o una lista de SKU candidatos. Los precios,
stock y demás valores se vuelven a obtener del inventario activo en cada consulta.
Si un producto se elimina o desactiva, no se reutilizan sus valores anteriores.
Una búsqueda sin resultados limpia el contexto.

## Persistencia y seguridad

Se añade `inventory_context` a los mensajes de WhatsApp. Se guarda
únicamente en la respuesta del asistente, dentro de la transacción existente,
respetando el token de propiedad del intento.
Los resultados especulativos de un intento antiguo no se guardan.

El contexto se consulta exclusivamente dentro del contacto actual. No se extrae de instrucciones libres escritas por el cliente ni de textos
de respuestas antiguas. Los registros anteriores empiezan sin contexto; no se
intenta inferir retrospectivamente un producto.

Se mantiene la protección comercial: la salida de la IA solo puede seleccionar
SKU recuperados y campos permitidos; los valores visibles los redacta el servidor.
Una salida inválida o un fallo del proveedor utiliza datos locales seguros.

## Activación

El contexto se incluye en la migración inicial (`whatsapp/0001_initial`); no requiere pasos adicionales. Comprueba con `python manage.py test applications.core applications.whatsapp`.
