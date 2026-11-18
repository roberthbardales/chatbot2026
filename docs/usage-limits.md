# Límites contra abuso y control local de IA


Los límites se guardan en PostgreSQL y se coordinan con bloqueos de fila, no con
memoria local. Se comparten entre procesos web y workers. No requieren Redis ni
dependencias adicionales.

## Configuración del `.env` raíz

| Variable | Predeterminado | Función |
| --- | --- | --- |
| `CHAT_RATE_WINDOW_SECONDS` | 60 | Ventana de frecuencia, en segundos |
| `CHAT_MAX_CONCURRENT_PER_PRINCIPAL` | 1 | Consultas simultáneas por contacto de WhatsApp |
| `CHAT_REQUEST_LEASE_SECONDS` | 300 | Plazo mínimo de recuperación de consultas abandonadas |
| `WHATSAPP_RATE_PER_CONTACT` | 20 | Grupos de mensajes procesados por contacto y ventana |
| `AI_MAX_CONCURRENT_REQUESTS` | 4 | Llamadas simultáneas globales a OpenRouter |
| `AI_DAILY_REQUEST_LIMIT` | 1000 | Máximo diario de llamadas reservadas |
| `AI_DAILY_TOKEN_LIMIT` | 2000000 | Máximo diario de tokens reservados |
| `AI_MAX_OUTPUT_TOKENS` | 1024 | Máximo de salida enviado a OpenRouter como `max_tokens` |
| `AI_MAX_PROMPT_CHARACTERS` | 24000 | Máximo de caracteres de pregunta/contexto más instrucciones de sistema |
| `AI_IMAGE_TOKEN_RESERVE` | 10000 | Reserva adicional por imagen |

Los límites de frecuencia y los dos cupos diarios admiten `0` para desactivar ese
límite específico. La concurrencia se mantiene en al menos 1. El plazo de la
reserva de una consulta se amplía si hace falta para cubrir tres tiempos de
espera del proveedor y un margen de 60 segundos.

## Dónde se aplican

- **WhatsApp:** `WHATSAPP_RATE_PER_CONTACT` por contacto, una consulta simultánea por contacto (`principal_slot`) y el cupo global de IA. Una denegación aplaza el mensaje (ver más abajo); no se avisa al cliente.
- **Login y registro** (`users/services.py`): `AUTH_LOGIN_PER_EMAIL`, `AUTH_LOGIN_PER_IP` y `AUTH_REGISTER_PER_IP` sobre la ventana `AUTH_RATE_WINDOW_SECONDS`. Usan `enforce_rates`, `identity` y `client_address` de `core/limits.py`.

Las llamadas externas se ejecutan fuera de las transacciones que guardan datos.
Las reservas de consumo no desaparecen si falla la persistencia posterior de la
respuesta.

## IP y proxies

Por defecto la IP se obtiene exclusivamente de `REMOTE_ADDR` y se ignora
`X-Forwarded-For`, para no confiar en cabeceras que puede falsificar el cliente. Las IPv4 mapeadas
en IPv6 se normalizan a IPv4; las IPv6 se agrupan por prefijo /64 para que cambiar
direcciones de privacidad no eluda el contador.

Si hay Nginx, Cloudflare u otro proxy propio delante de Django, `REMOTE_ADDR` es la
IP del proxy y todos los clientes compartirían el contador de login y registro.
Para evitarlo, define en el `.env` cuántos proxies de confianza hay:

| Variable | Predeterminado | Función |
| --- | --- | --- |
| `TRUSTED_PROXY_COUNT` | 0 | Número de proxies propios delante de Django. 0 = ignorar `X-Forwarded-For`. |

Con `TRUSTED_PROXY_COUNT=n` se toma la entrada n-ésima de `X-Forwarded-For`
contando desde la **derecha** (las que añadieron tus proxies); lo que está a la
izquierda lo puede falsificar el cliente y se ignora. Ejemplos: Nginx solo → `1`;
Cloudflare + Nginx → `2`. Si la cabecera trae menos entradas o no son IP válidas,
se usa `REMOTE_ADDR`. Bloquea además el acceso directo al servidor que eluda el
proxy, porque en ese caso el cliente podría enviar su propia cabecera. WhatsApp no
usa la IP: solo afecta a login y registro.

Las identidades se almacenan como HMAC usando el secreto de Django, no como IP,
teléfono o correo en texto claro. Cambiar `SECRET_KEY` cambia estas identidades.

## Cupo de IA y dinero real

La protección global se aplica en `generate_response`, por lo que cubre las
respuestas de WhatsApp y el reconocimiento visual. Cada llamada reserva una solicitud y un número
conservador de tokens: bytes UTF-8 de texto/sistema, margen de estructura, salida
máxima y reserva configurable de imagen. Una consulta con imagen puede hacer
más de una llamada y consume más de una reserva.

**No es facturación real ni una garantía de gasto en dólares.** Los precios y la
tokenización dependen del modelo; las imágenes necesitan una reserva estimada.
No se consultan precios automáticamente ni se descuentan los tokens no usados.
Los fallos del proveedor también consumen la reserva, porque pueden haber
generado consumo remoto. El contador puede sobreestimar el uso y la reserva
visual puede necesitar ajuste según el modelo.

Para un límite monetario real, configura también el crédito/presupuesto de la
clave en OpenRouter y supervisa su facturación. El cupo local se reinicia cada
día UTC. El umbral configurado genera un aviso en logs una vez al día; no se
añadió envío de correo, SMS ni una plataforma externa de alertas.

## WhatsApp y recuperación

Los límites por contacto
cuentan grupos procesados, no cada mensaje agrupado. Un bloqueo temporal por
frecuencia, concurrencia o cupo devuelve los mensajes a `PENDING`, con
`retry_after_at`, conservando su texto y sin encolar un aviso al cliente.

La espera no consume los intentos de recuperación de mensajes abandonados. Los
mensajes se retoman cuando vuelve a ejecutarse el procesador después del plazo;
`force=True` omite el debounce, no los límites de uso. El siguiente intento
queda en `retry_after_at`. Un contexto demasiado grande no se reintenta para siempre:
se marca fallido y se encola el aviso controlado habitual.

Las reservas de concurrencia se liberan en `finally`. Tras una caída, vencen
automáticamente. Estos plazos son una protección operativa, no una garantía de
cancelación de una llamada remota que sobreviva a su plazo.

## Operación

Limpieza opcional y acotada a las tablas de control, para programar periódicamente:

```powershell
.\venv\Scripts\python.exe manage.py cleanup_chat_limits
```

Por defecto conserva contadores recientes durante 7 días y días de consumo
durante 90 días, además de los bloqueos activos. No borra conversaciones,
mensajes, inventario ni salidas de WhatsApp. Los valores se ajustan con
`--retention-days` y `--usage-days`. La limpieza no se ejecutó en la base habitual
durante esta implementación.

Estas protecciones no sustituyen un WAF, límites de conexiones/tamaño de cuerpo
en el proxy, CAPTCHA ni defensa frente a ataques distribuidos. Usuarios de una
misma red pueden compartir el límite de IP y el cupo diario global puede afectar
a todos cuando se agota; ajusta los valores a tu tráfico y presupuesto.

## Activación

Las tablas de control están en `core/0001_initial` y el aplazamiento por límites
en `whatsapp/0001_initial`. Detén los procesos anteriores, ejecuta desde la raíz
y reinicia los servicios:

```powershell
.\venv\Scripts\python.exe manage.py migrate
```
