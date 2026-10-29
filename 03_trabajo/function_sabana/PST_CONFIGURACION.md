# Ingestión asíncrona de PST desde SharePoint

El código está preparado localmente; este cambio no crea recursos, no asigna permisos, no aplica SQL y no publica la Function. El workflow existente publicará el código cuando se incorpore a `main`.

## Comportamiento

1. `POST /api/pst/ingestas` recibe exclusivamente `drive_id`, `item_id` y `version_origen` (eTag). Devuelve `202 Accepted` y un identificador de seguimiento. No conecta a SQL, no llama a Graph y no descarga el archivo en esa petición. Las dos operaciones con el backend Durable tienen timeout de 15 segundos cada una.
2. Durable ejecuta la preparación. La identidad comprueba en Graph que el archivo pertenece a la biblioteca y carpeta autorizadas, que es PST, que su eTag coincide y que su tamaño está permitido. Consulta SQL para reutilizar una carga completa de esa misma versión.
3. La preparación copia el PST por streaming a un contenedor privado de Blob Storage, calculando SHA-256. No guarda todo el archivo en RAM ni en `/tmp`. Verifica el eTag de SharePoint de nuevo al terminar. No registra una carga SQL de contenido no verificado.
4. Se inventarían carpetas y se procesan lotes secuenciales. El lector usa rangos del blob con `If-Match` y caché limitada a unos 32 MiB. Cada actividad puede ejecutarse en una máquina diferente. La copia temporal evita depender de una URL de descarga de SharePoint que puede expirar durante el procesamiento.
5. Cada lote inserta datos y avanza el cursor en la misma transacción. El diario `ing.pst_elemento` impide contar otra vez los elementos confirmados. Se guarda tanto la procedencia MAPI como la cabecera cuando ambas existen: son observaciones distintas, no envíos adicionales.
6. Los fallos de lectura de elementos se registran. Una carga con elementos fallidos termina `CON_ERRORES` y no se publica como vigente. Los fallos de red/SQL hacen que Durable reintente la actividad; si persisten, la orquestación falla y puede reanudarse con el cursor SQL.
7. Cada 50 lotes se reinicia el historial mediante `continue_as_new`, conservando solo referencias pequeñas. Los cuerpos, adjuntos y datos personales extraídos no pasan por el historial Durable ni por los logs de la aplicación.

No se escriben estados de envío, entrega o lectura de casos. `mail.reporte_destinatario` solo recibe campos estructurados DSN/MDN observados cuando son recuperables; no interpreta palabras del asunto, las banderas de lectura local ni reportes de retransmisión como confirmaciones de lectura. Las clases MAPI y referencias originales se conservan para la fase de cruce.

## SQL

En `sqldb-jrb-sabana-dev`, aplicar:

1. `sql/07_tablas_pst.sql`, únicamente si todavía no se instalaron las tablas PST.
2. `sql/08_pst_asincrono.sql`, que agrega cursores, diario y el rol de carga. Es reejecutable.

La migración crea una tabla adicional `ing.pst_elemento` y añade campos de control a tablas PST. Las tablas Excel no cambian. La Function existente de Excel conserva su plazo cooperativo de 100 segundos; `host.json` amplía a 30 minutos el timeout del host para las actividades PST.

Como administrador Entra de la base, asignar el rol PST al mismo usuario de ejecución que ya usa SQL:

```sql
ALTER ROLE rol_carga_pst ADD MEMBER [func-jrb-sabana-dev];
```

El rol concede lectura/inserción en las tablas necesarias y actualización de los controles. No concede `db_owner` ni borrado. Si se usa otra identidad para ejecutar la aplicación, sustituir el nombre de usuario. La identidad de GitHub Actions no recibe acceso SQL.

## Identidad y recursos

La identidad usada por `pst.cloud.identity()` es la del sistema de la Function, salvo que se configure `PST_MANAGED_IDENTITY_CLIENT_ID` para una identidad de usuario. No copiar aquí el client ID de GitHub Actions. La conexión SQL conserva el comportamiento existente de `sabana.database.connect_sql()`.

Son necesarios:

