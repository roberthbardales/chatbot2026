# Plan de integración con WhatsApp

## 1. Arquitectura

Se utilizará **WhatsApp Business Cloud API de Meta** directamente.

```text
Usuario de WhatsApp
        -> WhatsApp Cloud API de Meta
        -> Webhook público en Django
        -> normalización y detección de intención
        -> Servicio actual answer_message()
        -> DummyJSON / PostgreSQL + pgvector / Gemini / Groq
        -> WhatsApp Cloud API
        -> Respuesta al usuario
```

La lógica existente de `applications/chat/services.py` se reutilizará, especialmente `answer_message()` y `answer_product_message()`. No se debe duplicar la lógica del chatbot para WhatsApp. El Blog está deshabilitado y no debe exponerse por WhatsApp durante esta fase.

## 2. Configurar Meta

1. Crear una cuenta en Meta for Developers.
2. Crear una aplicación de tipo Business.
3. Añadir el producto WhatsApp.
4. Crear o asociar una cuenta de WhatsApp Business.
5. Obtener el `PHONE_NUMBER_ID`, el `WHATSAPP_BUSINESS_ACCOUNT_ID` y el token de acceso.
6. Configurar primero el número de prueba de Meta.
7. Añadir el número personal como destinatario de prueba.
8. Registrar el número real del negocio después de completar las pruebas.

## 3. Preparar DuckDNS y el VPS

Usar un subdominio, por ejemplo:

```text
tuchatbot.duckdns.org
```

El subdominio debe apuntar a la IP pública del VPS de DigitalOcean.

En el VPS se debe:

- Abrir los puertos `80` y `443`.
- Configurar Nginx como proxy inverso.
- Ejecutar Django mediante Gunicorn y `systemd`.
- Emitir un certificado HTTPS con Let’s Encrypt.
- Verificar que funcione `https://tuchatbot.duckdns.org/`.

Meta exige un webhook público con HTTPS válido. No acepta `127.0.0.1`, HTTP simple ni certificados autofirmados.

## 4. Variables de entorno

Añadir al `.env` del servidor, sin subirlo a Git:

```env
WHATSAPP_VERIFY_TOKEN=un-token-secreto-creado-por-ti
WHATSAPP_ACCESS_TOKEN=token-de-meta
WHATSAPP_PHONE_NUMBER_ID=id-del-numero
WHATSAPP_API_VERSION=vXX.X
WHATSAPP_DEFAULT_PROVIDER=groq
WHATSAPP_DEFAULT_SOURCE=dummyjson
WHATSAPP_MAX_MESSAGE_LENGTH=2000
```

También se deben configurar correctamente `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS` y `DEBUG=False`.

## 5. Crear la aplicación Django

Crear una app separada:

```text
applications/whatsapp/
├── __init__.py
├── apps.py
├── urls.py
├── views.py
├── services.py
├── meta_api.py
└── tests.py
```

Responsabilidades:

- `views.py`: recibir y validar los webhooks.
- `meta_api.py`: comunicarse con la API de Meta.
- `services.py`: adaptar el mensaje de WhatsApp al chatbot.
- `tests.py`: probar recepción, errores, duplicados y respuestas.

La app debe importar los servicios existentes de `applications.chat`; no debe crear una segunda implementación del RAG vectorial.

## 6. Implementar el webhook

### Verificación GET

Meta enviará `hub.mode`, `hub.verify_token` y `hub.challenge`. Django debe comprobar que el modo sea `subscribe`, comparar el token y devolver `hub.challenge`. Si el token no coincide, debe responder `403`.

### Recepción POST

El endpoint debe:

1. Validar el JSON recibido.
2. Extraer el número del remitente, el ID del mensaje y el texto.
3. Ignorar eventos que no sean mensajes de texto.
4. Detectar mensajes duplicados.
5. Crear o recuperar la conversación.
6. Procesar la pregunta.
7. Enviar la respuesta a Meta.
8. Responder rápidamente con HTTP `200`.

