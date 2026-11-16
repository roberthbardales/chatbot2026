# Panel de atención de WhatsApp

El panel está disponible en `/whatsapp/` y en el enlace **WhatsApp** del menú.
Requiere iniciar sesión como superusuario, administrador (ocupación `0`) o
empleado (ocupación `2`). Los demás usuarios no pueden consultar sus API ni imágenes.

## Funciones

- Búsqueda por nombre o teléfono, con carga de más conversaciones.
- Historial inicial de 50 mensajes y botón para cargar los anteriores.
- Cambio entre IA y atención humana. El panel advierte que los envíos ya
  encolados no se cancelan al activar la atención humana.
- Respuesta manual de hasta 50000 caracteres, con protección CSRF y división
  automática de textos largos mediante la cola existente.
- Estados de procesamiento, partes aceptadas por Meta, pendientes y fallidas,
  intentos, último error y fecha del siguiente reintento.
- Resumen operativo, alertas y reactivación manual auditada de fallos locales,
  sin reenviar partes con aceptación conocida: `docs/whatsapp-operations.md`.
- Actualización cada 10 segundos mientras la pestaña está visible. No se
  desplaza automáticamente al final si el operador está leyendo más arriba.
- Imágenes bajo demanda: el botón **Ver imagen** consulta una ruta autenticada
  y acotada al contacto y mensaje correspondiente.

**Encolar no significa enviar.** El envío manual no llama a Meta; necesita el
worker independiente activo. El webhook solo guarda los mensajes. La aceptación de Meta
no confirma entrega ni lectura. El estado de configuración solo verifica la
presencia de credenciales, no realiza una prueba de conectividad.

## Imágenes y seguridad

El servidor descarga la imagen usando las credenciales de Meta; esas credenciales
no se envían al navegador. Solo se admiten JPEG, PNG y WebP válidos. Se valida
el límite de bytes y píxeles y se genera una vista JPEG de hasta 2000 × 2000
píxeles sin copiar metadatos. Se rechazan SVG, contenido inválido y archivos
demasiado grandes. Las respuestas llevan `no-store` y `nosniff`.

La imagen no se guarda de forma permanente. Una imagen antigua puede dejar de
estar disponible en Meta. Las vistas del historial y del panel tampoco deben
almacenarse en caché. Los textos se insertan en el navegador con `textContent`,
no como HTML, para evitar ejecutar contenido de mensajes o nombres de contactos.

## API

- `GET /whatsapp/api/conversations?q=...&offset=...`: búsqueda y páginas de 100
  conversaciones; devuelve `has_more` y `next_offset`. La paginación de contactos
  usa desplazamiento, por lo que cambios concurrentes pueden alterar las páginas;
  el panel deduplica por identificador.
- `GET /whatsapp/api/messages/<contact_id>?limit=50&before=...`: historial con
  cursor firmado, vinculado al contacto y ordenado por fecha e ID. Sin `limit`,
  mantiene el máximo anterior de los últimos 500 mensajes. Devuelve `has_more`
  y `next_before`; los mensajes siguen en orden cronológico.
- Cada mensaje añade `delivery_parts` y `media_url`; se mantienen los campos
  anteriores, incluido `sending`.
- `GET /whatsapp/api/messages/<contact_id>/<message_pk>/media`: vista protegida
  de la imagen, sin exponer enlaces de descarga privados de Meta.
- Los endpoints existentes de cambio de modo y envío manual mantienen POST y CSRF.

## Despliegue

No se añadieron dependencias ni migraciones para este panel. Reutiliza Django,
Pillow, la plantilla base y Tailwind por CDN. El JavaScript está en
`static/whatsapp/dashboard.js`; no hay compilación frontend.

Reinicia el servicio después de actualizar el código. En producción, publica los
estáticos con el procedimiento habitual (`manage.py collectstatic` si corresponde
a tu despliegue). Si aún faltan las migraciones de las correcciones anteriores,
aplícalas antes de usar la cola. No uses el worker real como prueba: puede
enviar mensajes pendientes. Ya no sincroniza el inventario. Consulta
`docs/whatsapp-worker.md` para supervisión y comprobaciones de salud.
