Estoy desarrollando un chatbot de WhatsApp con Django usando Meta WhatsApp Cloud API y ngrok.

Necesito que revises mi proyecto actual y soluciones un problema específico:

CONTEXTO

El webhook de Meta ya está correctamente configurado y verificado.

Callback URL:
https://wrongly-sugar-dimmed.ngrok-free.dev/whatsapp/webhook/

El campo "messages" está suscrito.

Meta sí está enviando correctamente los mensajes a Django.

Cuando envío "Hola" desde mi WhatsApp al número de prueba de Meta, Django recibe el POST correctamente y devuelve HTTP 200.

Ejemplo:

POST /whatsapp/webhook/ HTTP/1.1 200

El payload recibido contiene:

{
  "object": "whatsapp_business_account",
  "entry": [
    {
      "changes": [
        {
          "value": {
            "messaging_product": "whatsapp",
            "metadata": {
              "display_phone_number": "15552051741",
              "phone_number_id": "1348042578386824"
            },
            "contacts": [
              {
                "wa_id": "51960174778"
              }
            ],
            "messages": [
              {
                "from": "51960174778",
                "text": {
                  "body": "Hola"
                },
                "type": "text"
              }
            ]
          },
          "field": "messages"
        }
      ]
    }
  ]
}

PROBLEMA

El webhook recibe "Hola", pero el chatbot NO responde por WhatsApp.

Por lo tanto, NO necesito que vuelvas a configurar ngrok ni la verificación GET del webhook salvo que encuentres un error real relacionado.

Tu tarea es revisar el código existente del proyecto y encontrar por qué Django recibe el mensaje pero no envía la respuesta mediante WhatsApp Cloud API.

IMPORTANTE

Antes de modificar código:

1. Inspecciona la estructura del proyecto.
2. Localiza la URL /whatsapp/webhook/.
3. Localiza la view que procesa GET/POST del webhook.
4. Localiza la función o servicio encargado de enviar mensajes mediante WhatsApp Cloud API.
5. Revisa cómo se cargan las variables del .env.
6. Revisa los logs y manejo de excepciones existentes.
7. No reescribas todo el proyecto si no es necesario.

Comprueba especialmente que el código extraiga correctamente:

value = data["entry"][0]["changes"][0]["value"]
message = value["messages"][0]
numero = message["from"]
texto = message["text"]["body"]

Para este payload:

numero = "51960174778"
texto = "Hola"

Comprueba también que el envío utilice el endpoint correcto de Meta:

POST https://graph.facebook.com/v26.0/{PHONE_NUMBER_ID}/messages

El phone_number_id recibido por Meta actualmente es:

1348042578386824

El request de respuesta debería tener conceptualmente:

Headers:
Authorization: Bearer {WHATSAPP_TOKEN}
Content-Type: application/json

Body:
{
  "messaging_product": "whatsapp",
  "to": "51960174778",
  "type": "text",
  "text": {
    "body": "..."
  }
}

No hardcodees tokens ni secretos.

Usa las variables existentes del .env. Si los nombres actuales son distintos, adapta el código existente en vez de crear variables duplicadas innecesariamente.

SEGURIDAD

Nunca imprimas el WHATSAPP_TOKEN completo en logs.
No muestres secretos del .env.
No reemplaces secretos existentes por valores inventados.

DIAGNÓSTICO

Necesito que agregues logging temporal útil para ver:

- mensaje recibido
- número remitente
- URL/phone_number_id utilizado, sin secretos
- status_code devuelto por Meta
- response body devuelto por Meta

Ejemplo:

print("Mensaje recibido:", texto)
print("Número:", numero)
print("WhatsApp status:", response.status_code)
print("WhatsApp response:", response.text)

Si Meta devuelve 400, 401, 403 u otro error, analiza el response JSON y corrige la causa.

IMPORTANTE SOBRE WEBHOOKS

Meta también puede enviar eventos de estado que no contienen "messages".

El código NO debe fallar si recibe:

statuses
delivery updates
read updates
otros eventos del webhook

Debe comprobar primero que exista "messages" antes de intentar acceder a messages[0].

OBJETIVO FINAL

Quiero poder hacer:

Mi WhatsApp:
Hola

↓

Meta WhatsApp Cloud API

↓

ngrok

↓

Django /whatsapp/webhook/

↓

mi lógica/agente del chatbot

↓

WhatsApp Cloud API

↓

Mi WhatsApp recibe la respuesta del bot.

Haz los cambios mínimos necesarios.

Después de modificar el proyecto:

1. Indícame qué archivos modificaste.
2. Explica brevemente cuál era el problema.
3. Muéstrame cómo probarlo.
4. Dime exactamente qué debo observar en la terminal.
5. Si falta alguna variable de entorno, dime únicamente el NOMBRE de la variable que necesito configurar, nunca inventes su valor.