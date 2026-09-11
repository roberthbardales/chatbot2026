# Plan Para Una Conversación Más Natural

El chatbot debe distinguir entre conversación cotidiana, búsquedas de productos y consultas exactas. Los mensajes cotidianos simples no deben llegar al RAG ni consumir APIs innecesariamente.

## Flujo General

```text
Mensaje del usuario
  -> detectar intención
  -> saludo, despedida o agradecimiento: respuesta directa
  -> búsqueda de producto: RAG vectorial
  -> ID, SKU o comparación: lógica determinista
  -> mensaje ambiguo: pedir aclaración
```

## 1. Detectar Conversación Cotidiana

Detectar intenciones como:

- Saludos: `hola`, `hola amigo`, `buenos días`, `qué tal`.
- Despedidas: `chau`, `adiós`, `hasta luego`.
- Agradecimientos: `gracias`, `muchas gracias`.
- Capacidades: `qué puedes hacer`, `cómo puedes ayudarme`.

Ejemplos:

```text
Usuario: Hola amigo
Asistente: ¡Hola! ¿Qué producto estás buscando?
```

```text
Usuario: Gracias
Asistente: ¡De nada! ¿Quieres consultar algún producto?
```

```text
Usuario: Chau
Asistente: ¡Hasta luego! Cuando quieras puedes volver a consultar el inventario.
```

Estos mensajes deben responderse directamente, sin consultar DummyJSON, generar embeddings ni llamar a Gemini o Groq.

## 2. Detectar Búsquedas De Productos

Detectar expresiones como:

- `busco`
- `necesito`
- `quiero encontrar`
- `estoy buscando`
- `me interesa`
- `recomiéndame`
- `algo para`

Ejemplo:

```text
Estoy buscando algo para mi celular
```

Resultado de intención:

```text
product_search
```

Este tipo de mensaje sí debe pasar al RAG vectorial.

## 3. Limpiar La Consulta

Django debe eliminar las frases conversacionales que no aportan significado, conservando la pregunta original para redactar la respuesta:

```text
Pregunta original: Estoy buscando algo para mi celular
Consulta vectorial: producto para celular
```

## 4. Ejecutar La Búsqueda Vectorial

El sistema genera el embedding de la consulta y busca productos similares en `ProductDocument` mediante `pgvector`.

Ejemplo:

```text
Consulta: producto para celular
  -> embedding
  -> búsqueda vectorial
  -> Apple iPhone Charger
  -> iPhone 13 Pro
  -> Apple MagSafe Battery Pack
```

## 5. Detectar Consultas Amplias

Una consulta como `algo para mi celular` puede devolver productos de categorías diferentes. El sistema debe revisar:

- Puntuación de similitud.
- Diferencia entre el primer y segundo resultado.
- Cantidad de categorías recuperadas.
- Si la necesidad del usuario es concreta.

Si la consulta es demasiado amplia, no debe elegir una recomendación arbitraria:

```text
Puedo ayudarte. ¿Buscas un cargador, una funda, una batería externa, un teléfono u otro accesorio?
```

También puede mostrar una orientación general:

```text
Encontré productos relacionados con celulares, como cargadores, baterías y teléfonos. ¿Qué tipo de producto necesitas?
```

## 6. Usar El Historial Para Aclaraciones

El chatbot debe conservar el contexto reciente.

```text
Usuario: Estoy buscando algo para mi celular
Asistente: ¿Buscas un cargador, una funda, una batería o un teléfono?
Usuario: Un cargador
```

La consulta combinada debe ser:

```text
cargador para celular
```

Si el usuario responde:

```text
Para iPhone
```

La consulta debe convertirse en:

```text
cargador para iPhone
```

Después se ejecuta nuevamente el RAG vectorial.

## 7. Separar Los Tipos De Consulta

```text
small talk
  -> respuesta directa

búsqueda amplia
  -> RAG vectorial
  -> pedir aclaración si es necesario

búsqueda específica
  -> RAG vectorial
  -> devolver productos relevantes

ID, SKU o nombre exacto
  -> consulta determinista

comparación
  -> consulta determinista y cálculo en Python
```

Ejemplos:

- `Hola amigo`: respuesta directa.
- `Estoy buscando algo para mi celular`: RAG y posible aclaración.
- `Busco un cargador para iPhone`: RAG vectorial específico.
- `SKU MOB-APP-APP-104`: búsqueda exacta.
- `Compara los productos 6 y 7`: consulta determinista.

## 8. Generar La Respuesta

Gemini o Groq solo deben redactar cuando sea necesario. Django debe proporcionarles:

- Pregunta original.
- Aclaraciones anteriores.
- Productos recuperados.
- Precio, stock y SKU verificados.
- Instrucciones para no inventar información.

Las preguntas amplias pueden responderse directamente desde Django:

```text
¿Qué tipo de producto buscas: cargador, funda, batería o teléfono?
```

Los precios, stocks, SKU, IDs y comparaciones deben continuar siendo deterministas.

## Resultado Esperado

```text
Usuario: Hola amigo
Asistente: ¡Hola! ¿Qué producto estás buscando?

Usuario: Estoy buscando algo para mi celular
Asistente: ¿Buscas un cargador, una funda, una batería o un teléfono?

Usuario: Un cargador para iPhone
Asistente: Apple iPhone Charger es el producto más relacionado...
```

## Plan De Implementación

1. Crear un detector de saludos y conversación cotidiana.
2. Crear un detector de intención de búsqueda de productos.
3. Separar preguntas simples de búsquedas reales.
4. Limpiar frases como `estoy buscando` o `necesito`.
5. Usar el historial reciente para completar aclaraciones.
6. Ejecutar el RAG vectorial con la consulta combinada.
7. Detectar consultas demasiado amplias.
8. Pedir aclaración en lugar de devolver resultados irrelevantes.
9. Mantener ID, SKU y comparaciones fuera del flujo generativo.
10. Añadir pruebas para saludos, búsquedas amplias, aclaraciones y búsquedas específicas.
