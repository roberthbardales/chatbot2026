# Actualización segura del inventario

## El comando

Hay un único comando de catálogo. Desde la raíz, con el intérprete del proyecto:

```powershell
.\venv\Scripts\python.exe manage.py sync_inventory
```

**Sí modifica el catálogo.** Descarga la hoja, la valida, bloquea el estado de
sincronización y las filas actuales y aplica los cambios en una transacción: o se
aplica todo o nada. No borra filas (los ausentes se desactivan) y no necesita
confirmación; la protección contra bajas masivas (más abajo) sigue activa. Al
terminar muestra cuántos productos hay en el catálogo.

Si solo quieres ver qué cambiaría, sin escribir productos, bloqueos ni historial:

```powershell
.\venv\Scripts\python.exe manage.py sync_inventory --dry-run
```

Para revisar todos los valores anteriores/nuevos, sin el truncamiento de la
salida resumida, añade `--json` (con o sin `--dry-run`).

El resultado indica altas, campos que cambian, productos ausentes que se
desactivarían, cambios explícitos de activo a inactivo y filas sin cambios. El
comando requiere acceso al entorno y a PostgreSQL. No existe
un endpoint público para aplicar el catálogo ni un botón de
sincronización: solo se ejecuta con el comando.

## Identificadores y bajas lógicas

- Los productos se identifican por SKU normalizado con NFKC, espacios extremos
  eliminados y comparación sin distinguir mayúsculas. No se eliminan guiones,
  barras o puntos para intentar fusionar modelos distintos.
- Se conserva la fila y su ID. Para productos existentes se conserva la caja
  habitual y se normaliza su representación Unicode/espacios extremos cuando
  corresponde. Los cambios de nombre no crean otro producto si el SKU coincide.
- Los ausentes quedan `active=False`; si regresan con el mismo SKU recuperan
  el mismo ID. Cambiar realmente el SKU implica alta del nuevo y baja lógica del
  anterior; no se adivina que son el mismo artículo.
- No se tocan `updated_at` ni otros datos de filas sin cambios. Los hashes de
  contenido antiguos pueden actualizarse la primera vez.

Esto conserva IDs a partir de ahora; no recupera identificadores perdidos por
sincronizaciones antiguas que borraron/recrearon filas.

## Protección de bajas masivas

Se calcula qué proporción de los artículos antes activos quedarían inactivos,
tanto por ausencia como por `activo=0`.
Los artículos nuevos no ocultan una retirada masiva de los anteriores.

Por defecto, un catálogo con al menos 10 activos que perdería el 25 % o más se
marca como baja masiva y el comando se detiene. Para aceptarla, ejecútalo con
`--allow-large-drop`:

```powershell
.\venv\Scripts\python.exe manage.py sync_inventory --allow-large-drop
```

No uses esa opción para eludir un CSV incompleto sin investigarlo. Nunca permite
importar productos vacíos, referencias inválidas, precios incorrectos o un CSV
distinto del revisado. Un CSV de productos sin filas válidas siempre se rechaza.

## Columnas de la hoja (Excel subido a Google Sheets)

La única fuente es `GOOGLE_SHEET_PRODUCTS_CSV_URL` (hoja publicada como CSV). Se
reconocen, sin distinguir mayúsculas ni tildes, estos nombres de columna:

- SKU: `sku`, `codigo`, `codigo_producto` o `id_producto`. Nombre: `nombre`, `producto`, `name` o `title`.
- `marca`, `tipo` o `categoria`, `modelo`, `precio`, `stock`, `unidad`, `activo`.
- `disponible`: si no hay columna `stock`, «No» se guarda como stock 0 y «Sí» deja el stock sin cifra pero
  **marca el producto como disponible** (`Product.available`); el bot responde «está disponible; consulta la
  cantidad exacta» en vez de «no tengo el stock». Valores aceptados: Sí/Si/Yes/Disponible y No/Agotado
  (sin distinguir tildes ni mayúsculas); vacío = sin dato; cualquier otro valor rechaza la sincronización.
- Si la hoja trae `stock`, la cantidad manda sobre `disponible`: cantidad > 0 es «disponible» y 0 es «agotado»,
  aunque `disponible` diga otra cosa.