La URL será similar a:

```text
https://tuchatbot.duckdns.org/whatsapp/webhook/
```

## 7. Persistencia de contactos y conversaciones

La web actual usa `Conversation.user`, pero WhatsApp identifica al usuario por su número telefónico. Se recomienda crear un modelo `WhatsAppContact` con:

- `phone_number`
- `display_name`
- Usuario web opcional
- Estado activo
- Fechas de creación y actualización

Las conversaciones de WhatsApp deben asociarse a ese contacto. También conviene crear un modelo `WhatsAppMessage` para guardar el `message_id` de Meta, el número, el texto, el estado y la fecha. Esto permite evitar respuestas duplicadas.

## 8. Fuente Predeterminada

Como WhatsApp no tiene el selector visual de la web, se debe definir una fuente inicial:

```env
WHATSAPP_DEFAULT_SOURCE=dummyjson
```

Así podrá responder preguntas de productos. Más adelante se pueden admitir comandos como:

```text
/fuente productos
```

No se debe ofrecer ni activar la fuente Blog mientras `BLOG_SOURCE_ENABLED=False`. No se deben mezclar datos del Blog y de productos.

## 9. Flujo Del RAG Vectorial En WhatsApp

El flujo para una pregunta de productos será:

```text
Mensaje de WhatsApp
  -> validar webhook y remitente
  -> detectar saludo, despedida o conversación cotidiana
  -> si es conversación cotidiana, responder directamente
  -> detectar ID, SKU o comparación
  -> si no es una consulta exacta, generar embedding de la pregunta
  -> buscar productos similares en PostgreSQL + pgvector
  -> validar similitud y ambigüedad
  -> recuperar precio, stock y SKU exactos
  -> generar respuesta determinista o redactada por Gemini/Groq
  -> guardar mensaje y resultado
  -> enviar respuesta a WhatsApp
```

Los productos ya deben estar indexados antes de recibir mensajes:

```text
DummyJSON
  -> manage.py index_dummyjson
  -> ProductDocument
  -> embedding de cada producto
  -> PostgreSQL + pgvector
```

Las búsquedas por ID, SKU y las comparaciones no deben depender de la similitud vectorial. Los precios, stocks, SKU e IDs deben seguir siendo datos deterministas.

Para mensajes amplios como `Estoy buscando algo para mi celular`, la conversación debe pedir aclaración si los resultados pertenecen a categorías distintas. El plan de esta capa está documentado en `natural.md`.

## 10. Reutilizar El Servicio Actual

El webhook debe llamar a:

```python
answer_message(conversation, text)
```

Para productos, el sistema seguirá usando el RAG vectorial de DummyJSON, verificando coincidencias, devolviendo precios y stock reales, comparando productos y rechazando productos ambiguos o inexistentes.

El servicio de Blog no se utilizará mientras `BLOG_SOURCE_ENABLED=False`, aunque su código se conserva para una futura reactivación.

## 11. Enviar Mensajes A WhatsApp

Crear un cliente que haga `POST` a:

```text
https://graph.facebook.com/{version}/{PHONE_NUMBER_ID}/messages
```

Con un cuerpo similar a:

```json
{
  "messaging_product": "whatsapp",
  "to": "51999999999",
  "type": "text",
  "text": {
    "body": "Respuesta del chatbot"
  }
}
```

El cliente debe controlar tokens inválidos, números no autorizados, errores de Meta, tiempos de espera, respuestas extensas y errores de conexión.

## 12. Seguridad

Implementar:

- Validación de `X-Hub-Signature-256`.
- Verificación del token del webhook.
- Control de mensajes repetidos mediante `message_id`.
- Límite de longitud del texto.
- Límite de mensajes por número.
- Bloqueo de usuarios abusivos.
- Logs sin tokens ni datos sensibles.
- Protección contra solicitudes falsas.

El webhook no debe depender de CSRF, porque Meta no utiliza el formulario web de Django. Debe protegerse mediante la firma oficial de Meta.

