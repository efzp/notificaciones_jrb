# Actualizacion asincrona del maestro de terceros

La Function utiliza `terceros-v2`: regex/proximidad con umbral 95 y margen 5. Los pacientes permanecen excluidos. El filtro de frecuencia de dominios y el mapeo inverso revertidos no forman parte de esta implementacion.

## Componentes

| Archivo | Responsabilidad |
|---|---|
| `terceros/functions.py` | Avisos HTTP, estado, reanudacion, orquestacion y recuperacion programada. |
| `terceros/automation_domain.py` | Validacion estricta del contrato e identificacion estable de avisos. |
| `terceros/control.py` | Registro sin duplicados, agrupacion, reserva global, renovacion y reintentos en SQL. |
| `terceros/automation.py` | Captura y publicacion con renovacion de reserva durante la lectura. |
| `terceros/repository.py` | Publicacion y finalizacion de las solicitudes en la misma transaccion. Conserva la CLI existente. |

HTTP devuelve 202 despues de solicitar el inicio de Durable Functions. El registro SQL ocurre en una actividad posterior; 202 significa aceptacion para procesar, no maestro actualizado. La captura completa se mantiene en memoria de la actividad y nunca se devuelve al orquestador. El historial Durable contiene identificadores tecnicos, estados y conteos.

Cada aviso nuevo mueve la disponibilidad de las solicitudes PENDIENTE a diez minutos desde ese aviso. Un duplicado no extiende esa ventana. REINTENTO conserva su espera propia. Al reclamar, se agrupan hasta 1.000 solicitudes elegibles con un token UUID nuevo por intento fisico. Un bloqueo `terceros:carga` permite comprobar y reclamar una sola reserva global, incluso con varias instancias de Functions.

La reserva dura veinte minutos y se renueva cada minuto durante la captura. Antes de publicar se renueva una vez mas y se verifica dentro de la transaccion. La finalizacion vuelve a comprobar token, cantidad de solicitudes y vencimiento; si alguno no coincide, se revierte toda la publicacion. Las solicitudes que llegaron despues de la reserva permanecen pendientes. Las actividades repetidas consultan el estado SQL antes de volver a capturar.

Los fallos tienen espera creciente de 60, 120, 240 y 480 segundos; para mas intentos configurados, hasta 900 segundos. Tras cinco intentos se marca AGOTADA. Una reserva expirada se recupera al siguiente intento de procesamiento. La recuperacion diaria drena solicitudes registradas en SQL aunque ya no exista su historial Durable. AGOTADA requiere revision administrativa; reanudar una orquestacion no reinicia ese limite.

## Preparacion de SQL

1. JRB debe tener las migraciones 09 y 10 aplicadas. La migracion historica 11 no se ejecuta ni se requiere.
2. Ejecutar `sql/12_control_actualizacion_terceros.sql` en `sqldb-jrb-sabana-dev` para crear la tabla de control.
3. Ejecutar `sql/13_permisos_automatizacion_terceros_jrb.sql` en JRB.
4. Ejecutar `sql/14_permisos_automatizacion_terceros_jnc.sql` en `sqldb-jnc-notif-dev`.
5. Crear/reutilizar el usuario Entra de la identidad de ejecucion en ambas bases y asignarle `rol_carga_terceros` en JRB y `rol_lectura_terceros_jrb` en JNC.

Ejecutar como administrador Entra, sustituyendo el nombre de la identidad por el real:

```sql
-- En JRB; crear solo si el usuario no existe.
CREATE USER [IDENTIDAD_DE_EJECUCION] FROM EXTERNAL PROVIDER;
ALTER ROLE [rol_carga_terceros] ADD MEMBER [IDENTIDAD_DE_EJECUCION];

-- En JNC; crear solo si el usuario no existe.
CREATE USER [IDENTIDAD_DE_EJECUCION] FROM EXTERNAL PROVIDER;
ALTER ROLE [rol_lectura_terceros_jrb] ADD MEMBER [IDENTIDAD_DE_EJECUCION];
```

La identidad es la de ejecucion de la Function. La identidad federada de GitHub despliega el codigo. JNC se consulta exclusivamente en modo lectura. Ambos servidores deben admitir la conectividad de red de la Function. Se conserva el requisito `ALLOW_SNAPSHOT_ISOLATION` de la captura existente.

## Configuracion y despliegue

| Configuracion de la Function App | Predeterminado | Uso |
|---|---|---|
| `TERCEROS_AUTOMATION_ENABLED` | `0` | Cambiar a `1` cuando SQL y permisos esten preparados. |
| `TERCEROS_MANAGED_IDENTITY_CLIENT_ID` | Omitido | Client ID de una identidad de usuario asignada a esta Function; omitir para identidad de sistema. |
| `TERCEROS_DEBOUNCE_SECONDS` | `600` | Espera desde el ultimo aviso nuevo; entre 0 y 3.600. |
| `TERCEROS_LEASE_SECONDS` | `1200` | Duracion de reserva, entre 120 y 3.600 segundos. |
| `TERCEROS_BATCH_REQUESTS` | `1000` | Solicitudes maximas por reserva, entre 1 y 1.000. |
| `TERCEROS_RECOVERY_SCHEDULE` | `0 0 8 * * *` | Recuperacion diaria a las 08:00 UTC, 03:00 de Colombia. |

