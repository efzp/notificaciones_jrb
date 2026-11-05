# Carga asincrona de Canopus

El endpoint recibe un Excel en base64, lo guarda en un contenedor privado y responde HTTP 202 con un identificador. Una actividad Durable lee el archivo y publica filas/contactos en SQL. La respuesta inicial confirma recepcion, no publicacion. El historial Durable contiene referencias tecnicas y conteos; no contiene el base64, cedulas ni correos.

## Antes de habilitar

1. Aplicar `sql/15_contactos_canopus_consolidado.sql` en `sqldb-jrb-sabana-dev` si falta. Requiere el modelo existente y los permisos 09/10/13. En la revision de desarrollo se encontraron las tres tablas ya creadas y sin cargas. La Function no ejecuta DDL.
2. En la cuenta `rgjrbsabanadev9fc5`, crear el contenedor privado `canopus-staging`. En **Control de acceso (IAM)**, asignar **Colaborador de datos de Storage Blob** a `func-jrb-sabana-dev-uami`, con ambito en ese contenedor. Es la identidad de ejecucion; la identidad de GitHub se utiliza para desplegar.
3. En Function App > **Configuracion > Variables de entorno**, agregar los valores siguientes. Conservar la configuracion Durable y `AzureWebJobsStorage` existente.

| Variable | Valor |
|---|---|
| `CANOPUS_INGEST_ENABLED` | `1` despues de configurar SQL y Blob |
| `CANOPUS_STORAGE_ACCOUNT_URL` | `https://rgjrbsabanadev9fc5.blob.core.windows.net` |
| `CANOPUS_STAGING_CONTAINER` | `canopus-staging` |
| `CANOPUS_DRIVE_ID` | `b!BVdcW1M1CUGvabWi-TiRu48Z8tWTR9xJhVeDlno8JlrPYQEct01cSIycOPVx2lWL` |
| `TERCEROS_MANAGED_IDENTITY_CLIENT_ID` | `2edc5bdf-78ac-473d-be13-c15b773f2aa6` |
| `CANOPUS_MAX_BYTES` | `10485760` (10 MiB) |
| `CANOPUS_MAX_ROWS` | `50000` |
| `CANOPUS_MAX_CONTACTS` | `200000` |

La identidad debe pertenecer a `rol_carga_terceros` en JRB. El DDL15 concede acceso a las tablas nuevas; 09/13 conceden lectura de las fuentes y lectura/insercion de direcciones. No requiere permisos adicionales en JNC ni Microsoft Graph: Power Automate entrega el archivo.

4. Hacer commit y push a `main`. El workflow existente ejecuta pruebas y prepara un paquete que ahora incluye `canopus/`. Comprobar que ambos jobs de GitHub Actions terminen correctamente. Esta entrega no hace push ni despliega.
5. Configurar una regla de ciclo de vida para eliminar blobs temporales, por ejemplo despues de 30 dias. Un trabajo fallido solo se puede reanudar mientras exista su blob y su historial Durable; conservarlo hasta resolver el incidente. SharePoint conserva el original.

## Contrato para Power Automate

```http
POST https://func-jrb-sabana-dev-cjbufggmhqf3ane9.westus2-01.azurewebsites.net/api/canopus/cargas
Content-Type: application/json
x-functions-key: <CLAVE_DE_FUNCTION>
```

```json
{
  "drive_id": "b!BVdcW1M1CUGvabWi-TiRu48Z8tWTR9xJhVeDlno8JlrPYQEct01cSIycOPVx2lWL",
  "item_id": "ID_GRAPH_DEL_ARCHIVO",
  "nombre_archivo": "AUDIENCIAS JUNIO-JULIO Canopus.XLSX",
  "version_origen": "ETAG_O_VERSION_DEL_ARCHIVO",
  "fecha_modificacion_origen": "2026-10-29T17:09:50Z",
  "contenido_base64": "CONTENIDO_BASE64_REAL_UNA_SOLA_VEZ"
}
```

