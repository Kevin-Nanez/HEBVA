# Diccionario de datos

Todos los archivos son CSV UTF-8 con encabezado, separador coma y punto decimal. Fechas en ISO 8601 (`AAAA-MM-DD`; con hora `AAAA-MM-DD HH:MM`, hora local de Monterrey). Montos en pesos mexicanos (MXN). Los datos son 100 % sintéticos.

Periodo de ventas: 2026-06-01 a 2026-08-31. Corte de inventario: 2026-09-01.

## tiendas.csv (4 filas)

| Columna | Tipo | Unidad / valores | Ejemplo | Descripción |
|---|---|---|---|---|
| tienda_id | texto | `T01`…`T04` | `T01` | Identificador de la tienda. Llave para unir con ventas, inventario y tickets. |
| nombre | texto | — | `Tienda Valle Norte` | Nombre ficticio de la tienda. |
| ciudad | texto | — | `Monterrey` | Ciudad. |
| estado | texto | — | `Nuevo León` | Estado de la República. |
| formato | texto | `Supermercado`, `Mi Tienda` | `Supermercado` | Formato comercial. Solo los Supermercados tienen canal Ecommerce. |
| m2 | entero | metros cuadrados de piso de venta | `4200` | Superficie. |
| fecha_apertura | fecha | AAAA-MM-DD | `2014-03-15` | Fecha de apertura. |

## catalogo_productos.csv (121 filas)

| Columna | Tipo | Unidad / valores | Ejemplo | Descripción |
|---|---|---|---|---|
| sku | texto | `SKU-NNNN` | `SKU-1001` | Identificador del producto. Llave con ventas e inventario. |
| nombre | texto | — | `Leche entera 1 L` | Nombre comercial. Puede cambiar si el SKU se sustituye. |
| categoria | texto | `Lácteos`, `Panadería`, `Carnes`, `Frutas y verduras`, `Abarrotes`, `Bebidas`, `Limpieza`, `Cuidado personal` | `Lácteos` | Categoría comercial. |
| subcategoria | texto | — | `Leche` | Subcategoría. |
| perecedero | booleano | `True` / `False` | `True` | Si requiere control de caducidad. |
| vida_util_dias | entero | días | `12` | Vida útil típica desde recepción. |
| precio_lista_mxn | decimal | MXN por unidad de venta (pieza, kg o paquete según el nombre) | `27.5` | Precio de lista sin descuento. |
| costo_mxn | decimal | MXN | `20.85` | Costo de adquisición. |
| proveedor_id | texto | `PRV-NN` | `PRV-02` | Proveedor ficticio. |
| estatus | texto | `Activo`, `Descontinuado` | `Descontinuado` | Un SKU descontinuado ya no vende ni tiene inventario. |
| fecha_alta | fecha | AAAA-MM-DD | `2024-01-15` | Alta en catálogo. |
| fecha_baja | fecha | AAAA-MM-DD o vacío | vacío | Último día en que vendió, si está descontinuado. |
| sku_sustituto | texto | `SKU-NNNN` o vacío | vacío | SKU que lo reemplaza, si aplica. |

## ventas.csv (41,859 filas)

Una fila por día, tienda, SKU y canal con venta distinta de cero. Los días sin venta no aparecen. Las devoluciones se registran como filas con unidades negativas.

| Columna | Tipo | Unidad / valores | Ejemplo | Descripción |
|---|---|---|---|---|
| fecha | fecha | AAAA-MM-DD | `2026-06-01` | Día de la venta. |
| tienda_id | texto | `T01`…`T04` | `T01` | Tienda. |
| sku | texto | `SKU-NNNN` | `SKU-1001` | Producto. |
| unidades | entero | piezas/kg según el producto; negativo = devolución | `52` | Unidades vendidas (o devueltas). |
| venta_neta_mxn | decimal | MXN; negativo = devolución | `1430.0` | Importe cobrado después de descuento (unidades × precio − descuento). |
| descuento_mxn | decimal | MXN | `0.0` | Descuento aplicado en la fila. |
| canal | texto | `Piso`, `Ecommerce` | `Piso` | Canal de venta. |

## inventario.csv (480 filas)

Snapshot al 2026-09-01 por tienda y SKU activo.

| Columna | Tipo | Unidad / valores | Ejemplo | Descripción |
|---|---|---|---|---|
| tienda_id | texto | `T01`…`T04` | `T01` | Tienda. |
| sku | texto | `SKU-NNNN` | `SKU-1002` | Producto. |
| existencia | entero | unidades | `450` | Existencia física al corte. |
| punto_reorden | entero | unidades | `125` | Nivel al que debería dispararse un pedido. |
| fecha_ultimo_conteo | fecha | AAAA-MM-DD | `2026-08-26` | Último conteo físico. |
| fecha_caducidad_lote_proximo | fecha | AAAA-MM-DD o vacío | `2026-09-12` | Caducidad del lote más próximo. Solo perecederos; vacío en los demás. |

## tickets_mesa_servicio.csv (285 filas)

Tickets levantados por las tiendas a la mesa de servicio. El texto es libre, en español coloquial, con errores de dedo intencionales.

| Columna | Tipo | Unidad / valores | Ejemplo | Descripción |
|---|---|---|---|---|
| ticket_id | entero | — | `1001` | Folio único, secuencial por fecha de creación. |
| fecha_creacion | fecha y hora | AAAA-MM-DD HH:MM | `2026-06-02 10:15` | Cuándo se levantó. |
| tienda_id | texto | `T01`…`T04` | `T01` | Tienda que reporta. |
| categoria | texto | `TI`, `Mantenimiento`, `RH`, `Inventario`, `Cajas` | `Inventario` | Área que atiende. |
| prioridad | texto | `Baja`, `Media`, `Alta`, `Crítica` | `Media` | Prioridad asignada. |
| estado | texto | `Abierto`, `En proceso`, `Resuelto`, `Cerrado` | `Resuelto` | Estado al corte. |
| titulo | texto | — | `Merma alta en frutas` | Resumen corto. |
| descripcion | texto | — | (texto libre) | Descripción del reporte. |
| fecha_cierre | fecha y hora | AAAA-MM-DD HH:MM o vacío | vacío si sigue abierto | Cuándo se resolvió o cerró. |

## Relaciones

- `ventas.tienda_id`, `inventario.tienda_id`, `tickets_mesa_servicio.tienda_id` → `tiendas.tienda_id`
- `ventas.sku`, `inventario.sku` → `catalogo_productos.sku`
- `catalogo_productos.sku_sustituto` → `catalogo_productos.sku`