## 13. Procesamiento Y Tiempos De Respuesta

La primera versión puede ser síncrona, pero se deben configurar tiempos de espera para DummyJSON, PostgreSQL, embeddings, Gemini/Groq y Meta. El webhook debe responder HTTP `200` rápidamente; si la generación tarda demasiado, se debe pasar posteriormente a una cola con Celery y Redis.

Si el procesamiento supera los límites de Meta, se puede añadir posteriormente una cola con Celery y Redis.

## 14. Configuración Del Servidor Ubuntu

1. Instalar Python, PostgreSQL, Nginx y Gunicorn.
2. Copiar el proyecto y crear el entorno virtual.
3. Configurar PostgreSQL y el archivo `.env`.
4. Ejecutar las migraciones:

```bash
python3 manage.py migrate
```

5. Confirmar que PostgreSQL tenga instalada la extensión `vector`.
6. Indexar DummyJSON antes de activar el webhook:

```bash
python3 manage.py index_dummyjson
```

7. Ejecutar `collectstatic`.
8. Configurar Gunicorn como servicio `systemd`.
9. Configurar Nginx como proxy inverso.
10. Activar HTTPS con Let’s Encrypt.
11. Abrir los puertos `80` y `443` en el firewall.
12. Reiniciar el servicio después de cada despliegue.

## 15. Configurar El Webhook En Meta

1. Introducir la URL pública del webhook.
2. Introducir el `WHATSAPP_VERIFY_TOKEN`.
3. Presionar **Verify and Save**.
4. Suscribirse al evento `messages`.
5. Confirmar que Meta valide el endpoint.
6. Enviar un mensaje desde el número de prueba.
7. Revisar los logs del VPS.

## 16. Pruebas

Probar automáticamente:

- Verificación GET correcta e incorrecta.
- JSON inválido.
- Mensaje de texto válido.
- Mensajes sin texto.
- Eventos de imagen o audio.
- Mensajes duplicados.
- Número desconocido.
- Error de DummyJSON.
- Error de Gemini o Groq.
- Error al enviar a Meta.
- Persistencia de la conversación por número.
- Saludos y despedidas sin consultar DummyJSON.
- Búsquedas semánticas usando productos indexados.
- Consultas amplias que solicitan aclaración.
- Consultas por ID y SKU sin usar similitud para los datos exactos.
- Comparaciones deterministas de precio y stock.
- Respuestas cuando el índice vectorial está vacío o desactualizado.

Pruebas funcionales recomendadas:

```text
¿Cuál es el precio del producto 1?
Compara los productos 1 y 2.
¿Cuál tiene mayor stock entre los productos 1, 2 y 3?
Dame el SKU del Apple iPhone Charger.
Busca un producto que no existe.
```

## 17. Orden De Implementación

1. Preparar DuckDNS y HTTPS.
2. Preparar Nginx, Gunicorn y PostgreSQL en el VPS.
3. Confirmar que `pgvector` esté instalado y que existan los productos indexados.
4. Crear la app de WhatsApp.
5. Añadir la configuración mediante `.env`.
6. Implementar la verificación GET.
7. Implementar la recepción POST.
8. Añadir contactos y mensajes de WhatsApp.
9. Implementar la detección de conversación natural.
10. Reutilizar `answer_message()` y el RAG vectorial existente.
11. Implementar el envío de respuestas a Meta.
12. Añadir seguridad y control de duplicados.
13. Crear pruebas automatizadas.
14. Configurar Meta Cloud API.
15. Probar con el número de prueba.
16. Pasar a producción con el número real.

## Resultado esperado

El usuario podrá escribir por WhatsApp:

```text
Compara los productos 1, 2 y 3 e indica cuál es más barato y cuál tiene mayor stock.
```

El chatbot responderá utilizando el RAG vectorial y los datos reales de DummyJSON, conservará la conversación asociada al número telefónico y no expondrá la fuente Blog mientras permanezca deshabilitada.