Los seis campos son obligatorios y no se admiten otros. Usar el ID estable Graph del archivo, no el ID numerico del elemento de lista ni una ruta. Version, fecha y contenido deben corresponder a la misma revision: consultar metadatos antes y despues de **Get file content**; si cambia el eTag, repetir la lectura. Si el conector expone `$content` en base64, usarlo directamente sin volver a codificarlo.

El futuro flujo debe limitar su disparador a `Documentos compartidos/Notificaciones JB/canopus` del sitio `https://cpabaas.sharepoint.com/sites/JuntaRegionalBogot`, incluyendo subcarpetas solo si se desea, y filtrar archivos `.xlsx`. La Function comprueba biblioteca, metadatos, contenido y limites; recibe el archivo de un llamador autenticado y no consulta su carpeta en Graph.

En la accion **HTTP**, desactivar **Settings > Asynchronous pattern** para que Power Automate reciba el 202 sin esperar la publicacion. Activar **Secure Inputs** y **Secure Outputs** en acciones con archivo/clave. La recepcion no consulta SQL ni interpreta las filas; tiene esperas acotadas de Storage y Durable y devuelve 503 si no puede aceptar el trabajo. Reenviar exactamente la misma solicitud es seguro.

Ejemplo de respuesta 202:

```json
{
  "id_seguimiento": "canopus-<64_CARACTERES_HEXADECIMALES>",
  "estado": "ACEPTADA",
  "consulta_estado": "https://<HOST>/api/canopus/cargas/canopus-<HASH>"
}
```

Consultar `consulta_estado` mediante **GET**, con `x-functions-key` en el encabezado. No incluir claves en la URL. HTTP 200 puede indicar `Running`, `Pending`, etc. Una publicacion exitosa exige **estado = Completed y resultado.estado = PUBLICADA**. `Completed` con `resultado.estado = ERROR` indica un rechazo de contenido/version y devuelve un codigo seguro y, si se registro, `id_carga_canopus`. Corregir el Excel y enviar la nueva revision. No tratar el 202 como carga terminada.

Si la orquestacion queda `Failed` o `Terminated`, revisar acceso/disponibilidad y enviar **POST** a `consulta_estado + /reanudar`, con la misma clave. Los errores de infraestructura se reintentan tres veces en segundo plano. Los errores de contenido no se reintentan automaticamente.

## Lectura, vinculacion y publicacion

- `.xlsx` sin macros, encabezados en fila 1. Campos obligatorios: `identificacion`, `correo_electronico`, `ultimo_empleador`, `email_empresa`. Opcionales: `fecha_audiencia`, `reparto`, `correo_electronicofe`. Se normalizan acentos, mayusculas y separadores de encabezado; las columnas pueden cambiar de orden. Cada hoja no vacia debe tener este perfil. Un archivo sin filas, con una cedula invalida, formulas/errores en campos importados o encabezado ambiguo se rechaza completo.
- Cedula como texto: conserva ceros iniciales. Los numeros enteros seguros se convierten a texto; no se reconstruyen numeros redondeados, separadores ni notacion cientifica. No se exige audiencia/reparto para vincular.
- Fecha opcional: fecha nativa, serial Excel con el epoch del libro, ISO, dia/mes/anio y dia-mes-anio. Fecha invalida queda NULL y se cuenta en `fechas_revision`; no cambia el vinculo por cedula.
- Se guardan la cedula, procedencia y empleador. Se omiten nombre del paciente, direccion, telefono, nacimiento, dictamen y datos clinicos. Las listas de correo conservan todas las direcciones validas, con dominio normalizado y local-part preservada. No se corrigen dominios ni se comprueba que un buzon exista.
- El empleador se resuelve contra entidades del rol empleador en el consolidado vigente con las reglas genericas existentes, umbral 95 y margen 5. Sin correspondencia clara, la fila y los correos del empleador quedan en revision. No se crea ni se adivina una empresa. El paciente no requiere pertenecer al maestro de terceros.
- `correo_electronicofe` conserva rol `por_definir` y estado `REVISION`. Un campo mixto crea contactos validos y una incidencia adicional con el original limitado a 1024 caracteres; el codigo indica si fue truncado. El archivo completo permanece en SharePoint y temporalmente en Blob.
- Tablas destino: `ing.canopus_carga`, `ing.canopus_fila`, `ref.contacto_reportado`; direcciones validas reutilizadas en `mail.direccion`. Publicacion atomica: el cambio de carga vigente se hace al confirmar todas las filas/contactos. Un fallo conserva la publicacion anterior.
- La identidad de contenido es biblioteca + archivo + SHA256 + parser. Los reintentos no duplican filas ni direcciones. Un contenido antiguo puede reutilizarse si aparece como una revision posterior; un evento anterior o dos contenidos con igual fecha se rechazan. Los metadatos de la carga reutilizada reflejan la ultima revision aceptada; no se genera un historial adicional por cada evento repetido.
- `rep.vw_contactos_caso` aplica **CONSOLIDADO > CANOPUS para paciente/empleador > MAESTRO_TERCEROS para entidades**. Conserva varios correos de la fuente preferida. Para empleadores exige correspondencia de entidad; compartir cedula no autoriza cambiar la empresa.

