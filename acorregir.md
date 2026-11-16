# A corregir en el módulo `chat`

Revisión del 2026-11-16. Estado base: `manage.py check` limpio, sin migraciones pendientes y 143 pruebas en verde (`applications.chat` y `whatsapp.test_operations`). Son problemas de funcionamiento, no roturas.

## Hecho

- **Marca y tipo del catálogo:** la hoja ya trae la marca real (56 marcas; los accesorios y packs sin marca quedan vacíos) y 17 categorías en `Tipo`. Cargado con `sync_inventory`: 659 productos activos, con stock.
- **Catálogo con un solo comando:** `sync_inventory` carga la hoja en la base de datos (`--dry-run` solo muestra la vista previa). Se eliminó la huella de confirmación y el comando `inventory_status`.
- **Documentación:** `README.md`, `CLAUDE.md`, `comofunciona.md` y `docs/` actualizados al flujo actual.

## Importantes

1. **`applications/chat/views.py:110` traga todos los errores sin registrarlos.**
   - `except Exception` devuelve el 503 genérico sin `logger.exception`. Si falla la base de datos o hay un bug, no queda rastro.
   - Solución: añadir una línea de log.

2. **La búsqueda por categoría distingue tildes.**
   - `inventory.py::category_products` usa `category__icontains`, y en PostgreSQL eso distingue tildes: `categoría: sérum` encuentra «Sérum y ampolla», pero `categoría: serum` no. Pasa igual con `tonico` y «Tónico y bruma».
   - Solución: comparar sin tildes, normalizando como ya hace `search_terms`.

## Medios

3. **Resto del dominio anterior (válvulas y tuberías).**
   - `services.py:76`: el prompt de visión dice «inventario industrial».
   - `inventory.py`: sinónimos `válvula`, `codo`, `acople` y `llave de paso → valvula`.
   - `services.py:138`: `modifiers` con `bronce`, `npt`, `bsp`…
   - Para un catálogo de belleza no aportan y pueden confundir. Solución: prompt de cosmética y quitar los sinónimos.

4. **La búsqueda recorre los 659 productos en cada pregunta.**
   - `inventory.py::search_products` carga todos los productos y aplica `search_terms` (muchas regex) a cada uno, cada vez. Escala mal.
   - El `score` solo indica «coincide o no»; no hay ranking por relevancia (ordena por nombre).

5. **Siempre se llama a la IA, aunque no haga falta.**
   - Con un solo producto la IA solo elige qué campos mostrar, y se le envía el producto entero (incluida la descripción larga). Gasta cupo diario y añade latencia.
   - Opcional: resolver sin IA preguntas simples («¿cuántas unidades hay de X?») con campos por defecto.

6. **Los listados se cortan en 5 sin avisar.** `MAX_PRODUCTS_IN_CONTEXT` recorta la lista y el cliente no sabe que hay más (ya figura en «límites conocidos» de `comofunciona.md`).

## Menores

7. `services.py:32` usa `hasattr(history.model, 'role')` para distinguir chat web de WhatsApp: funciona, pero es frágil.
8. `providers.py`: el chequeo de la API key está duplicado y hay una rama `return handler(prompt)` que nunca se ejecuta (solo existe OpenRouter).
9. `views.py:63-65`: los mensajes vacíos o demasiado largos devuelven 400 sin pasar por el límite de frecuencia, así que se pueden enviar sin tope.
10. `views.py:23`: las conversaciones anónimas se limitan a las últimas 20 de la sesión; las anteriores dejan de ser accesibles sin avisar.

## Orden sugerido

1. Puntos 1 y 2 (log del error y categorías sin tildes): rápidos y de bajo riesgo.
2. Punto 3 (limpiar el dominio anterior).
3. Puntos 4 a 6 según prioridad.
