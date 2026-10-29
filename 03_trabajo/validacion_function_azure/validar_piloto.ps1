param(
    [string]$Sala = 'Sala 1',
    [string]$NombreArchivo = 'AUD 20 FEB.XLSX',
    [string]$CarpetaResultados = '.'
)
$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$env:PYTHONIOENCODING = 'utf-8'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding
$root = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$subscription = '84e56f9e-b50d-4e86-9ac1-5baef9df3467'
$group = 'rg-jrb-sabana-dev'
$app = 'func-jrb-sabana-dev'
$server = 'sql-jrb-sabana-dev.database.windows.net'
$database = 'sqldb-jrb-sabana-dev'
$file = Join-Path (Join-Path (Join-Path $root '03_trabajo\analisis_estructura_sabana\inputs') $Sala) $NombreArchivo
$inventory = Get-Content -LiteralPath (Join-Path $root '03_trabajo\analisis_estructura_sabana\inventario_sharepoint.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$sourceItem = @($inventory | Where-Object {$_.sala -eq $Sala -and $_.name -eq $NombreArchivo})
if ($sourceItem.Count -ne 1) { throw 'El archivo no es unico en el inventario de SharePoint.' }
$itemUrl = $sourceItem[0].url
$artifactRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot $CarpetaResultados))
if ($artifactRoot -ne $PSScriptRoot -and -not $artifactRoot.StartsWith($PSScriptRoot + '\',[StringComparison]::OrdinalIgnoreCase)) { throw 'La carpeta de resultados debe quedar dentro de la carpeta de validacion.' }
$report = [ordered]@{ fecha_utc = [DateTime]::UtcNow.ToString('o'); archivo = "$Sala/$NombreArchivo"; solicitudes = @() }

function Read-Sql([string]$Query) {
    $command = $connection.CreateCommand()
    $command.CommandTimeout = 30
    $command.CommandText = $Query
    $reader = $command.ExecuteReader()
    $rows = @()
    try {
        while ($reader.Read()) {
            $record = [ordered]@{}
            for ($i = 0; $i -lt $reader.FieldCount; $i++) {
                $record[$reader.GetName($i)] = if ($reader.IsDBNull($i)) { $null } else { $reader.GetValue($i) }
            }
            $rows += [pscustomobject]$record
        }
    } finally { $reader.Close(); $command.Dispose() }
    return $rows
}

$countsSql = @'
SELECT
 (SELECT COUNT_BIG(*) FROM ing.archivo) AS archivos,
 (SELECT COUNT_BIG(*) FROM ing.carga) AS cargas,
 (SELECT COUNT_BIG(*) FROM ing.fila_origen) AS filas,
 (SELECT COUNT_BIG(*) FROM core.caso) AS casos,
 (SELECT COUNT_BIG(*) FROM core.caso_dato) AS datos_caso,
 (SELECT COUNT_BIG(*) FROM core.entidad) AS entidades,
 (SELECT COUNT_BIG(*) FROM core.caso_parte) AS partes,
 (SELECT COUNT_BIG(*) FROM core.notificacion_declarada) AS notificaciones,
 (SELECT COUNT_BIG(*) FROM ing.parte_fila) AS partes_fila,
 (SELECT COUNT_BIG(*) FROM ing.valor_original) AS originales,
 (SELECT COUNT_BIG(*) FROM ing.incidencia) AS incidencias;
'@

$connection = $null
try {
    $metadata = az rest --method get --url ($itemUrl + '?$select=id,name,size,eTag,lastModifiedDateTime,parentReference') --output json --only-show-errors | ConvertFrom-Json
    if ($LASTEXITCODE -ne 0) { throw 'No se pudo leer la metadata de SharePoint.' }
    $bytes = [IO.File]::ReadAllBytes($file)
    if ($bytes.Length -ne $metadata.size) { throw 'El tamano local no coincide con SharePoint.' }
    # Copia actual obtenida mediante el conector autenticado de SharePoint.
    $currentBytes = [IO.File]::ReadAllBytes((Join-Path $artifactRoot $NombreArchivo))
    $sha = [Security.Cryptography.SHA256]::Create()
    try {
        $hash = [BitConverter]::ToString($sha.ComputeHash($bytes)).Replace('-','').ToLowerInvariant()
        $currentHash = [BitConverter]::ToString($sha.ComputeHash($currentBytes)).Replace('-','').ToLowerInvariant()
    } finally { $sha.Dispose() }
    if ($hash -cne $currentHash) { throw 'La copia local no coincide byte a byte con el Excel de SharePoint.' }
    $report.sha256 = $hash
    $report.copia_sharepoint_verificada = $true
    $report.version_origen = $metadata.eTag
    $report.fecha_modificacion_origen = $metadata.lastModifiedDateTime

    $sqlToken = az account get-access-token --resource 'https://database.windows.net/' --query accessToken --output tsv --only-show-errors
    if ($LASTEXITCODE -ne 0) { throw 'No se pudo obtener el token SQL.' }
    $connection = New-Object Data.SqlClient.SqlConnection("Server=tcp:$server,1433;Database=$database;Encrypt=True;TrustServerCertificate=False;Connection Timeout=30;Application Name=ValidacionPilotoSabana;")
    $connection.AccessToken = $sqlToken.Trim()
    $sqlToken = $null
    $connection.Open()
    $report.antes = @(Read-Sql $countsSql)[0]
    $report.identidad_sql = @(Read-Sql "SELECT p.type_desc, CASE WHEN r.name='rol_carga_sabana' THEN 1 ELSE 0 END AS rol_correcto FROM sys.database_principals p LEFT JOIN sys.database_role_members rm ON rm.member_principal_id=p.principal_id LEFT JOIN sys.database_principals r ON r.principal_id=rm.role_principal_id WHERE p.name='func-jrb-sabana-dev';")
    $keyDocument = az functionapp function keys list --subscription $subscription --resource-group $group --name $app --function-name 'ConsolidarSabana' --output json --only-show-errors | ConvertFrom-Json
    if ($LASTEXITCODE -ne 0 -or -not $keyDocument.default) { throw 'No se pudo obtener la clave especifica de la funcion.' }
    $endpoint = 'https://func-jrb-sabana-dev-cjbufggmhqf3ane9.westus2-01.azurewebsites.net/api/consolidar-sabana'
    $payload = @{
        drive_id = $metadata.parentReference.driveId; item_id = $metadata.id
        nombre_archivo = $metadata.name
        ruta_sharepoint = ($metadata.parentReference.path -replace '^/drives/[^/]+/root:', '') + '/' + $metadata.name
        version_origen = $metadata.eTag; fecha_modificacion_origen = $metadata.lastModifiedDateTime
        sala = [int]($Sala -replace '\D',''); id_perfil = 1; contenido_base64 = [Convert]::ToBase64String($bytes)
    } | ConvertTo-Json -Compress
    for ($attempt=1; $attempt -le 2; $attempt++) {
        $watch = [Diagnostics.Stopwatch]::StartNew()
        try {
            $response = Invoke-WebRequest -UseBasicParsing -Method Post -Uri $endpoint -Headers @{'x-functions-key'=$keyDocument.default} -ContentType 'application/json; charset=utf-8' -Body ([Text.Encoding]::UTF8.GetBytes($payload)) -TimeoutSec 180
            $status = [int]$response.StatusCode
            $result = $response.Content | ConvertFrom-Json
        } catch {
            if (-not $_.Exception.Response) { throw 'La peticion HTTP no pudo completarse.' }
            $status = [int]$_.Exception.Response.StatusCode
            $streamReader = New-Object IO.StreamReader($_.Exception.Response.GetResponseStream())
            try { $responseText = $streamReader.ReadToEnd() } finally { $streamReader.Dispose() }
            try { $result = $responseText | ConvertFrom-Json } catch { $result = @{codigo='RESPUESTA_NO_JSON'} }
        }
        $watch.Stop()
        $snapshot = @(Read-Sql $countsSql)[0]
        $entry = [ordered]@{ intento=$attempt; http=$status; segundos=[Math]::Round($watch.Elapsed.TotalSeconds,3); respuesta=$result; conteos_sql=$snapshot }
        $report.solicitudes += $entry
        $entry | ConvertTo-Json -Depth 8
        if ($status -ne 200) { break }
    }
    if ($report.solicitudes.Count -eq 2 -and $report.solicitudes[1].http -eq 200) {
        $first = $report.solicitudes[0]
        $second = $report.solicitudes[1]
        $report.idempotencia = [ordered]@{
            mismo_id_carga = $first.respuesta.id_carga -eq $second.respuesta.id_carga
            reutilizada = $second.respuesta.reutilizada -eq $true
            conteos_sin_cambios = ($first.conteos_sql | ConvertTo-Json -Compress) -ceq ($second.conteos_sql | ConvertTo-Json -Compress)
        }
        $loadId = [long]$second.respuesta.id_carga
        $report.carga_sql = @(Read-Sql "SELECT c.id_carga,c.estado,c.filas_esperadas,c.tamano_bytes,LOWER(CONVERT(varchar(64),c.hash_contenido,2)) AS sha256,CASE WHEN a.id_carga_vigente=c.id_carga THEN 1 ELSE 0 END AS vigente,(SELECT COUNT_BIG(*) FROM ing.fila_origen f WHERE f.id_carga=c.id_carga) AS filas_sql,(SELECT COUNT_BIG(*) FROM rep.vw_fila_base f WHERE f.id_carga=c.id_carga) AS filas_vista FROM ing.carga c JOIN ing.archivo a ON a.id_archivo=c.id_archivo WHERE c.id_carga=$loadId;")
        $expected = Get-Content -LiteralPath (Join-Path $artifactRoot 'huellas_esperadas.json') -Raw -Encoding UTF8 | ConvertFrom-Json
        $columns = @($expected.filas[0].hashes.PSObject.Properties | ForEach-Object {
            $name = $_.Name
            if ($name -notmatch '^[a-z_]+$') { throw 'Campo de comparacion no permitido.' }
            "LOWER(CONVERT(varchar(64),HASHBYTES('SHA2_256',CASE WHEN [$name] IS NULL THEN N'N' ELSE N'V'+CONVERT(nvarchar(max),[$name]) END),2)) AS [$name]"
        }) -join ','
        $actual = @(Read-Sql ("SELECT fila_origen,$columns FROM rep.vw_fila_base WHERE id_carga=$loadId ORDER BY fila_origen;"))
        $differences = @()
        $compared = 0
        foreach ($expectedRow in $expected.filas) {
            $actualRow = @($actual | Where-Object fila_origen -eq $expectedRow.numero_fila)
            if ($actualRow.Count -ne 1) { $differences += 'fila_no_unica'; continue }
            foreach ($field in $expectedRow.hashes.PSObject.Properties) {
                $compared++
                if ($actualRow[0].($field.Name) -cne $field.Value) { $differences += $field.Name }
            }
        }
        $report.comparacion_excel_sql = @{campos_comparados=$compared; diferencias=$differences; filas_esperadas=$expected.filas_parseadas; filas_sql=$actual.Count}
        $report.consolidado = @(Read-Sql "SELECT (SELECT COUNT(*) FROM sys.columns WHERE object_id=OBJECT_ID('rep.vw_consolidado_sabana')) AS columnas,(SELECT COUNT_BIG(*) FROM rep.vw_consolidado_sabana WHERE archivo_origen=(SELECT a.ruta_actual FROM ing.archivo a JOIN ing.carga c ON c.id_archivo=a.id_archivo WHERE c.id_carga=$loadId)) AS filas,(SELECT COUNT(*) FROM ing.incidencia WHERE id_carga=$loadId AND severidad='E' AND resuelta=0) AS errores_activos;")
        $report.granularidad = @(Read-Sql "SELECT COUNT(*) AS filas,COUNT(DISTINCT id_caso) AS casos,COUNT(DISTINCT id_caso_dato) AS versiones_dato_caso,(SELECT COUNT(*) FROM (SELECT f.id_caso FROM ing.fila_origen f JOIN ing.parte_fila pf ON pf.id_fila=f.id_fila JOIN core.caso_parte cp ON cp.id_caso_parte=pf.id_caso_parte WHERE f.id_carga=$loadId AND cp.tipo_parte='aseguradora' GROUP BY f.id_caso HAVING COUNT(DISTINCT cp.id_entidad)=2) d) AS casos_dos_aseguradoras FROM ing.fila_origen WHERE id_carga=$loadId;")
    }
} catch {
    $report.error = $_.Exception.Message
    Write-Output ('ERROR_PILOTO: ' + $_.Exception.Message)
} finally {
    if ($connection) { $connection.Dispose() }
    $keyDocument = $null
    $payload = $null
    $report | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath (Join-Path $artifactRoot 'resultado_piloto.json') -Encoding UTF8
}