- `precio_anterior` y `url`: si no hay `descripcion`, se guardan como texto en la descripción.
- Las demás columnas (imágenes, estado de imagen...) se ignoran.

Ya no hay combos: el modelo `Combo` no existe y la sincronización no los lee.

## Validación y dinero exacto

Se rechazan cabeceras vacías/duplicadas, columnas sobrantes sin cabecera,
cabeceras requeridas ausentes, alias contradictorios, SKU/nombres obligatorios
vacíos, identificadores demasiado largos, duplicados normalizados y banderas
de activo desconocidas. No se truncan silenciosamente SKU o nombres.

El precio se convierte directamente a `Decimal` finito, no negativo, con máximo
dos decimales y dentro del rango del modelo. No se redondean silenciosamente
precios como `1.005`. Un valor vacío significa precio desconocido, no cero.
El stock debe ser un entero no negativo dentro del rango de PostgreSQL; no se
trunca `2.5` a `2`. Se admite punto o coma decimal, no se interpretan separadores
de miles ni se infiere una moneda.

Cada fila importada es la representación completa del producto, no un
parche parcial: un campo opcional ausente/vacío puede pasar a vacío o desconocido.
Esos cambios se ven antes de aplicar con `--dry-run`; revisa especialmente precios y
stock cuando cambien cabeceras de la hoja.

Los precios del inventario activo permanecen `Decimal` al consultar
y redactar respuestas. En JSON se representan como cadenas exactas, por ejemplo
`"27.35"`, no como números de coma flotante. Las rutas heredadas de DummyJSON no
forman parte de este cambio.

## Historial, vigencia y diagnóstico local

Los intentos de aplicación se registran en `InventorySyncRun`:
inicio, fin, estado `running`/`applied`/`failed`, resumen y error. Los
cambios del catálogo y el registro aplicado se confirman en una transacción.
Si falla una escritura, no se guardan cambios parciales y se registra el fallo
fuera de esa transacción. Dos aplicaciones simultáneas coordinadas por el
servicio se serializan y vuelven a validar el catálogo.

Una vista previa no crea historial. Un fallo no borra la fecha de la última
aplicación correcta. Tras una interrupción brusca puede quedar un intento
`running`; no se considera éxito y debe revisarse. No se borra automáticamente
el historial ni se restauran instantáneas anteriores.

El estado (activos/totales, última aplicación y vigencia) se imprime al final de
cada ejecución de `sync_inventory` y se calcula sin descargar CSV.

`inventory_status()` calcula activos/totales, última aplicación, último intento y
vigencia. Sin historial, la vigencia es desconocida; no se inventa una fecha a
partir de los productos existentes. Se avisa de catálogo vacío, sin confirmación
reciente, desactualizado o con último intento fallido. No se inicia una
sincronización desde esa consulta ni se detiene automáticamente el bot por edad.

## Configuración del `.env` raíz

| Variable | Predeterminado | Uso |
| --- | --- | --- |
| `INVENTORY_MAX_CSV_BYTES` | 5242880 | Tamaño máximo por descarga, 5 MiB |
| `INVENTORY_MAX_ROWS` | 20000 | Filas máximas por CSV |
| `INVENTORY_MAX_DEACTIVATE_PERCENT` | 25 | Umbral de bajas, limitado entre 1 y 100 |
| `INVENTORY_MIN_CATALOG_FOR_DROP_GUARD` | 10 | Activos mínimos para la protección adicional |
| `INVENTORY_STALE_HOURS` | 24 | Antigüedad de la última aplicación que genera aviso |

La sincronización sigue separada del worker. No se añadió aplicación automática
periódica ni se activó ninguna tarea del sistema. Si necesitas automatizarla,
define una política explícita de revisión/aceptación y mantén la protección de
bajas masivas.

## Activación

Las tablas de control (`InventorySyncLock`, `InventorySyncRun`) están en `core/0001_initial`. Tras `python manage.py migrate`, ejecuta `sync_inventory --dry-run` para revisar y `sync_inventory` para cargar el catálogo.

## Verificación

Las pruebas de `applications/core/tests/test_inventory_sync.py` cubren vistas previas sin escrituras, identidad estable, reactivación de bajas lógicas, validación de CSV/precios/stock, bloqueos de bajas masivas, rollback y aplicaciones simultáneas con conexiones independientes. Ninguna descarga el catálogo real.
