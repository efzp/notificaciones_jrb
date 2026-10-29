# Piloto ampliado a 100 correos

Ejecutado el 23 de octubre de 2026 sobre `JUNIO Y JULIO 2026.pst`, abierto en modo de solo lectura. No se creó base de datos.

## Resultados

- 100 correos únicos, incluidos los 20 originales con sus mismos identificadores.
- Asunto y cuerpo extraídos en los 100 correos. Todos los cuerpos disponibles estaban en HTML; se conserva el HTML devuelto por el lector y se genera texto legible sin ejecutar código ni cargar recursos externos.
- 246 adjuntos inventariados. Hay 54 adjuntos de método MAPI 5, mensajes incrustados, sin nombre de archivo disponible. Se identifican como nombre no disponible; no se inventan nombres.
- 53 reportes de entrega o relevo y 1 de no entrega.
- Ninguna confirmación técnica de lectura identificada en esta muestra.
- 46 correos sin señal técnica suficiente para identificar un acuse de lectura. Entre ellos se marcaron 3 posibles acuses manuales por expresiones de recibido en el texto inicial, pendientes de revisión.
- 14 candidatos presentaron errores del lector y fueron reemplazados durante la selección hasta completar 100 correos. Quedaron registrados, sin concluir que el archivo esté corrupto. El piloto no estima la frecuencia de errores en el PST completo.

## Entregables

`resultados/piloto_100_correos.xlsx` contiene:

- Correos: metadatos, asunto, nombres de adjuntos, vista breve del cuerpo, identificación del reporte, evidencia del criterio y posible acuse manual.
- Cuerpos: texto completo dividido en segmentos consecutivos por correo para facilitar la lectura. Filtrar por ID y ordenar por segmento. La división visual puede insertar saltos de línea dentro de una línea extensa.
- Adjuntos: una fila por adjunto, vinculada al correo.
- Participantes: datos de cabeceras, incluidos remitentes y destinatarios.
- Incidencias: candidatos que el lector no pudo procesar.

`resultados/correos.csv` y `correos.json` contienen el cuerpo completo en un campo por correo, sin el límite de la vista breve del Excel. Los CSV usan punto y coma y UTF-8 con BOM. Texto con apariencia de fórmula se escapa para Excel; JSON conserva los valores extraídos.

`resultados/cuerpos/` contiene los cuerpos individuales `.txt` y los HTML de origen `.html`. `cabeceras/` conserva las cabeceras devueltas por el lector. No se extrajo contenido binario de los adjuntos.

## Identificación de confirmaciones

La clase MAPI `REPORT.*.IPNRN` identifica un reporte de lectura. `IPNNRN` corresponde a no lectura, `DR` a entrega y `NDR` a no entrega, según [Microsoft](https://learn.microsoft.com/en-us/openspecs/exchange_server_protocols/ms-oxomsg/6f3751db-583b-4b31-b2ed-58fcc440e5b9).

Un reporte MIME de disposición requiere revisar la disposición concreta antes de atribuir lectura, conforme a [RFC 8098](https://www.rfc-editor.org/info/rfc8098/). Un prefijo de asunto como «Leído:» solo se marca como posible cuando no está respaldado por la señal técnica. Una solicitud de confirmación no se clasifica como acuse recibido.

Los posibles acuses manuales se marcan separadamente por expresiones explícitas, como «notifico recibido», en el texto inicial anterior a marcadores de respuesta reconocidos. Es una heurística limitada: no detecta todas las redacciones ni todos los formatos de citas. No equivale a una confirmación técnica ni demuestra lectura del contenido completo o de sus adjuntos.

«No identificada» indica ausencia de señales reconocidas por el método; no demuestra que el mensaje no haya sido leído. La clasificación tampoco certifica autenticidad o validez jurídica de los reportes.

## Selección y validación

La muestra es técnica, determinista y no estadística. Se alterna entre carpetas no vacías ordenadas por ruta, con posiciones equidistantes dentro de cada una, hasta completar 100 elementos legibles. Se inventariaron 34 carpetas con 6.996 elementos reportados por el lector; esto no constituye validación integral del PST.

Se comprobaron identificadores únicos, conservación de los 20 originales, conciliación de 246 adjuntos, correspondencia de los cuerpos con sus archivos de texto y reconstrucción del texto desde los segmentos Excel sin pérdida de caracteres no blancos. El tamaño y fecha de modificación del PST no cambiaron durante la lectura. El Excel se reabrió para comprobar tablas, filas, fechas y resultados, y se revisaron visualmente las vistas afectadas.

## Reproducción

Se reutilizan los scripts de `../piloto_20/` y su lector local:

1. `extraer_piloto.py --limit 100 --out 03_trabajo/piloto_100/resultados`.
2. `ampliar_cuerpos.py --out 03_trabajo/piloto_100/resultados`.
3. `crear_excel.mjs 03_trabajo/piloto_100/resultados` con el runtime Node disponible.

Ejecutar desde la raíz del proyecto. Una nueva ejecución sobrescribe los resultados. La recomendación de almacenamiento continúa siendo SQL por las relaciones entre correos, adjuntos y participantes. La implementación sigue pendiente.
