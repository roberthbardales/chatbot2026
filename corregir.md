# Estado Y Correcciones Pendientes

## Estado Actual

DummyJSON funciona correctamente:

- Se indexaron 194 productos.
- Gemini genera los embeddings.
- PostgreSQL con pgvector realiza la búsqueda semántica.
- OpenRouter genera respuestas naturales usando únicamente el contexto recuperado.
- Google Sheets y DummyJSON están disponibles en el selector del chat web.
- WhatsApp usa DummyJSON por defecto.
- La suite completa pasa: 49 tests OK.
- `manage.py check` y `manage.py migrate --check` pasan correctamente.

## Riesgo Pendiente

La sincronización de DummyJSON genera los embeddings uno por uno y tarda varios minutos.
Actualmente se ejecuta dentro del worker de WhatsApp de forma síncrona:

```text
1. Sincronizar DummyJSON.
2. Generar los embeddings.
3. Procesar mensajes entrantes.
4. Enviar respuestas pendientes.
```

Mientras se generan los embeddings:

- Los mensajes nuevos quedan almacenados como pendientes.
- Las respuestas de WhatsApp pueden retrasarse varios minutos.
- No se pierden mensajes porque permanecen en PostgreSQL.

## Corrección Recomendada

Separar la indexación de DummyJSON del worker de WhatsApp o generar embeddings por lotes.
Así WhatsApp podrá seguir procesando mensajes mientras se actualiza el índice vectorial.
