# Procesamiento de auditorias de notificaciones JRB

Repositorio de codigo y configuracion para procesar reportes de SharePoint, tabularizar PST, cruzar evidencias de envio y lectura, y procesar insumos de auditoria. Los archivos fuente permanecen fuera del repositorio.

| Componente | Estado actual |
|---|---|
| Consolidacion Sabana en Azure SQL | Function Python implementada, pendiente de piloto de integracion en Azure |
| Modelo SQL y control de cargas | Scripts incluidos en `sql/`, preparados para instalacion y verificacion |
| Despliegue con GitHub Actions | Workflow con pruebas y publicacion mediante OIDC |
| Tabularizacion PST | Alcance previsto; pendiente de integrar como componente del repositorio |
| Cruces de envio, entrega y lectura | Alcance previsto; pendiente de implementacion |
| Procesamiento de otros insumos de auditoria | Alcance previsto; pendiente de implementacion |

## Function de consolidacion Sabana

Implementación Python 3.12, Azure Functions runtime 4 y modelo de programación v2. Recibe un Excel por HTTP y escribe en el modelo `cfg`, `ing`, `core` y `rep` preparado para `sqldb-jrb-sabana-dev`. El código está generado; no se han creado recursos, desplegado la aplicación ni ejecutado escrituras en Azure SQL desde esta entrega.

## Archivos

| Archivo | Responsabilidad |
|---|---|
| `function_app.py` | Endpoint `POST /api/consolidar-sabana`, respuestas y correlación |
| `sabana/domain.py` | Contrato HTTP, perfil, hashes y estructuras de datos |
| `sabana/excel.py` | Lectura del Excel, mapping y normalización |
| `sabana/database.py` | Identidad administrada, conexión, transacciones y escritura SQL |
| `sabana/service.py` | Orquestación, reintentos y publicación |
| `sql/06_identidad_function.sql` | Usuario Entra y pertenencia a `rol_carga_sabana` |
| `tools/enviar_archivo.ps1` | Cliente de prueba para enviar un archivo |
| `tools/validar_corpus.py` | Prueba de lectura local sin SQL |
| `profile.example.json` | Copia de referencia del perfil para pruebas locales; el endpoint lee `cfg` en SQL |
| `VALIDACION.json` | Resultados y límites de la verificación realizada |

## Preparar SQL y la identidad

1. Ejecutar los scripts `01_tablas.sql` a `04_publicacion.sql` del modelo. Si se instaló el primer DDL con `tipo_dato varchar(12)`, aplicar su corrección antes de cargar catálogos.
2. Ejecutar `05_verificacion_motor.sql` y confirmar su resultado `OK` en la nueva base.
3. Crear la Function App para Python 3.12 y activar su identidad administrada. Es posible usar identidad asignada por el sistema o una identidad de usuario; para esta última, configurar `AZURE_CLIENT_ID` con su client ID.
4. Como administrador Microsoft Entra de la base, reemplazar el nombre de identidad en `sql/06_identidad_function.sql` y ejecutarlo. La aplicación no requiere `db_owner`. Para desarrollo local, asignar el mismo rol a la identidad Entra usada por Azure CLI.
5. Autorizar en el firewall SQL las IP de salida de la Function, o configurar conectividad privada según el plan elegido. La identidad y el firewall se configuran por separado. El código no cambia reglas de red.

El servidor conserva autenticación exclusiva de Entra. La Function utiliza `ManagedIdentityCredential` en Azure y `DefaultAzureCredential` en desarrollo local. No hay contraseñas SQL. Se usa conexión cifrada, validación de certificado y Microsoft ODBC Driver 18, que debe estar instalado en el entorno de ejecución. `ODBC_DRIVER` permite seleccionar otro driver Microsoft compatible que esté efectivamente instalado; se recomienda 18.

La creación de usuarios con `FROM EXTERNAL PROVIDER` requiere que el administrador pueda resolver esa identidad en el directorio. Si Azure no puede resolverla o existen nombres duplicados, completar esa configuración Entra antes de probar el endpoint.

## Configuración

