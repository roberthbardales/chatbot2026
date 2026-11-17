# A corregir en el módulo `chat`

Revisión del 2026-11-16. Estado base: `manage.py check` limpio, sin migraciones pendientes y 124 pruebas de `applications.chat` en verde. Son problemas de funcionamiento, no roturas.

## Hecho

- **Marca y tipo del catálogo:** la hoja ya trae la marca real (56 marcas; los accesorios y packs sin marca quedan vacíos) y 17 categorías en `Tipo`. Cargado con `sync_inventory`: 659 productos activos, con stock.
- **Catálogo con un solo comando:** `sync_inventory` carga la hoja en la base de datos (`--dry-run` solo muestra la vista previa). Se eliminó la huella de confirmación y el comando `inventory_status`.
- **Log de errores en el chat** (`views.py`): el `except Exception` ahora registra el traceback con `logger.exception` y el usuario sigue viendo el mismo mensaje genérico (503).
- **Categorías sin tildes** (`inventory.py::category_products`): `categoría: serum` y `sérum` encuentran lo mismo, sin distinguir mayúsculas.
- **Documentación:** `README.md`, `CLAUDE.md`, `comofunciona.md` y `docs/` actualizados al flujo actual.

## Medios

1. **Resto del dominio anterior (válvulas y tuberías).**
   - `services.py:76`: el prompt de visión dice «inventario industrial».
   - `inventory.py`: sinónimos `válvula`, `codo`, `acople` y `llave de paso → valvula`.
   - `services.py:138`: `modifiers` con `bronce`, `npt`, `bsp`…
   - Para un catálogo de belleza no aportan y pueden confundir. Solución: prompt de cosmética y quitar los sinónimos.

2. **La búsqueda recorre los 659 productos en cada pregunta.**
   - `inventory.py::search_products` carga todos los productos y aplica `search_terms` (muchas regex) a cada uno, cada vez. Escala mal.
   - El `score` solo indica «coincide o no»; no hay ranking por relevancia (ordena por nombre).

3. **Siempre se llama a la IA, aunque no haga falta.**
   - Con un solo producto la IA solo elige qué campos mostrar, y se le envía el producto entero (incluida la descripción larga). Gasta cupo diario y añade latencia.
   - Opcional: resolver sin IA preguntas simples («¿cuántas unidades hay de X?») con campos por defecto.

4. **Los listados se cortan en 5 sin avisar.** `MAX_PRODUCTS_IN_CONTEXT` recorta la lista y el cliente no sabe que hay más (ya figura en «límites conocidos» de `comofunciona.md`).

## Menores

5. `services.py:32` usa `hasattr(history.model, 'role')` para distinguir chat web de WhatsApp: funciona, pero es frágil.
6. `providers.py`: el chequeo de la API key está duplicado y hay una rama `return handler(prompt)` que nunca se ejecuta (solo existe OpenRouter).
7. `views.py:63-65`: los mensajes vacíos o demasiado largos devuelven 400 sin pasar por el límite de frecuencia, así que se pueden enviar sin tope.
8. `views.py:23`: las conversaciones anónimas se limitan a las últimas 20 de la sesión; las anteriores dejan de ser accesibles sin avisar.

## Orden sugerido

1. Punto 1 (limpiar el dominio anterior: válvulas, tuberías, «inventario industrial»).
2. Puntos 2 a 4 según prioridad (búsqueda, llamadas a la IA y listados cortados).
3. Menores (5 a 8) cuando haya tiempo.
