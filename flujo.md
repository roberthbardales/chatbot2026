# Flujo Del RAG Vectorial Para DummyJSON

> Requisito de infraestructura: PostgreSQL debe tener instalada la extensión de servidor `pgvector`. Instalar el paquete Python `pgvector` no instala por sí solo la extensión dentro de PostgreSQL.

El Blog permanece deshabilitado y conserva su RAG textual. El RAG vectorial se utilizará únicamente para buscar productos de DummyJSON.

## 1. Preparación Del Índice

Este proceso se ejecuta inicialmente y luego periódicamente:

```text
DummyJSON
  -> Django descarga los productos
  -> Django valida la respuesta
  -> Django limpia y normaliza los datos
  -> Django crea un documento de texto por producto
  -> Servicio de embeddings convierte el texto en un vector
  -> Django guarda producto, texto y vector en PostgreSQL
```

Ejemplo de documento:

```text
Producto: Apple iPhone Charger
Descripción: Cargador rápido para iPhone
Categoría: mobile-accessories
Marca: Apple
Etiquetas: electronics, chargers
```

En PostgreSQL se guardaría:

```text
dummyjson_id = 104
title = Apple iPhone Charger
sku = MOB-APP-APP-104
content = texto descriptivo del producto
embedding = [0.021, -0.443, 0.187, ...]
content_hash = hash del contenido
```

Si el producto no cambió, no se vuelve a generar su embedding.

## 2. Pregunta Del Usuario

El usuario escribe, por ejemplo:

```text
Busco un cargador rápido para mi iPhone
```

El flujo continúa así:

```text
Usuario
  -> interfaz del chatbot
  -> endpoint Django /chat/send/
  -> validar usuario, conversación y pregunta
  -> identificar la fuente DummyJSON
```

Como el Blog está deshabilitado, solo continúa la ruta de DummyJSON.

## 3. Clasificación De La Consulta

Django revisa primero si la pregunta es una operación exacta:

```text
¿Contiene un ID?
¿Contiene un SKU?
¿Es una comparación?
¿Pide una categoría?
¿Es una búsqueda general?
```

Ejemplos:

- `Producto 104` usa búsqueda exacta por ID.
- `SKU MOB-APP-APP-104` usa búsqueda exacta por SKU.
- `Compara los productos 6 y 7` usa consultas exactas y lógica determinista.
- `Busco un cargador para iPhone` usa búsqueda vectorial.

## 4. Crear El Vector De La Pregunta

Para una búsqueda semántica, Django envía la pregunta al modelo de embeddings:

```text
Busco un cargador rápido para mi iPhone
  -> embedding
  -> [0.018, -0.421, 0.201, ...]
```

La pregunta y los productos utilizan el mismo modelo de embeddings para que sus vectores sean comparables.

## 5. Buscar En PostgreSQL

Django envía ese vector a PostgreSQL mediante `pgvector`.

PostgreSQL compara el vector de la pregunta contra los vectores de los productos:

```text
Apple iPhone Charger       similitud 0.93
Apple MagSafe Battery Pack similitud 0.81
Selfie Lamp with iPhone    similitud 0.55
```

Se seleccionan los productos más similares, por ejemplo los seis primeros.

## 6. Validar Los Resultados

Django aplica reglas antes de responder:

- Si la similitud es demasiado baja, rechaza el resultado.
- Si hay un único resultado claro, lo acepta.
- Si hay varios productos muy parecidos, pide precisión.
- Si se solicitan precio, stock o SKU, verifica esos datos contra DummyJSON.
- Nunca genera un SKU, precio o stock por inferencia.

Si no hay resultados válidos:

```text
No encontré un producto suficientemente relacionado con tu consulta.
```

## 7. Recuperar Los Datos Reales

Después de encontrar el producto semánticamente, Django obtiene sus datos exactos:

```text
ID: 104
Nombre: Apple iPhone Charger
Precio: 19.99
Stock: 31
SKU: MOB-APP-APP-104
```

La búsqueda vectorial sirve para encontrar el producto. DummyJSON sigue siendo la fuente de los datos comerciales.

## 8. Crear El Contexto

Django prepara un contexto controlado:

```text
Producto encontrado:
Nombre: Apple iPhone Charger
Descripción: Cargador diseñado para cargar rápidamente un iPhone.
Categoría: mobile-accessories
Marca: Apple
Precio: 19.99
Stock: 31
SKU: MOB-APP-APP-104
Fuente: DummyJSON
```

## 9. Generar La Respuesta

Hay dos posibilidades.

### Respuesta Determinista

Para datos exactos, Django genera directamente la respuesta sin usar el modelo generativo:

```text
Apple iPhone Charger (SKU MOB-APP-APP-104): precio 19.99 y stock de 31 unidades.
```

### Respuesta Con Modelo

Para preguntas descriptivas, Django envía el contexto validado a Gemini o Groq, con instrucciones para no inventar información:

```text
El producto más relacionado es Apple iPhone Charger. Está diseñado para cargar rápidamente un iPhone, tiene un precio de 19.99 y cuenta con 31 unidades en stock.
```

## 10. Guardar La Conversación

Django guarda en PostgreSQL:

```text
Mensaje del usuario:
Busco un cargador rápido para mi iPhone

Respuesta:
El producto más relacionado es Apple iPhone Charger...

Proveedor:
Groq

Conversación:
Usuario autenticado

Fuente:
DummyJSON
```

Opcionalmente también puede guardar:

```text
Producto recuperado: 104
Similitud: 0.93
```

## 11. Mostrar El Resultado

Finalmente:

```text
Django
  -> devuelve JSON al navegador
  -> la interfaz actualiza el chat
  -> el usuario ve la respuesta
```

## Flujo Resumido

```text
DummyJSON
  -> sincronización
  -> documento por producto
  -> embedding del producto
  -> PostgreSQL + pgvector

Pregunta del usuario
  -> validación
  -> embedding de la pregunta
  -> búsqueda vectorial
  -> validación de resultados
  -> datos exactos de DummyJSON
  -> contexto controlado
  -> respuesta determinista o Gemini/Groq
  -> guardar conversación
  -> mostrar respuesta
```
