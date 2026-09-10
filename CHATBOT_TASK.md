# Tarea: MVP Chatbot Django + Gemini + API pública del blog

Trabaja sobre mi proyecto Django existente `chatbot2026`.

## 1. Inspección previa obligatoria

Primero inspecciona todo el proyecto antes de modificar nada:

-   apps existentes;
-   `settings.py` y configuración general;
-   `INSTALLED_APPS`;
-   URLs principales y de cada app;
-   views;
-   models;
-   forms;
-   templates;
-   `base.html`, si existe;
-   archivos static;
-   Tailwind CSS y su configuración;
-   PostgreSQL y configuración de base de datos;
-   variables de entorno;
-   `.env.example`, si existe;
-   `.gitignore`;
-   dependencias (`requirements.txt`, `pyproject.toml` u otro);
-   middleware relevante;
-   autenticación existente, si la hay;
-   tests existentes;
-   convenciones y estructura actual del proyecto.

Reutiliza lo que ya existe. No dupliques configuraciones ni reemplaces
componentes existentes innecesariamente.

Si ya existe una app adecuada para alojar el chatbot, evalúa
reutilizarla. Si no existe, puedes crear una app `chat`.

Si ya existe `base.html`, extiéndelo. Si Tailwind ya está configurado,
utiliza esa configuración. No instales una segunda solución de Tailwind.
No reemplaces la configuración actual de PostgreSQL.

## 2. Objetivo

Quiero implementar un MVP de chatbot web con estas características:

-   Django + PostgreSQL ya existente.
-   Interfaz tipo chat usando Django Templates + Tailwind.
-   Ruta similar a `/chat/`.
-   Gemini API como proveedor de IA.
-   La API key debe almacenarse mediante variable de entorno.
-   Guardar conversaciones y mensajes en PostgreSQL.
-   NO usar todavía RAG vectorial, embeddings, pgvector, WhatsApp, Redis
    ni Celery.

Esta primera versión es una prueba para validar la arquitectura antes de
reutilizarla posteriormente en otro proyecto.

## 3. Fuente de información

La única fuente externa de información factual del chatbot será mi API
pública:

https://bardalesweb.duckdns.org/api/entries/entries/

Inspecciona primero el JSON real que devuelve.

Verifica qué campos, endpoints, filtros y mecanismos de búsqueda soporta
realmente. No asumas que existen parámetros como `?search=`, filtros u
ordering sin comprobarlo.

Cuando estén disponibles, quiero aprovechar campos como:

-   `id`
-   `title`
-   `resume`
-   `content`
-   `slug`
-   `category`
-   `tag` / `tags`
-   `user` / autor
-   `created`
-   `likes_count`

El chatbot puede utilizar el campo `content` completo de las entradas
relevantes.

## 4. Capacidades del chatbot

El chatbot debe poder:

-   listar artículos;
-   buscar artículos;
-   consultar una entrada específica;
-   obtener entradas recientes;
-   leer el `content` completo de una entrada relevante;
-   resumir artículos;
-   responder preguntas sobre el contenido;
-   mantener contexto entre mensajes.

Ejemplo:

Usuario:

> ¿Tienes artículos sobre GitHub?

Flujo esperado:

Usuario → Django → búsqueda en API del blog → resultados relevantes →
Gemini → respuesta basada en esos resultados.

Después:

Usuario:

> Resume el segundo.

El chatbot debe entender a qué artículo se refiere usando el historial
de la conversación.

Otro ejemplo:

Usuario:

> ¿Quién escribió ese artículo?

Debe utilizar la entrada mencionada anteriormente y responder con
información obtenida de la API.

## 5. Regla fundamental

Gemini puede utilizarse para:

-   interpretar la pregunta;
-   determinar qué información necesita;
-   resumir;
-   mantener una conversación natural;
-   redactar la respuesta final.

Pero la información factual sobre el blog debe provenir de mi API.

Si el usuario pregunta algo que no está respaldado por la información
encontrada en la API, Gemini NO debe completar la respuesta utilizando
su conocimiento general.

Ejemplo:

Usuario:

> ¿Quién ganó el mundial 2022?

