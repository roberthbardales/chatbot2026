# Cómo probar el bot

Revisión: 2026-11-17. Las respuestas de los ejemplos son reales, tomadas del catálogo cargado ese día (659 productos). Si cambias la hoja, los precios, el stock y los nombres serán otros. El bot varía la frase de apertura y de cierre («¡Claro!», «Listo 😉», «¿Buscas otro producto? 🙌»…), así que el texto exacto no siempre coincide, pero los datos sí.

Hay tres niveles de prueba, de menos a más completo. Los dos primeros no necesitan Meta.

## Antes de empezar

```powershell
python manage.py migrate            # tablas al día
python manage.py sync_inventory     # catálogo cargado (--dry-run solo muestra los cambios)
```

Necesitas `OPENROUTER_API_KEY` en el `.env`: cada respuesta de un producto concreto usa la IA una vez y se suma a «IA hoy» del panel.

## Nivel 1: el motor, desde el shell

No usa Meta, ni el webhook, ni el worker. Prueba la búsqueda y las respuestas. Abre `python manage.py shell` y pega:

```python
from types import SimpleNamespace
from applications.core.services import answer_sheet_message


class Historial:
    """Guarda el contexto de la última respuesta, como haría el historial de un contacto."""
    def __init__(self):
        self.context = {}
    def filter(self, **kwargs):
        return self
    def order_by(self, *args):
        return self
    def first(self):
        return SimpleNamespace(inventory_context=self.context)


historial = Historial()
conversacion = SimpleNamespace(provider='openrouter', messages=historial)


def preguntar(texto):
    respuesta = answer_sheet_message(conversacion, texto)
    historial.context = respuesta.inventory_context
    print(respuesta)
```

Después escribe `preguntar('hola')`, `preguntar('tienes protector solar?')`, etc. Las preguntas seguidas se acuerdan de la lista y del producto elegido, igual que en WhatsApp. Para empezar de cero, vuelve a ejecutar `historial.context = {}`.

### Qué preguntar y qué responde

| Pregunta | Respuesta del bot |
| --- | --- |
| `hola` | «¡Hola! 👋 Soy el asistente de la tienda. Escríbeme el nombre del producto o su categoría y te digo precio y stock.» |
| `gracias` | «¡De nada! 😊 Si necesitas algo más, escríbeme el nombre del producto.» |
| `tienes protector solar?` | Lista numerada de 5 productos y «Hay 64 productos en total y te muestro 5. Si me dices la marca, el tipo o el tamaño, afino la búsqueda 😉» |
| `la 2` (tras la lista) | Los datos de la opción 2: «Beauty of Joseon Relief Sun…: está a 99.00 y está agotado.» |
| `y el stock?` | Repite los datos del mismo producto: el bot recuerda cuál elegiste |
| `precio del Beauty of Joseon Ginseng Essence Water` | «Beauty of Joseon Ginseng Essence Water: está a 99.00 y hay 30 unidades.» |
| `y la marca?` | «…: es de la marca Beauty of Joseon, está a 99.00 y hay 30 unidades.» |
| `de qué categoría es?` | «…es de la categoría Sérum y ampolla.» |
| `Medicube Zero Pore Pad 2.0 70 Pads` | «…: está a 119.00 y está agotado.» |
| `bloqueador solar` | La misma lista de protectores solares: el bot entiende `bloqueador` como `protector` |
| `tienes cleanser?` | Lista de 5 limpiadores y «Hay 89 productos en total y te muestro 5…» |
| `categoría: tónico y bruma` | Productos de esa categoría con precio y stock, y «Hay 19 productos en total…» |
| `tienes base mate?` | «No encontré ese producto en el inventario disponible. ¿Puedes indicar la marca o alguna característica adicional?» |

### Cosas que conviene saber

- **El bot no adivina.** Si hay varios candidatos, muestra una lista y espera que elijas con el número. Nunca elige por ti.
- **Solo ve los primeros 5.** Las listas se ordenan por nombre; para llegar a un producto que no está entre los primeros, afina con la marca o el tamaño («protector solar La Roche-Posay», «protector solar 50 ml»).
- **Todos los términos deben coincidir.** «Base mate» no encuentra nada si ningún producto tiene a la vez «base» y «mate». Prueba con palabras que aparezcan en el nombre.
- **Los SKU de tu hoja son identificadores largos** y el bot no los muestra. El cliente busca por nombre, marca o categoría.
- **Los precios y el stock salen del catálogo**, nunca de la IA. Si cambias la hoja y no ejecutas `sync_inventory`, el bot responde con los datos anteriores.
- **El bot entiende sinónimos:** `cleanser`/`cleansing` → limpiador, `cream` → crema, `toner` → tónico, `labial` → labios, `ampollas` → ampolla… (lista completa en `inventory.py`, `_SEARCH_SYNONYMS`).
- **Sin respuesta:** el bot ignora audios, stickers, ubicaciones y documentos (solo lee texto e imágenes).

