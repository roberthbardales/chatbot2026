# RAG y Agente de IA

## Estado actual

El chatbot actual es principalmente una IA generativa con recuperación de información desde la API pública del blog. También puede considerarse un RAG básico, aunque todavía no utiliza embeddings ni búsqueda vectorial.

## RAG

RAG significa que el modelo recibe información recuperada desde una fuente externa antes de generar la respuesta.

```text
Pregunta
  -> búsqueda de información relevante
  -> contexto recuperado
  -> modelo generativo
  -> respuesta basada en el contexto
```

Para implementar un RAG más completo se necesitaría:

- Descargar y dividir los artículos en fragmentos.
- Crear embeddings de esos fragmentos.
- Guardar los embeddings en PostgreSQL con `pgvector`.
- Buscar los fragmentos más similares a la pregunta.
- Enviar al modelo únicamente los fragmentos relevantes.
- Mostrar o conservar las fuentes utilizadas.
- Validar que la respuesta esté respaldada por los fragmentos.

Una arquitectura posible:

```text
API del blog
  -> proceso de indexación
  -> fragmentos y embeddings
  -> PostgreSQL + pgvector
  -> búsqueda semántica
  -> Gemini/Groq
```

Se recomienda utilizar búsqueda híbrida, combinando palabras clave y similitud vectorial.

## Agente de IA

Un agente interpreta la pregunta y decide qué herramienta controlada utilizar.

Ejemplo:

```text
Usuario: ¿Quién ha publicado más posts?
  -> agente selecciona count_posts_by_author()
  -> Python consulta el API y calcula el resultado
  -> agente redacta la respuesta
```

Herramientas posibles:

```python
search_articles(query)
get_article_by_id(article_id)
get_latest_article()
get_oldest_article()
get_articles_by_year(year)
count_posts_by_author()
count_posts_by_category()
get_most_liked_articles()
```

El agente no debe tener acceso libre a Internet, SQL ni al sistema operativo. Django debe controlar las herramientas disponibles, validar sus argumentos y verificar sus resultados.

## RAG y agente combinados

```text
Pregunta del usuario
  -> agente interpreta la intención
  -> selecciona una herramienta
  -> herramienta consulta el índice vectorial, el API o funciones estadísticas
  -> datos verificados
  -> modelo redacta la respuesta
```

Ejemplos:

- “¿Cuál es el último post?” usa `get_latest_article()`.
- “¿Qué artículos hablan de Django?” usa el buscador RAG.
- “Resume el segundo artículo” resuelve la referencia y usa `get_article_by_id()`.
- “¿Quién publicó más posts?” usa `count_posts_by_author()`.

## Plan futuro recomendado

Implementar por etapas:

1. Mejorar el RAG básico actual con búsqueda textual, fragmentos, puntuaciones y fuentes.
2. Añadir RAG vectorial con embeddings y `pgvector`.
3. Añadir un agente con herramientas seguras y registro de las herramientas utilizadas.

Primero debe ser fiable la recuperación de información y después se debe añadir autonomía al agente. Un agente sobre datos mal recuperados podría seleccionar herramientas incorrectas o generar respuestas imprecisas.
