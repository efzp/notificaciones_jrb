# Piloto de 20 correos

El piloto se ejecutó el 23 de octubre de 2026 sobre `JUNIO Y JULIO 2026.pst`, abierto en modo de solo lectura. No se creó ninguna base de datos.

## Entregables

- `resultados/piloto_20_correos.xlsx`: tabla de correos, metadatos de adjuntos, participantes de cabeceras e incidencias.
- `resultados/correos.csv`, `adjuntos.csv` y `participantes_cabecera.csv`: tablas delimitadas por punto y coma, con codificación UTF-8 con BOM.
- Archivos JSON: datos completos, inventario de carpetas, resumen e incidencias.
- `resultados/cabeceras/`: texto de las cabeceras de los correos incluidos, tal como lo devuelve el lector.
- `resultados/huella_pst.json`: SHA-256 del PST calculado después de la extracción.

## Resultados y alcance

Se inventariaron 34 carpetas con 6.996 elementos reportados por el lector. Se examinaron 22 candidatos hasta obtener 20 correos extraídos, con metadatos de 78 adjuntos y 79 participaciones en cabeceras. Las participaciones incluyen remitentes y destinatarios; no representan personas únicas.

Dos candidatos presentaron errores del lector al consultar el número de adjuntos. Se conservaron sus rutas, posiciones y mensajes técnicos en Incidencias. No se ha determinado si la causa está en el PST o en el lector. La muestra excluye esos dos elementos y no permite estimar tasas de error del archivo completo.

La selección es técnica y determinista: carpetas no vacías ordenadas por ruta, alternando entre ellas y tomando posiciones distribuidas por índice hasta completar 20 correos legibles. En esta ejecución se obtuvo un correo de cada una de 20 carpetas. No es una muestra estadística ni una selección de 20 notificaciones confirmadas.

Los 20 registros contienen Message-ID, asunto, remitente SMTP, Para y fechas de envío y recepción del buzón. No se detectaron Message-ID repetidos dentro de la muestra. Dos adjuntos carecen de nombre recuperable. El conteo de adjuntos incluye imágenes y otros objetos, no solo documentos de notificación.

Se identificaron 18 mensajes de clase `IPM.Note` y 2 de clase `REPORT.IPM.Note.Relayed.DR`. La clase de mensaje no basta para concluir entrega o validez de una notificación.

## Validaciones

- 20 identificadores PST únicos y referencias consistentes entre tablas.
- 78 filas de adjuntos conciliadas con las cantidades reportadas por los 20 correos.
- Fechas UTC conservadas en JSON y CSV, junto con su conversión a UTC-05:00. En Excel, las fechas de Colombia son valores de fecha ordenables.
- Archivo Excel reabierto para comprobar dimensiones, tablas y tipos de fecha; totales calculados verificados y hojas revisadas visualmente.
- Tamaño y fecha de modificación del PST iguales al inicio y final de la extracción. La huella SHA-256 se calculó después, como referencia para futuras comprobaciones; no es una comparación de huellas antes y después.

No se extrajeron cuerpos ni contenido binario de los adjuntos. Los participantes se obtuvieron de las cabeceras de transporte, no de un censo completo de destinatarios MAPI. Las fechas de recepción corresponden al buzón exportado y no prueban entrega al destinatario.

## Recomendación de almacenamiento

Usar SQL. El piloto confirma relaciones de uno a muchos entre correo y adjuntos, y entre correo y participantes. Un modelo relacional permite consultar y conciliar esas relaciones para la auditoría.

Para una implementación centralizada, PostgreSQL es una opción adecuada. Propuesta inicial: fuentes, ejecuciones de extracción, correos, participantes por correo, adjuntos e incidencias. Mantener un identificador propio por registro y conservar el identificador PST y Message-ID como datos de origen. No imponer unicidad global a Message-ID: podría repetirse entre copias de un mismo mensaje.

Las propiedades opcionales pueden conservarse en un campo JSONB, sin requerir una base NoSQL adicional. PostgreSQL documenta [restricciones y claves foráneas](https://www.postgresql.org/docs/18/ddl-constraints.html) y [almacenamiento JSONB](https://www.postgresql.org/docs/current/datatype-json.html).

Conservar el PST y los documentos como archivos de evidencia, vinculados mediante rutas e identificadores. Antes de una carga completa, resolver los dos errores de lectura y contrastar destinatarios contra las propiedades MAPI. No se ha creado ni configurado SQL o NoSQL.

## Reproducción

`extraer_piloto.py` usa Python 3.12 y el paquete local `libpff-python-windows==20231205` en `.deps`. La biblioteca informa versión interna `20251218`. El script abre el PST como `rb`. La ruta predeterminada apunta al PST de la raíz del proyecto; admite `--pst` para indicar otra fuente.

`crear_excel.mjs` genera el Excel desde los JSON con Artifact Tool. Los CSV escapan texto que pudiera interpretarse como fórmula al abrirse en Excel; el JSON conserva los valores exactos extraídos. Una nueva ejecución sobrescribe los resultados del piloto.