Copiar `local.settings.example.json` a `local.settings.json` para desarrollo local. Cambiar el servidor por su FQDN real. `local.settings.json` está excluido del paquete de despliegue y de Git.

En Azure, agregar los valores como configuración de la aplicación:

```text
SQL_SERVER=<servidor-real>.database.windows.net
SQL_DATABASE=sqldb-jrb-sabana-dev
ODBC_DRIVER=ODBC Driver 18 for SQL Server
MAX_EXCEL_BYTES=10485760
MAX_EXCEL_ROWS=2000
MAX_PROCESSING_SECONDS=100
```

Configurar también el almacenamiento y las opciones del host que requiere la Function App según el plan. `UseDevelopmentStorage=true` sirve para Azurite local; no es una configuración de almacenamiento para Azure. La aplicación no utiliza tablas, colas ni blobs para persistir los Excel recibidos. Azure Functions sí requiere su almacenamiento de host.

El límite de filas es físico en la hoja principal: 2.000 filas después de la cabecera. El límite se puede ajustar tras medir el piloto. Cada conexión tiene timeout de inicio de sesión de 15 segundos y de consulta de 20 segundos. El plazo de procesamiento es cooperativo y se comprueba entre operaciones; una operación nativa en curso puede terminar después del plazo. `host.json` limita la ejecución a tres minutos. No se ha medido aún el tiempo total de escritura en Azure SQL.

## Desplegar desde GitHub Actions

El workflow completo está en `.github/workflows/deploy-function.yml`. Está preparado para que el contenido de esta carpeta sea la raíz del repositorio de código. Prueba Python 3.12 en los pull requests y publica únicamente desde `main`, tras completar las pruebas y cuando esté configurada la variable `AZURE_FUNCTIONAPP_NAME`. También permite ejecución manual desde `main`. Mientras esa variable no exista, se ejecutan las pruebas y se omite la publicación.

Se distinguen dos identidades:

| Identidad | Uso | Permiso |
|---|---|---|
| Asignada por el sistema de la Function App | Ejecutar la aplicación y escribir en SQL | Usuario Entra de la BD y `rol_carga_sabana` |
| Asignada por el usuario `id-gha-jrb-sabana-dev` | Publicar código desde GitHub Actions mediante OIDC | `Website Contributor`, limitado a la Function App |

Crear la identidad de publicación en el grupo del proyecto. En la Function App, abrir IAM y asignarle `Website Contributor` con alcance en esa Function App. Esta identidad de publicación no necesita permisos SQL.

En la identidad de publicación, agregar una credencial federada con **Other issuer / Otro emisor**, copiando el subject exacto del repositorio. Para repositorios nuevos de GitHub.com desde el 15 de julio de 2026, el formato incluye los IDs inmutables del propietario y del repositorio:

```text
Nombre de credencial: github-main
Issuer: https://token.actions.githubusercontent.com
Subject: repo:<PROPIETARIO_GITHUB>@<OWNER_ID>/<REPOSITORIO_FUNCTION>@<REPO_ID>:ref:refs/heads/main
Audience: api://AzureADTokenExchange
```

Con GitHub CLI autenticado, obtener el subject para un repositorio nuevo desde PowerShell:

```powershell
$repoInfo = gh api 'repos/PROPIETARIO/REPOSITORIO' | ConvertFrom-Json
"repo:$($repoInfo.owner.login)@$($repoInfo.owner.id)/$($repoInfo.name)@$($repoInfo.id):ref:refs/heads/main"
```

Sustituir propietario y repositorio por los reales. Un repositorio anterior al 15 de julio de 2026 puede conservar el subject basado en nombres `repo:PROPIETARIO/REPOSITORIO:ref:refs/heads/main` hasta optar por el formato inmutable. Si se personalizaron los claims de la organización, usar el subject real mostrado por la configuración OIDC. El valor en Azure debe coincidir exactamente con el emitido por GitHub; no usar automáticamente el ejemplo anterior basado solo en nombres para un repositorio nuevo.

En GitHub, configurar Settings > Secrets and variables > Actions:

| Tipo | Nombre | Valor |
|---|---|---|
| Secret | `AZURE_CLIENT_ID` | Client ID de la identidad de publicación |
| Secret | `AZURE_TENANT_ID` | ID del directorio Entra |
| Secret | `AZURE_SUBSCRIPTION_ID` | ID de la suscripción del proyecto |
| Variable | `AZURE_FUNCTIONAPP_NAME` | Nombre real de la Function App |

Son identificadores; la autenticación usa tokens OIDC de corta duración. No hay client secret ni publish profile en este workflow. `AZURE_CLIENT_ID` es un secreto del repositorio para `azure/login`; no copiarlo a la configuración de la Function App, cuyo acceso SQL mantiene la identidad asignada por el sistema.

Conservar el tipo de credencial federada y el workflow alineados: este archivo no declara `environment`, por lo que utiliza el subject de la rama `main`. Si se incorpora un GitHub Environment, configurar su subject federado correspondiente antes de publicar. Si el Centro de implementación de Azure ya generó otro workflow, mantener una sola ruta de publicación para esta aplicación.

El paso de preparación crea `.deploy` usando una lista explícita de archivos: `function_app.py`, `host.json`, `requirements.txt` y `sabana/`. Las pruebas, los SQL de configuración, los informes y los ajustes locales quedan fuera de la publicación. `remote-build: true` solicita que Azure instale las dependencias para Linux. No agregar los parámetros heredados `scm-do-build-during-deployment` ni `enable-oryx-build` a este workflow de Flex.

Este workflow publica código en una Function App ya existente. La creación de recursos, la configuración de variables SQL, la identidad de ejecución y la regla de acceso de red se completan previamente. El runner de GitHub no conecta a SQL; las pruebas de integración y el piloto se ejecutan después desde la aplicación desplegada.

Este proyecto de código se vincula con `https://github.com/efzp/notificaciones_jrb`. La carpeta superior de auditoría y sus fuentes permanecen fuera del repositorio. Si más adelante se reorganiza el código en subcarpetas, mantener el workflow en `.github/workflows` de la raíz y actualizar sus rutas de pruebas y empaquetado.

