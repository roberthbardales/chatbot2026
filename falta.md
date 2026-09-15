# Pendientes Del Proyecto

## Estado Correcto

- `WHATSAPP_PHONE_NUMBER_ID`: configurado.
- `WHATSAPP_VERIFY_TOKEN`: configurado en `.env`.
- `WHATSAPP_ACCESS_TOKEN`: configurado en `.env`.
- `WHATSAPP_APP_SECRET`: configurado en `.env`.
- `ALLOWED_HOSTS`: configurado para localhost y dominios de ngrok.
- Django: ejecutándose en `127.0.0.1:8000`.
- Migración de WhatsApp: aplicada.
- Webhook Django: implementado en `/whatsapp/webhook/`.
- RAG de DummyJSON: disponible.
- Ngrok: solo necesario durante las pruebas locales y debe apuntar a `http://localhost:8000`.
- URL pública: temporal; cambia al reiniciar el túnel salvo que se use un dominio reservado.
- Verificación del webhook en Meta: correcta.
- Prueba automática del campo `messages`: correcta y responde `HTTP 200`.
- Mensajes reales recibidos desde el número personal: confirmado; el chatbot genera y envía respuestas correctamente.
- API de Meta: envío validado con `v26.0` y respuesta `HTTP 200`.
- VPS: no es necesario para esta fase.

## Pendientes Técnicos

- [ ] Mover el procesamiento de IA y el envío a WhatsApp fuera del webhook síncrono mediante una cola de tareas (por ejemplo, Celery o un worker equivalente) para evitar timeouts y reintentos bajo carga.
- [ ] Configurar permisos para que el usuario de PostgreSQL pueda crear la extensión `vector` en la base de pruebas, o crearla previamente con un administrador.
- [ ] Eliminar la base de pruebas existente `test_db_chatbot2026` si quedó incompleta y volver a ejecutar los tests.
- [ ] Ejecutar toda la suite de tests después de habilitar `pgvector` en la base de pruebas.
- [ ] Migrar de Python 3.10 a Python 3.11 o superior antes del `2026-10-04` por el aviso de `google.api_core`.
- [ ] Desactivar `DEBUG` y revisar `ALLOWED_HOSTS` antes de desplegar en un VPS.
- [ ] Confirmar que `.env` nunca haya sido incluido en el historial de Git y rotar cualquier secreto que haya sido expuesto.
- [ ] Reducir o desactivar los logs temporales de WhatsApp en producción.

## Completado: Variables De Entorno

Django carga correctamente estas variables desde `.env`:

```text
WHATSAPP_VERIFY_TOKEN
WHATSAPP_ACCESS_TOKEN
WHATSAPP_APP_SECRET
```

Estas variables ya están añadidas al archivo `.env`:

```env
WHATSAPP_VERIFY_TOKEN=un_token_creado_por_ti
WHATSAPP_ACCESS_TOKEN=token_de_meta
WHATSAPP_APP_SECRET=app_secret_de_meta
```

Estos valores son secretos y no deben compartirse, escribirse en el código, subirse a Git ni aparecer en logs.

## Completado: Reiniciar Django

Después de modificar `.env`, reiniciar Django:

```powershell
Ctrl + C
venv\Scripts\python.exe manage.py runserver
```

## Completado: Iniciar Ngrok Durante Desarrollo

Ngrok debe ejecutarse durante las pruebas locales y redirigir la URL pública hacia Django:

```text
https://URL-ACTUAL-DE-NGROK.ngrok-free.dev
```

## 1. Configurar El Webhook En Meta

1. Abre [Meta for Developers](https://developers.facebook.com/) e inicia sesión.
2. Entra en tu aplicación de Meta.
3. En el menú de la aplicación, entra en **WhatsApp > Configuration**.
4. Busca la sección **Callback URL** y pulsa **Edit**.
5. En **Callback URL**, pega exactamente:

   ```text
   https://URL-ACTUAL-DE-NGROK.ngrok-free.dev/whatsapp/webhook/
   ```

6. En **Verify Token**, escribe exactamente el valor de `WHATSAPP_VERIFY_TOKEN` que está en tu `.env`.
7. Pulsa **Verify and Save**.

Si la verificación es correcta, Meta guardará la URL. Si falla, comprueba que Django y ngrok sigan ejecutándose y que la URL termine en `/whatsapp/webhook/`.

## 2. Suscribirse A Los Mensajes

1. En la misma pantalla de configuración de WhatsApp, localiza **Webhook fields**.
2. Pulsa **Manage** o **Edit**.
3. Activa la suscripción `messages`.
4. Guarda los cambios.

La suscripción `messages` es necesaria para que Meta envíe los mensajes recibidos al webhook de Django.

## 3. Configurar El Destinatario De Prueba

1. Entra en **WhatsApp > API Setup**.
2. En **To**, agrega o selecciona tu número personal de WhatsApp como destinatario de prueba.
3. Si Meta solicita una autorización, confirma la invitación desde ese WhatsApp.
4. Usa el número de prueba de Meta para iniciar la conversación, o responde al mensaje inicial de prueba.

## 4. Probar El Chatbot

Envía uno de estos mensajes:

```text
¿Cuál es el precio y stock de Apple iPhone Charger?
```

O:

```text
Busco un cargador para mi iPhone
```

Debes comprobar dos cosas:

1. La terminal de Django muestra una solicitud `POST` a:

   ```text
   /whatsapp/webhook/
   ```

2. WhatsApp recibe la respuesta del chatbot.

La prueba automática **Test** de Meta no equivale a un mensaje real. Los eventos
de estado adicionales pueden generar varios `POST /whatsapp/webhook/` con
respuesta `HTTP 200`. Los mensajes reales ya fueron probados correctamente y
Meta aceptó la respuesta saliente.

## Si No Funciona

- Error de verificación en Meta: revisa la URL, el `WHATSAPP_VERIFY_TOKEN` y que Django esté activo.
- Error `DisallowedHost`: confirma que `.env` contiene `.ngrok-free.dev` en `ALLOWED_HOSTS` y reinicia Django.
- No llega ningún `POST`: verifica que el campo `messages` esté suscrito y que ngrok siga activo.
- Llega el `POST` pero no responde: revisa la terminal de Django y el `WHATSAPP_ACCESS_TOKEN`.
- Error de firma: confirma que `WHATSAPP_APP_SECRET` corresponde a la aplicación seleccionada en Meta.
- Si reinicias ngrok y cambia la URL, actualiza la Callback URL en Meta.
- Si la prueba automática responde `200` pero un mensaje personal no genera un `POST`, revisa en **WhatsApp > API Setup** que tu número personal esté agregado y autorizado como destinatario de prueba. El número debe estar agregado en **To** y debes escribir al número de prueba de Meta, no al revés.

## Orden Exacto

```text
1. Iniciar Django y ngrok.
2. Configurar en Meta la Callback URL actual indicada por ngrok.
3. Usar el mismo `WHATSAPP_VERIFY_TOKEN`.
4. Pulsar **Verify and Save**.
5. Suscribirse al evento `messages`.
6. Configurar el destinatario de prueba en **WhatsApp > API Setup**.
7. Enviar un mensaje real desde el número personal autorizado al número de prueba de Meta.
8. Confirmar un nuevo `POST` en Django con estado `200`.
9. Confirmar el registro del mensaje con estado `processed` y la respuesta en WhatsApp.
```