Si esa información no aparece en los artículos recuperados, el chatbot
debe responder algo similar a:

> No encontré esa información en los artículos disponibles del blog.

No debe inventar ni completar información externa.

Tampoco debe inventar:

-   títulos;
-   autores;
-   fechas;
-   categorías;
-   contenido;
-   enlaces;
-   información sobre el blog.

## 6. Recuperación de información

NO implementar RAG vectorial en esta fase.

No utilizar:

-   embeddings;
-   pgvector;
-   Pinecone;
-   Chroma u otra base vectorial.

Para este MVP, utiliza una recuperación sencilla basada en la API.

No envíes todos los `content` completos de todas las entradas a Gemini
en cada pregunta.

La estrategia debe ser aproximadamente:

1.  obtener/listar las entradas necesarias;
2.  identificar entradas potencialmente relevantes;
3.  seleccionar pocas entradas;
4.  utilizar el `content` completo solamente de las seleccionadas;
5.  enviar a Gemini únicamente el contexto necesario;
6.  generar la respuesta basándose en ese contexto.

Si la API no ofrece búsqueda server-side, implementa en el servicio
Django una estrategia razonable para localizar coincidencias utilizando
información como:

-   título;
-   resumen;
-   categoría;
-   tags.

## 7. Separación de responsabilidades

No pongas toda la lógica dentro de `views.py`.

Mantén separadas conceptualmente:

-   vistas/controladores;
-   modelos;
-   URLs;
-   templates;
-   servicio principal del chatbot;
-   integración/proveedor Gemini;
-   cliente o servicio de la API del blog;
-   persistencia de conversaciones y mensajes.

La integración con Gemini debe quedar desacoplada para que en el futuro
pueda reemplazar Gemini por OpenAI u otro proveedor sin tener que
reescribir toda la aplicación.

Gemini NO debe:

-   conectarse directamente a PostgreSQL;
-   generar o ejecutar SQL;
-   decidir libremente qué URLs externas consultar.

Django debe controlar el acceso a datos y las herramientas disponibles.

## 8. PostgreSQL e historial

Utiliza la base PostgreSQL que ya está configurada en `chatbot2026`.

Necesito guardar como mínimo:

### Conversation

Información como:

-   identificador;
-   fecha de creación;
-   última actividad.

### Message

Relacionado con una conversación y con información como:

-   conversación;
-   rol;
-   contenido;
-   fecha.

Roles principales:

-   `user`
-   `assistant`

Puedes agregar campos adicionales únicamente cuando aporten valor claro
al MVP.

El historial debe permitir conversaciones contextuales como:

Usuario:

> Muéstrame artículos sobre Python.

Bot:

> Encontré estos artículos...

Usuario:

> Resume el segundo.

El sistema debe poder identificar correctamente qué significa "el
segundo".

## 9. Procesamiento de `content`

Las entradas pueden contener:

-   HTML;
-   bloques de código;
-   enlaces;
-   imágenes;
-   contenido extenso.

Antes de enviar el contenido a Gemini:

-   limpia HTML innecesario;
-   conserva el texto útil;
-   controla el tamaño enviado al modelo;
-   evita enviar contenido innecesario.

Deja además un punto claro en la arquitectura donde posteriormente pueda
definir reglas para excluir entradas o fragmentos sensibles antes de
enviarlos a la IA.

No necesito construir ahora un sistema complejo de filtrado, pero debe
ser fácil agregarlo posteriormente.

## 10. Interfaz

Crear una interfaz sencilla tipo ChatGPT usando Django Templates +
Tailwind.

Debe incluir como mínimo:

-   encabezado;
-   área de conversación;
-   diferenciación visual entre mensajes del usuario y del asistente;
-   campo para escribir;
-   botón Enviar;
-   indicador/estado de carga mientras se procesa la respuesta;
-   diseño responsive.

Prioriza funcionalidad y claridad sobre efectos visuales complejos.

## 11. Configuración y seguridad

La API key de Gemini debe cargarse mediante variable de entorno.

No:

-   escribir claves directamente en el código;
-   guardar claves en PostgreSQL;
-   subir claves al repositorio.