Referencia: [GitHub Actions para Azure Functions](https://learn.microsoft.com/en-us/azure/azure-functions/functions-how-to-github-actions?tabs=python), [parámetros oficiales de la acción](https://github.com/Azure/functions-action), [subjects OIDC inmutables](https://docs.github.com/en/actions/reference/security/oidc#immutable-subject-claims).

## Ejecutar localmente y publicar manualmente

Desde esta carpeta, con Python 3.12, Azure Functions Core Tools v4 y ODBC Driver 18 instalados:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item local.settings.example.json local.settings.json
# Editar SQL_SERVER en local.settings.json.
az login
func start
```

El comando `func start` requiere que su configuración de almacenamiento de host sea válida, por ejemplo Azurite en desarrollo. Para desplegar cuando la Function App y su identidad estén creadas:

```powershell
func azure functionapp publish <NOMBRE_REAL_FUNCTION_APP> --build remote
```

Publicar desde esta carpeta. `.funcignore` excluye pruebas, informes, archivos locales y el perfil de ejemplo. No subir dependencias Windows a la Function Linux: la compilación remota instala las de su plataforma.

## Contrato con Power Automate

Enviar `POST` con `Content-Type: application/json` y una clave específica de la función en el encabezado `x-functions-key`.

```json
{
  "drive_id": "ID_ESTABLE_BIBLIOTECA",
  "item_id": "ID_ESTABLE_ARCHIVO",
  "nombre_archivo": "REPORTE.xlsx",
  "ruta_sharepoint": "/Reporte Sabana/Sala 1/REPORTE.xlsx",
  "version_origen": "3.0",
  "fecha_modificacion_origen": "2026-10-26T15:30:00.1234567Z",
  "sala": 1,
  "id_perfil": 1,
  "contenido_base64": "BASE64_REAL_DEL_ARCHIVO"
}
```

Los campos `sala` e `id_perfil` deben enviarse como enteros JSON. Los identificadores de biblioteca y archivo son cadenas. Usar una pareja de identificadores estable: Graph drive/item, o GUID de biblioteca y UniqueId del archivo de SharePoint. Mantener siempre el mismo criterio. El nombre, la ruta y el identificador basado en ruta no son una identidad estable frente a renombrados.

Los metadatos de versión y modificación deben corresponder exactamente al contenido descargado. Obtener o comprobar esos metadatos al leer el archivo; si cambia durante la lectura, repetirla. Enviar la fecha con zona horaria, preferiblemente UTC. La Function conserva siete decimales para `datetime2(7)`.

`contenido_base64` es el contenido base64 del Excel una sola vez. Si el conector ya expone `$content` en base64, usar ese valor directamente; no codificarlo de nuevo. No enviar el objeto completo de contenido ni una URL en ese campo. Evitar registrar el cuerpo HTTP o la clave en salidas de diagnóstico del flujo; configurar las entradas/salidas seguras de las acciones que contengan el archivo.

Una modificación de contenido produce otra carga; repetir el mismo contenido y perfil reutiliza la existente. Una carga con la misma identidad de archivo, contenido y perfil pero otra sala se rechaza por contexto conflictivo. Ese cambio de interpretación requiere revisar el origen y versionar el perfil o la identidad de archivo según corresponda.

## Comportamiento de lectura

- Se admite `.xlsx` y `.xlsm`; no se ejecutan macros. Se procesa la primera hoja `hoja1`, con encabezados en la primera fila. Las otras hojas quedan contadas como omitidas en la estructura auditada.
- El mapping usa encabezados normalizados y alias de `cfg.alias_columna`. Exige 50 correspondencias únicas. Acepta columnas reordenadas cuando los nombres son inequívocos.
- Para un encabezado desconocido, la inferencia requiere al menos 45 anclas con el mismo desplazamiento respecto a la plantilla y valores compatibles en la posición esperada. Registra la inferencia en SQL y emite advertencia. No contiene una regla `NO APLICA → eps`. Sin evidencia suficiente, bloquea la publicación.
- Ignora dimensiones infladas por formato y cadenas compartidas vacías. El contenido adicional fuera del bloque de encabezados se resume en una advertencia y permanece en el Excel de SharePoint; no se replica masivamente en SQL. Si un encabezado conocido aparece más allá de las 512 columnas soportadas, se requiere revisar la plantilla.
- Fechas: nativas, ISO, ISO con hora, año/mes/día, día/mes/año, día-mes-año, día.mes.año, compacto YYYYMMDD y seriales usando el epoch real del libro. El perfil local interpreta día/mes/año y no prueba mes/día/año como alternativa.
- Una fecha opcional inválida conserva el original, escribe NULL en el campo tipado y genera W. Una llave ausente o inválida bloquea el archivo completo. Se distinguen blancos y NO APLICA. Las longitudes excesivas no se truncan.
- Las identificaciones de texto conservan ceros iniciales. Los enteros con formato simple de ceros también los recuperan. Los identificadores numéricos de 16 o más dígitos se rechazan para evitar usar valores que Excel pudo redondear.
- Una fórmula sin resultado almacenado bloquea el archivo: es necesario recalcular y guardar en Excel. No hay motor de cálculo de fórmulas en la Function.

El perfil inicial cubre 2025 a 2027 y el calendario SQL conserva los festivos 2026 del script original. Para ampliar el periodo o usar un calendario operativo completo, preparar el calendario y un perfil nuevo. Los cambios de semántica del parser/modelo requieren una versión compatible en código y perfil.

## Escritura y costo

Los bytes y el base64 viven solo en memoria. SQL conserva referencias al origen, datos tipados, excepciones y el mapping auditado. Un archivo no duplica todas sus filas al recibir un reintento.

La carga registra primero archivo y contenido. Después, una transacción explícita por archivo inserta los hechos y procedencias, valida y publica mediante `ing.publicar_carga`. Un error revierte esa transacción y conserva el puntero publicado anteriormente. El registro del error se intenta en una transacción aparte. Si SQL está totalmente inaccesible, la respuesta devuelve correlación y el registro SQL del fallo puede quedar pendiente; el archivo permanece en SharePoint.

Se adquiere un bloqueo transaccional por archivo mediante `sys.sp_getapplock`. Los hechos compartidos se buscan con bloqueos de rango, se comparan antes de reutilizarlos y se almacenan en una caché limitada a la transacción. Ante deadlocks, conflictos únicos o errores transitorios se reintenta la operación completa hasta tres intentos, dentro del plazo. Una carga publicada se reutiliza sin insertar sus filas de nuevo. Un evento atrasado o conflictivo devuelve 409.

La conexión física se cierra al terminar y se desactiva el pooling ODBC para permitir la pausa de SQL serverless. La Function no asigna su identidad a `db_owner`, no modifica el esquema y no cambia el firewall. Las notificaciones del Excel son declaraciones; esta fase no acredita envío, entrega ni lectura de correos PST.

El endpoint es síncrono para el piloto de un archivo por solicitud. No hay trabajo de fondo que continúe después de devolver éxito. Medir el piloto incluyendo la reanudación de SQL serverless. Si el volumen o el conector no permiten completar la operación dentro del tiempo HTTP disponible, convertir el ingreso en un trabajo asíncrono con una cola y un endpoint de estado antes de ampliar la carga; aumentar solo `functionTimeout` no elimina los límites HTTP.

## Respuestas

| HTTP | Significado | Acción del flujo |
|---|---|---|
| 200 | Carga publicada o reutilizada | Registrar `id_carga` y advertencias |
| 400 | JSON, base64 o metadatos inválidos | Corregir el contrato |
| 409 | Versión atrasada o conflicto de identidad/contenido | Revisar evento y versión vigente |
| 413 | Archivo o hoja supera los límites | Revisar tamaño o cambiar la estrategia de ingreso |
| 422 | Error de contenido, perfil o validación SQL | Revisar las incidencias; no reintentar sin corregir |
| 503 | Conexión, acceso, archivo ocupado o plazo agotado | Revisar configuración y reintentar con espera |

Las respuestas incluyen `correlacion`; las incidencias se limitan a 25 por respuesta. Todas las incidencias de contenido de una carga registrada se conservan en SQL. Los logs propios contienen correlación, ID de carga y códigos, sin cuerpos HTTP, valores de celdas, tokens ni texto completo de errores SQL.

Ejemplo de éxito:

```json
{
  "estado": "PUBLICADA",
  "id_archivo": 1,
  "id_carga": 1,
  "filas_procesadas": 29,
  "reutilizada": false,
  "cambio_carga_vigente": true,
  "advertencias": 1,
  "incidencias": [],
  "total_incidencias": 1,
  "correlacion": "IDENTIFICADOR_DE_LA_SOLICITUD"
}
```

El ejemplo omite el detalle de la advertencia y el SHA para facilitar la lectura. La respuesta real incluye ambos.

## Pruebas

```powershell
python -m unittest discover -s tests -v
python tools/validar_corpus.py --inputs '<CARPETA_CON_EXCEL>' --output resultado_corpus.json
```

Las pruebas de repositorio utilizan conexiones simuladas para revisar parametrización, reutilización y rollback; no sustituyen una prueba de integración con el motor SQL. El corpus local revisa lectura y normalización, no inserciones ni tiempos de SQL. `VALIDACION.json` distingue esos alcances. El siguiente hito es desplegar, conceder el rol, cargar un Excel piloto, comparar la vista y volver a enviarlo para comprobar la idempotencia real.

Referencias oficiales: [modelo Python de Azure Functions](https://learn.microsoft.com/azure/azure-functions/functions-reference-python), [autenticación con tokens del controlador ODBC](https://learn.microsoft.com/en-us/sql/connect/odbc/using-azure-active-directory?view=sql-server-ver17), [claves HTTP](https://learn.microsoft.com/en-us/azure/azure-functions/functions-bindings-http-webhook-trigger?tabs=python-v2), [límites de ejecución](https://learn.microsoft.com/en-us/azure/azure-functions/functions-scale).
