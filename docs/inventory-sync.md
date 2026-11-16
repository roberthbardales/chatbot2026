# Actualización segura del inventario

## Cambio importante del comando

`sync_inventory` **ya no elimina y recrea el catálogo ni aplica cambios por defecto**.
Sin argumentos descarga los CSV, valida y muestra una vista previa sin escribir
productos, bloqueos ni historial. No se ejecutó contra los CSV reales
durante esta implementación.

Desde la raíz, con el intérprete del proyecto:

```powershell
.\venv\Scripts\python.exe manage.py sync_inventory
```

Para revisar todos los valores anteriores/nuevos, sin el truncamiento de la
salida humana resumida:

```powershell
.\venv\Scripts\python.exe manage.py sync_inventory --json
```

El resultado indica altas, campos que cambian, productos ausentes que se
desactivarían, cambios explícitos de activo a inactivo, filas sin cambios y una
huella de revisión. Solo después de revisarlo, aplica con esa huella:

```powershell
.\venv\Scripts\python.exe manage.py sync_inventory --apply --confirm HUELLA
```

Este último comando **sí modifica el catálogo**. Vuelve a descargar y validar,
bloquea el estado de sincronización y las filas actuales y recalcula la huella.
Si cambian el CSV, los datos locales o la configuración de protección desde la
vista previa, rechaza la aplicación y exige revisar otra vez. La huella incluye
las fuentes mediante hash; no se imprimen sus URLs ni credenciales.

La huella es una confirmación de datos revisados, no un mecanismo de autorización
ni un secreto. El comando requiere acceso al entorno y a PostgreSQL. No existe
un endpoint público para aplicar el catálogo y no se agregó un botón de
sincronización automática al panel.

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
- No se tocan `updated_at` ni otros datos de filas sin cambios. Las huellas de
  contenido antiguas pueden actualizarse la primera vez.

Esto conserva IDs a partir de ahora; no recupera identificadores perdidos por
sincronizaciones antiguas que borraron/recrearon filas.

## Protección de bajas masivas

Se calcula qué proporción de los artículos antes activos quedarían inactivos,
tanto por ausencia como por `activo=0`.
Los artículos nuevos no ocultan una retirada masiva de los anteriores.

Por defecto, un catálogo con al menos 10 activos que perdería el 25 % o más se
marca como baja masiva. No basta con proporcionar la huella; exige también:

```powershell
.\venv\Scripts\python.exe manage.py sync_inventory --apply --confirm HUELLA --allow-large-drop
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
Esos cambios se muestran antes de confirmar; revisa especialmente precios y
stock cuando cambien cabeceras de la hoja.

Los precios del inventario activo permanecen `Decimal` al consultar
y redactar respuestas. En JSON se representan como cadenas exactas, por ejemplo
`"27.35"`, no como números de coma flotante. Las rutas heredadas de DummyJSON no
forman parte de este cambio.

## Historial, vigencia y diagnóstico local

Los intentos de aplicación con huella válida se registran en `InventorySyncRun`:
inicio, fin, estado `running`/`applied`/`failed`, huella, resumen y error. Los
cambios del catálogo y el registro aplicado se confirman en una transacción.
Si falla una escritura, no se guardan cambios parciales y se registra el fallo
fuera de esa transacción. Dos aplicaciones simultáneas coordinadas por el
servicio se serializan y vuelven a validar el catálogo.

Una vista previa no crea historial. Un fallo no borra la fecha de la última
aplicación correcta. Tras una interrupción brusca puede quedar un intento
`running`; no se considera éxito y debe revisarse. No se borra automáticamente
el historial ni se restauran instantáneas anteriores.

Consulta de solo lectura, **sin descargar CSV**:

```powershell
.\venv\Scripts\python.exe manage.py inventory_status
```

El panel operativo muestra activos/totales, última aplicación, último intento y
vigencia. Sin historial, la vigencia es desconocida; no se inventa una fecha a
partir de los productos existentes. Se avisa de catálogo vacío, sin confirmación
reciente, desactualizado o con último intento fallido. No se inicia una
sincronización desde esa consulta ni se detiene automáticamente el chat por edad.

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
define una política explícita de revisión/aceptación; no reutilices una huella
vieja para saltarte cambios de datos.

## Activación pendiente

Se añadió `chat/0012_inventory_sync_tracking`. Las migraciones de la base habitual
y la activación del worker siguen reservadas para el final por indicación del
usuario. La migración solo crea tablas de control/historial, no sincroniza datos.
Durante esta implementación no se descargó ni aplicó el catálogo real, no se
arrancó el worker y no se enviaron mensajes reales.

## Verificación

La suite completa pasó 291 pruebas, incluidas 25 nuevas de inventario. Cubren
vistas previas sin escrituras, identidad estable, reactivación de bajas lógicas,
validación de CSV/precios/stock, bloqueos de bajas masivas, confirmaciones
obsoletas, rollback y aplicaciones simultáneas con conexiones independientes.
Las comprobaciones de Django y migraciones, JavaScript y `git diff --check`
finalizaron sin problemas. No se descargó ni aplicó el catálogo real.