Si el proyecto utiliza `.env.example`, agrega allí únicamente el nombre
de la variable requerida, sin ningún secreto real.

Revisa también `.gitignore` para evitar que archivos con secretos sean
versionados accidentalmente.

## 12. Manejo de errores

Maneja correctamente situaciones como:

-   API del blog caída;
-   timeout;
-   JSON inválido;
-   Gemini no disponible;
-   límite gratuito/cuota de Gemini alcanzada;
-   conversación inexistente;
-   mensaje vacío;
-   resultados de búsqueda vacíos.

No muestres trazas ni errores técnicos completos al usuario final.

Registra información útil para desarrollo mediante el mecanismo
apropiado del proyecto.

## 13. Tests

Agrega tests básicos para las partes críticas, especialmente:

-   cliente/servicio de la Blog API;
-   búsqueda de entradas;
-   comportamiento cuando no existe información suficiente;
-   creación de Conversation;
-   guardado de Message;
-   cualquier lógica crítica del flujo conversacional que sea razonable
    probar.

Si ya existen tests, respeta la estructura existente.

## 14. Fuera de alcance

NO implementar en esta fase:

-   RAG vectorial;
-   embeddings;
-   pgvector;
-   Pinecone;
-   Chroma;
-   WhatsApp;
-   Meta WhatsApp API;
-   productos;
-   stock;
-   ecommerce;
-   carrito;
-   Redis, salvo que sea absolutamente imprescindible (en principio no
    lo es);
-   Celery;
-   WebSockets.

Quiero un MVP pequeño y entendible.

## 15. Forma de trabajo

Primero inspecciona el proyecto existente antes de cambiar archivos.

No destruyas configuraciones existentes.

Reutiliza la configuración PostgreSQL actual.

Revisa si Tailwind ya está instalado/configurado antes de instalar
cualquier cosa.

Revisa las dependencias actuales antes de agregar nuevas.

Respeta las convenciones existentes del proyecto.

Implementa los cambios de manera incremental.

Después de cada bloque importante, verifica que el proyecto siga
funcionando cuando sea posible.

Ejecuta las migraciones necesarias.

Ejecuta las comprobaciones de Django.

Ejecuta los tests existentes y los nuevos tests relevantes.

No hagas cambios innecesarios fuera del alcance.

## 16. Resultado esperado

Al finalizar quiero poder levantar Django localmente, entrar a `/chat/`
y mantener conversaciones como:

Usuario:

> Hola

Bot:

Un saludo natural y una explicación breve de que puede ayudar con los
artículos del blog.

Usuario:

> ¿Tienes artículos sobre GitHub?

Bot:

Consulta mi API y responde con información real.

Usuario:

> Resume el segundo.

Bot:

Utiliza el artículo correspondiente y su `content`.

Usuario:

> ¿Quién escribió ese artículo?

Bot:

Utiliza los datos reales de la API y el contexto anterior.

Usuario:

> ¿Quién ganó el mundial 2022?

Bot:

Indica que no encontró esa información en los artículos del blog, en
lugar de responder mediante conocimiento general.

## 17. Entrega final

Antes de modificar nada, dame un resumen corto de:

-   qué encontraste en el proyecto;
-   qué componentes existentes vas a reutilizar;
-   qué necesitas crear;
-   cuál será el plan de implementación.

Después procede con la implementación sin esperar mi confirmación,
excepto si detectas:

-   riesgo de pérdida de datos;
-   posibilidad seria de romper funcionalidad existente;
-   una decisión importante que no puedas inferir de forma segura.

Cuando termines:

1.  indica qué archivos creaste;
2.  indica qué archivos modificaste;
3.  explica brevemente el flujo completo de una pregunta;
4.  indica qué variable(s) de entorno debo configurar para Gemini;
5.  indica los comandos necesarios para ejecutar/probar el chatbot;
6.  ejecuta las comprobaciones y tests posibles;
7.  informa cualquier problema encontrado en mi API;
8.  no realices cambios adicionales después de terminar las pruebas.

El objetivo es mantener una arquitectura sencilla, limpia y reutilizable
que posteriormente pueda servir como base conceptual para un chatbot de
una tienda de cosméticos.
