param([string]$ReportName='20261027_actualizacion')
$ErrorActionPreference='Stop'
[Console]::OutputEncoding=New-Object Text.UTF8Encoding
$reportDir=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot $ReportName))
if(-not $reportDir.StartsWith($PSScriptRoot+'\',[StringComparison]::OrdinalIgnoreCase)){throw 'Informe fuera de la carpeta de trabajo.'}
$inventory=Get-Content -LiteralPath (Join-Path $reportDir 'inventario_verify.json') -Raw -Encoding UTF8
$connection=New-Object Data.SqlClient.SqlConnection('Server=tcp:sql-jrb-sabana-dev.database.windows.net,1433;Database=sqldb-jrb-sabana-dev;Encrypt=True;TrustServerCertificate=False;Connection Timeout=30;Application Name=VerificacionActualizacionSabana;')
$token=az account get-access-token --resource 'https://database.windows.net/' --query accessToken --output tsv --only-show-errors
if($LASTEXITCODE -ne 0 -or -not $token){throw 'No fue posible autenticar SQL.'}
$connection.AccessToken=$token.Trim();$token=$null
function Query([string]$Sql){
    $command=$connection.CreateCommand();$command.CommandText=$Sql;$command.CommandTimeout=90
    $null=$command.Parameters.Add('@inventario',[Data.SqlDbType]::NVarChar,-1)
    $command.Parameters['@inventario'].Value=$inventory
    $items=New-Object 'Collections.Generic.List[object]'
    try{
        $reader=$command.ExecuteReader()
        try{while($reader.Read()){
            $item=[ordered]@{}
            for($i=0;$i -lt $reader.FieldCount;$i++){$item[$reader.GetName($i)]=if($reader.IsDBNull($i)){$null}else{$reader.GetValue($i)}}
            $items.Add([pscustomobject]$item)
        }}finally{$reader.Dispose()}
    }finally{$command.Dispose()}
    return $items.ToArray()
}
$scope=@'
WITH alcance AS (
 SELECT drive_id,item_id,sala FROM OPENJSON(@inventario)
 WITH(drive_id nvarchar(200),item_id nvarchar(200),sala int)
), archivos AS (
 SELECT s.*,a.id_archivo,a.id_carga_vigente,c.estado,c.filas_esperadas
 FROM alcance s LEFT JOIN ing.archivo a ON a.drive_id=s.drive_id AND a.item_id=s.item_id
 LEFT JOIN ing.carga c ON c.id_carga=a.id_carga_vigente
)
'@
try{
    $connection.Open()
    $byRoom=@(Query ($scope+@'
SELECT a.sala,COUNT_BIG(*) AS archivos,
 SUM(CASE WHEN a.estado='PUBLICADA' THEN 1 ELSE 0 END) AS publicados,
 SUM(ISNULL(f.filas,0)) AS filas,
 SUM(CASE WHEN ISNULL(f.filas,-1)<>ISNULL(a.filas_esperadas,-2) THEN 1 ELSE 0 END) AS archivos_con_conteo_incorrecto,
 SUM(ISNULL(e.sala_incorrecta,0)) AS filas_con_sala_incorrecta
FROM archivos a OUTER APPLY(
 SELECT COUNT_BIG(*) AS filas
 FROM ing.fila_origen f JOIN core.caso c ON c.id_caso=f.id_caso WHERE f.id_carga=a.id_carga_vigente
) f OUTER APPLY(
 SELECT COUNT_BIG(*) AS sala_incorrecta
 FROM ing.fila_origen f JOIN core.caso c ON c.id_caso=f.id_caso
 WHERE f.id_carga=a.id_carga_vigente AND c.sala<>a.sala
) e GROUP BY a.sala ORDER BY a.sala;
'@))
    $cases=@(Query ($scope+@'
SELECT COUNT(DISTINCT f.id_caso) AS casos_distintos
FROM archivos a JOIN ing.fila_origen f ON f.id_carga=a.id_carga_vigente;
'@))[0]
    $issues=@(Query ($scope+@'
SELECT i.severidad,i.codigo,COUNT_BIG(*) AS incidencias,COUNT(DISTINCT a.id_archivo) AS archivos
FROM archivos a JOIN ing.incidencia i ON i.id_carga=a.id_carga_vigente
WHERE i.resuelta=0 GROUP BY i.severidad,i.codigo ORDER BY i.severidad,i.codigo;
'@))
    $outside=@(Query @'
SELECT a.id_archivo,a.nombre_actual,c.id_carga,c.filas_esperadas
FROM ing.archivo a JOIN ing.carga c ON c.id_carga=a.id_carga_vigente
WHERE NOT EXISTS(SELECT 1 FROM OPENJSON(@inventario)
 WITH(drive_id nvarchar(200),item_id nvarchar(200)) s
 WHERE s.drive_id=a.drive_id AND s.item_id=a.item_id)
ORDER BY a.id_archivo;
'@)
    $latest=@{}
    foreach($line in [IO.File]::ReadLines((Join-Path $reportDir 'resultados.jsonl'))){
        if($line.Trim()){$entry=$line | ConvertFrom-Json;$latest[$entry.drive_id+'|'+$entry.item_id]=$entry}
    }
    $success=@($latest.Values | Where-Object {$_.verificado_sql})
    $inventory=@($success) | ConvertTo-Json -Depth 8 -Compress
    $integrity=@(Query @'
SELECT COUNT_BIG(*) AS cargas_comprobadas,
 SUM(CASE WHEN c.estado='PUBLICADA'
 AND LOWER(CONVERT(varchar(64),c.hash_contenido,2))=j.sha256
 AND c.filas_esperadas=j.filas AND f.filas=j.filas THEN 0 ELSE 1 END) AS discrepancias
FROM OPENJSON(@inventario)
WITH(drive_id nvarchar(200),item_id nvarchar(200),sha256 varchar(64),filas bigint) j
LEFT JOIN ing.archivo a ON a.drive_id=j.drive_id AND a.item_id=j.item_id
LEFT JOIN ing.carga c ON c.id_carga=a.id_carga_vigente
OUTER APPLY(SELECT COUNT_BIG(*) AS filas FROM ing.fila_origen f WHERE f.id_carga=c.id_carga) f;
'@)[0]
    $report=[ordered]@{
        verificado_utc=[DateTime]::UtcNow.ToString('o')
        archivos_nuevos_publicados=$success.Count
        filas_nuevas=($success | Measure-Object filas -Sum).Sum
        fallos_pendientes=@($latest.Values | Where-Object {-not $_.verificado_sql}).Count
        archivos_nuevos_con_advertencias=@($success | Where-Object {$_.advertencias -gt 0}).Count
        integridad_cargas_publicadas=$integrity
        alcance_por_sala=$byRoom
        casos_distintos_alcance=$cases.casos_distintos
        incidencias_vigentes_alcance=$issues
        archivos_previos_fuera_del_alcance=$outside
    }
    $json=$report | ConvertTo-Json -Depth 8
    [IO.File]::WriteAllText((Join-Path $reportDir 'verificacion_final.json'),$json,(New-Object Text.UTF8Encoding($false)))
    $json | Write-Output
}finally{$connection.Dispose()}