La publicacion queda disponible inmediatamente en esa vista. Esta entrega no crea obligaciones pendientes ni cruza PST. Tampoco envia avisos a la cola del maestro de terceros, que todavia no consume Canopus y excluye pacientes.

## Validar en SQL

```sql
SELECT TOP (20) id_carga_canopus,nombre_archivo,version_origen,version_parser,
       estado,vigente,filas_procesadas,creado_utc,publicado_utc
FROM ing.canopus_carga ORDER BY id_carga_canopus DESC;

DECLARE @id_carga_canopus int = 123; -- Reemplazar por el resultado de la Function.
SELECT f.estado_empleador,COUNT_BIG(*) AS filas
FROM ing.canopus_fila f WHERE f.id_carga_canopus=@id_carga_canopus
GROUP BY f.estado_empleador;

SELECT c.tipo_parte,c.estado,COUNT_BIG(*) AS contactos
FROM ref.contacto_reportado c
JOIN ing.canopus_fila f ON f.id_fila_canopus=c.id_fila_canopus
WHERE f.id_carga_canopus=@id_carga_canopus GROUP BY c.tipo_parte,c.estado;

SELECT TOP (20) * FROM rep.vw_contactos_caso WHERE origen='CANOPUS'
ORDER BY id_caso,tipo_parte;
```

## Pruebas reproducibles

```powershell
python -m unittest discover -s tests -v
# Opt-in SQL: DDL15 aplicado; siempre revierte las escrituras al terminar.
$env:CANOPUS_CARGA_SQL_VALIDAR = '1'
$env:CANOPUS_ARCHIVO_REAL_VALIDAR = '1' # Requiere el Excel local; no se versiona.
python -m unittest discover -s tests -p test_canopus_carga_sql.py -v
Remove-Item Env:CANOPUS_CARGA_SQL_VALIDAR, Env:CANOPUS_ARCHIVO_REAL_VALIDAR
```

`VALIDACION_CANOPUS_FUNCION.json` distingue pruebas locales, SQL con rollback y validaciones aun pendientes de despliegue. El corpus real leido tiene 2750 filas. Las pruebas no sustituyen la validacion del endpoint ya desplegado con la identidad administrada y el contenedor configurado.

Referencias: [Durable Functions Python v2 y persistencia del historial](https://learn.microsoft.com/en-us/azure/durable-task/durable-functions/durable-functions-bindings?tabs=python-v2), [carga Blob con Python y permisos](https://learn.microsoft.com/en-us/azure/storage/blobs/storage-blob-upload-python).