- Microsoft Graph: permiso de aplicación `Sites.Selected` y concesión explícita de lectura al sitio de SharePoint. El permiso de aplicación y la concesión de sitio son pasos separados. No basta con iniciar sesión como usuario en Power Automate.
- Blob Storage: contenedor privado `pst-staging` creado previamente y `Storage Blob Data Contributor` para esa identidad, con alcance al contenedor o a la cuenta correspondiente. No hay SAS ni claves en los cuerpos HTTP.
- Backend Durable: `AzureWebJobsStorage` debe admitir Blob, Queue y Table. Si utiliza identidad en vez de connection string, configurar `AzureWebJobsStorage__blobServiceUri`, `__queueServiceUri`, `__tableServiceUri` y, para Durable, `Storage Blob Data Contributor`, `Storage Queue Data Contributor` y `Storage Table Data Contributor`. Conservar también los permisos que requiere el host existente. La identidad de almacenamiento ya usada por el host puede ser distinta de la identidad del cliente Blob de PST; ambas deben configurarse con sus alcances correctos.
- Acceso de red a Graph, SharePoint, Storage y al SQL existente. Este código no modifica firewall ni reglas de red.

Referencias oficiales: [permisos seleccionados de Graph](https://learn.microsoft.com/en-us/graph/permissions-selected-overview), [conexiones de Durable mediante identidad](https://learn.microsoft.com/en-us/azure/azure-functions/durable/durable-functions-configure-managed-identity).

Configurar el backend Durable y sus permisos antes de publicar esta ampliación en la Function App compartida. Un backend sin acceso a sus colas o tablas puede impedir el inicio correcto del host y afectar también el endpoint Excel.

## Variables de la Function App

Además de los ajustes SQL existentes:

| Variable | Valor |
|---|---|
| `PST_DRIVE_ID` | `b!BVdcW1M1CUGvabWi-TiRu48Z8tWTR9xJhVeDlno8JlrPYQEct01cSIycOPVx2lWL` |
| `PST_SOURCE_FOLDER_ID` | `014EVAVLDPPDOAX5DDJFHZ3HBJ2BLWQLFF` |
| `PST_STORAGE_ACCOUNT_URL` | `https://rgjrbsabanadev9fc5.blob.core.windows.net` |
| `PST_STAGING_CONTAINER` | `pst-staging` |
| `PST_MAX_BYTES` | `21474836480` (20 GiB, ajustable) |
| `PST_STAGE_SECONDS` | `1500` (25 minutos para transferencia inicial) |
| `PST_BATCH_MESSAGES` | `50` |
| `PST_BATCH_SECONDS` | `180` (plazo cooperativo; se comprueba entre mensajes) |
| `PST_MANAGED_IDENTITY_CLIENT_ID` | Omitir para usar la identidad del sistema |

Carpeta verificada mediante el conector SharePoint el 28 de octubre de 2026: biblioteca `Documentos`, ruta `/Documentos compartidos/Notificaciones JB` del sitio `https://cpabaas.sharepoint.com/sites/JuntaRegionalBogot`. El ID anterior pertenece a `Notificaciones JB` e incluye sus subcarpetas. Es diferente de `Reporte Sabana`.

El timeout del host está configurado para Flex Consumption. El límite de dos minutos del cliente HTTP no se amplía: se evita desacoplando la petición del procesamiento. El backend Durable usa el task hub `PstIngestionV1`. Se limita a una actividad concurrente por instancia; esto no es un bloqueo global de toda la aplicación. SQL coordina las cargas y los reintentos concurrentes por contenido.

Una descarga que no cabe en el plazo de preparación no se declara completada. La preparación podrá reintentarse desde el principio; si ya existe el blob completo pero falta su hash, recupera la verificación leyendo ese blob. La descarga inicial no tiene checkpoint por byte. Para archivos o redes que excedan repetidamente ese plazo se requiere una estrategia de copia externa o más tiempo en un plan compatible.

## Power Automate (UI en inglés)

Crear un flujo separado para PST:

1. Trigger **SharePoint — When a file is created or modified (properties only)** con Site Address `https://cpabaas.sharepoint.com/sites/JuntaRegionalBogot`, Library Name `Documentos` y Folder `/Documentos compartidos/Notificaciones JB`; filtrar `.pst` y excluir carpetas.
2. **Send an HTTP request to SharePoint**, usando la biblioteca y el ID numérico del trigger para obtener `id`, `eTag`, `name`, `size` y `parentReference`. El ID numérico de lista no equivale al `item_id` de Graph.
3. **HTTP** con método `POST` a `https://<function-host>/api/pst/ingestas`, `Content-Type: application/json` y `x-functions-key` en Headers. No usar **Get file content** ni base64.
4. En **HTTP → Settings → Networking → Asynchronous pattern**, seleccionar **Off**. Si la UI no presenta el control, el JSON de la acción debe contener `"operationOptions": "DisableAsyncPattern"` al mismo nivel que `inputs`. Esto impide que Power Automate siga automáticamente el encabezado `Location` y permanezca esperando.
5. Activar **Secure Inputs** y **Secure Outputs** en la acción HTTP que contiene la clave. Guardar únicamente `id_seguimiento` y el enlace de consulta como resultado operativo del flujo. Las claves no se copian a los enlaces de estado.
6. Considerar `202` como solicitud aceptada, no como extracción completada. Consultar el estado posteriormente con otra acción/ejecución. No mantener el flujo original esperando con un bucle cada pocos segundos.

Cuerpo de la acción HTTP, usando el nombre real de la acción de metadatos:

```json
{
  "drive_id": "@{body('Metadata_PST')?['parentReference']?['driveId']}",
  "item_id": "@{body('Metadata_PST')?['id']}",
  "version_origen": "@{body('Metadata_PST')?['eTag']}"
}
```

Respuesta de aceptación:

```json
{
  "id_seguimiento": "pst-<sha256-del-contexto>",
  "estado": "ACEPTADA",
  "consulta_estado": "https://<function-host>/api/pst/ingestas/pst-<sha256-del-contexto>"
}
```

`GET /api/pst/ingestas/{id_seguimiento}` requiere también `x-functions-key`. Devuelve fase y contadores; al acabar, `estado` de Durable es `Completed` y `resultado.estado` de SQL es `COMPLETA` o `CON_ERRORES`. Solo `COMPLETA` indica una carga sin elementos fallidos. Los resultados de extracción siguen siendo datos previos al cruce, no validaciones de envío.

`POST /api/pst/ingestas/{id_seguimiento}/reanudar` permite reiniciar una orquestación `Failed` o `Terminated` manteniendo el identificador y aprovechando los lotes confirmados. No reinicia cargas activas. Un elemento ya marcado con error no se reextrae automáticamente al reanudar infraestructura; una corrección del extractor debe incrementar su versión o debe recibirse una versión nueva del PST. No modificar manualmente un cursor para forzar repeticiones.

Referencia: [desactivar el seguimiento asíncrono automático de HTTP](https://learn.microsoft.com/en-us/azure/connectors/connectors-native-http#disable-asynchronous-operations).

## Almacenamiento y límites de interpretación

- SQL guarda solo metadatos, relaciones y controles. Una copia idéntica de PST usa el mismo `ing.pst_contenido`; otra versión de SharePoint conserva su procedencia.
- La copia de staging sí consume almacenamiento Blob adicional mientras exista. Configurar retención y limpieza para copias cuyo procesamiento haya terminado, sin borrar una copia activa o necesaria para reanudar. La aplicación no elimina archivos de SharePoint ni evidencia original.
- El borrado de historial Durable mediante su API de purga se configura como operación de mantenimiento con retención explícita; no se ejecuta automáticamente aquí. Después de purgar el estado, se puede reenviar la referencia: SQL sigue evitando recargar una versión completa conocida.
- La lectura por rangos reduce RAM y evita `/tmp`; añade operaciones Blob. El tamaño inicial es 64 KiB y la caché unos 32 MiB. El tiempo/costo completo debe medirse en el piloto de Azure, especialmente con PST grandes y redes con alta latencia.
- No hay deduplicación global por Message-ID, asunto o fecha. La huella global de mensaje se deja nula hasta definir y verificar una política basada en contenido. Las copias de PST con contenido idéntico sí comparten su extracción. La comparación de direcciones conserva la representación observada; no se inventa SMTP a partir de nombres o de direcciones EX.
- MAPI y cabecera pueden aportar dos observaciones del mismo destinatario. La fase de cruce debe resolverlas antes de contar envíos. `Reply-To` tampoco equivale a destinatario del envío.
- SHA-256 de adjuntos y cuerpo permanece nulo si no se han leído sus bytes. No se infiere un hash a partir del nombre o el tamaño.
- No se truncan silenciosamente campos demasiado largos. El elemento queda como error visible para su revisión y el original se conserva en el PST.

## Verificación y despliegue

`requirements.txt` fija Durable, Blob y `libpff-python==20260926`, con wheel disponible para Python 3.12 / Linux x86-64. El workflow comprueba la importación nativa en Linux y las pruebas antes de publicar. `tools/preparar_paquete.py` incluye `pst/` y `sabana/`, y excluye fuentes, pruebas y SQL del paquete de ejecución.

Pruebas locales:

```powershell
python -m unittest discover -s tests -v
python tools/validar_pst_local.py 'C:\ruta\archivo.pst' --maximum 40
```

La segunda orden solo lee una muestra y muestra conteos; no consulta SQL ni sube contenido. No reemplaza la validación de extremo a extremo en Azure. Antes del primer envío real hay que aplicar SQL, configurar permisos y variables, desplegar y comprobar una carga completa pequeña contra sus conteos de origen.