Se reutilizan la Function App, el host Durable y AzureWebJobsStorage existentes. Se mantiene el limite actual de treinta minutos por actividad. Si la captura llega a superarlo, habra que dividirla en actividades con referencias a instantaneas externas; la reserva vencida permite reintentar, pero no amplifica ese limite.

`tools/preparar_paquete.py` incluye ahora `terceros/` y los DDL 09/10 para verificar sus huellas. La Function lee esos dos archivos; no ejecuta DDL. El paquete no incluye snapshots, archivos fuente, pruebas ni la migracion 11. El workflow existente de GitHub Actions ejecuta las pruebas y construye este paquete al publicar. Esta entrega no realiza push ni despliegue.

## Contrato para los productores

Enviar al terminar una publicacion exitosa de origen:

```http
POST https://<HOST_REAL_DE_LA_FUNCTION>/api/terceros/actualizaciones
Content-Type: application/json
x-functions-key: <CLAVE_DE_FUNCION>
```

```json
{
  "origen": "JRB",
  "tipo_fuente": "EXCEL",
  "id_carga_origen": "123",
  "hash_revision": "abababababababababababababababababababababababababababababababab"
}
```

El hash del ejemplo es sintetico: usar el SHA-256 real de la revision publicada, de 64 caracteres hexadecimales. No incluir la fecha de reintento en la clave. Las combinaciones admitidas son JNC/LOTE, JRB/EXCEL y JRB/PST. El identificador de carga es texto tecnico de hasta 128 caracteres (letras ASCII, digitos, punto, guion, guion bajo y dos puntos).

Para Excel puede utilizarse el SHA-256 devuelto por la consolidacion. Si se reprocesa el mismo archivo por un cambio de perfil/regla o se inactiva una carga, el productor debe calcular una revision distinta que incluya ese cambio. Para PST usar el SHA-256 del contenido junto con la revision de procesamiento cuando corresponda. En JNC la revision debe representar el lote publicado e incluir correcciones o inactivaciones; MAX(id) no basta para detectarlas.

El productor debe enviar avisos solo despues de que Excel quede PUBLICADA, PST COMPLETA o el lote JNC publicado. Este endpoint recibe una declaracion del productor; no valida el estado del lote en la base de origen a partir del aviso. Las consultas del maestro si leen exclusivamente publicaciones vigentes de JRB y los datos activos de JNC.

Respuesta 202:

```json
{
  "id_seguimiento": "terceros-<SHA256_DEL_AVISO>",
  "estado": "ACEPTADA",
  "consulta_estado": "https://<HOST_REAL_DE_LA_FUNCTION>/api/terceros/actualizaciones/terceros-<SHA256_DEL_AVISO>"
}
```

Consultar `GET /api/terceros/actualizaciones/{id_seguimiento}` con la misma autenticacion. Al completar, `resultado.estado` sera COMPLETADA o AGOTADA. Los errores de Durable se exponen mediante codigos, sin cuerpo SQL ni valores del origen. Si la orquestacion esta Failed/Terminated, `POST /api/terceros/actualizaciones/{id_seguimiento}/reanudar` la reinicia; SQL evita volver a procesar solicitudes completadas.

La entrega implementa el receptor y el trabajador. Los productores JNC, el flujo de Excel y el flujo de PST todavia deben incorporar la llamada HTTP al concluir. El temporizador recupera avisos ya registrados; no descubre cambios arbitrarios de JNC/JRB que nunca hayan sido notificados, ni registra avisos cuyo primer registro Durable/SQL fallo definitivamente. Estos ultimos se recuperan reanudando o repitiendo el aviso tras corregir el fallo.

## Validacion

La suite sin SQL comprueba contrato, 202 sin acceso SQL, idempotencia del aviso, esperas Durable, recuperacion, reintentos, perdida de reserva y contenido del paquete. La integracion opt-in requiere una cola vacia y usa transacciones externas revertidas, sin publicar datos sinteticos permanentemente:

```powershell
# Desde 03_trabajo/function_sabana.
$env:TERCEROS_CONTROL_SQL_INSTANTANEA = (Resolve-Path '..\catalogo_terceros\resultados\corte_v2.json.gz').Path
python -m unittest discover -s tests -p test_terceros_control_sql.py -v
Remove-Item Env:TERCEROS_CONTROL_SQL_INSTANTANEA
```

```sql
SELECT TOP (100) id_actualizacion,origen,tipo_fuente,id_carga_origen,
       estado,intentos,max_intentos,disponible_utc,id_ejecucion,
       reserva_hasta_utc,id_carga_terceros,codigo_ultimo_error
FROM ing.terceros_actualizacion
ORDER BY id_actualizacion DESC;

SELECT * FROM ing.terceros_publicacion;
```

Referencias oficiales: [blueprints Python](https://learn.microsoft.com/azure/azure-functions/functions-reference-python), [persistencia de entradas y salidas Durable](https://learn.microsoft.com/azure/azure-functions/durable-functions-serialization-and-persistence).