## Nivel 2: el panel, sin Meta

Prueba las conversaciones, el modo humano y el envío manual con un contacto inventado. Crea uno desde `python manage.py shell`:

```python
from applications.whatsapp.models import WhatsAppContact, WhatsAppMessage
c = WhatsAppContact.objects.create(phone_number='51900000001', display_name='Cliente de prueba')
WhatsAppMessage.objects.create(contact=c, message_id='prueba-1', text='Hola, tienes protector solar?', status='processed')
```

Entra en `http://localhost:8000/whatsapp/` con un superusuario, administrador o empleado. En unos 5 segundos aparece el contacto. Qué esperar:

| Acción | Resultado |
| --- | --- |
| Abrir la conversación | El mensaje del cliente a la izquierda y la etiqueta «Atiende el bot» |
| **Atender yo** | Etiqueta «Atiende una persona», «Humano» en la lista y el aviso de que el bot no responderá |
| Escribir y enviar | Burbuja verde «Operador» con el estado «en cola» |
| **Devolver al bot** | Vuelve «Atiende el bot» y desaparece «Humano» |

El mensaje manual **no sale** mientras el worker no esté corriendo, y con un número inventado Meta lo rechazaría («falló»). Antes de la prueba real, borra el contacto de prueba:

```python
WhatsAppContact.objects.get(phone_number='51900000001').delete()
```

(también borra sus mensajes y envíos en cola).

## Nivel 3: con Meta, de punta a punta

Es lo único que envía WhatsApps reales. Necesitas tu número en la lista de destinatarios de la app de Meta (si usas el número de prueba), una URL pública con HTTPS y el worker.

1. **Servidor:** `python manage.py runserver 8000`.
2. **Túnel** (en otra terminal): `ngrok http 8000`. Copia la URL `https://…ngrok-free.dev`. Su dominio debe estar en `ALLOWED_HOSTS`.
3. **Meta Developers → WhatsApp → Configuración → Webhook:** URL de devolución `https://…/whatsapp/webhook/`, token de verificación igual a `WHATSAPP_VERIFY_TOKEN`, y suscripción al campo `messages`. `WHATSAPP_APP_SECRET` debe ser el de esa misma app.
4. **Worker** (otra terminal; **envía mensajes reales**): `python manage.py whatsapp_worker`. Comprueba con `python manage.py whatsapp_healthcheck` que dice `"healthy": true`.
5. **Escribe desde tu WhatsApp** al número de la app, por ejemplo `tienes protector solar?`. La respuesta tarda entre 8 y 10 segundos: el bot espera 6 s por si escribes varias líneas seguidas.
6. **Mira el panel:** tu conversación aparece con la respuesta del bot y su estado pasa de «en cola» a «enviado», «entregado» y «leído».

### Si algo falla

| Síntoma | Qué revisar |
| --- | --- |
| Meta no verifica el webhook | El token no coincide, o el servidor o el túnel están detenidos |
| El POST llega con 403 | `WHATSAPP_APP_SECRET` no es el de la app |
| Llega pero no responde | El worker no está corriendo (`whatsapp_healthcheck`) o la tarjeta «Colas» del panel muestra mensajes sin procesar |
| Responde «No encontré ese producto» | El catálogo no está cargado (`sync_inventory`) o la palabra no aparece en ningún producto |
| El envío aparece como «falló» | El motivo de Meta se muestra en el panel; lo más habitual es la ventana de 24 horas o un destinatario no permitido |
| La respuesta tarda mucho | La IA está ocupada o se alcanzó el límite; mira la tarjeta «IA hoy» |

## Ver también

[comofunciona.md](../comofunciona.md) (el flujo paso a paso) · [whatsapp-panel.md](whatsapp-panel.md) · [whatsapp-worker.md](whatsapp-worker.md) · [inventory-search.md](inventory-search.md)
