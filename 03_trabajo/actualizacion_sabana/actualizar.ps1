param(
    [ValidateSet('Plan','Run','Verify')][string]$Mode='Plan',
    [string]$ReportName='20261027',
    [int]$MaxFiles=0
)
$ErrorActionPreference='Stop'
[Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12
$env:PYTHONIOENCODING='utf-8'
[Console]::OutputEncoding=New-Object Text.UTF8Encoding
Add-Type -AssemblyName System.Net.Http
Add-Type -TypeDefinition @'
public static class SabanaContentChecksum {
    public static string QuickXor(byte[] data) {
        byte[] result = new byte[20];
        int shift = 0;
        foreach (byte value in data) {
            int index = shift / 8, offset = shift % 8;
            result[index] ^= (byte)(value << offset);
            if (offset != 0) result[(index + 1) % 20] ^= (byte)(value >> (8 - offset));
            shift = (shift + 11) % 160;
        }
        byte[] length = System.BitConverter.GetBytes((long)data.Length);
        for (int i = 0; i < 8; i++) result[12 + i] ^= length[i];
        return System.Convert.ToBase64String(result);
    }
}
'@
$utf8=New-Object Text.UTF8Encoding($false)
$subscription='84e56f9e-b50d-4e86-9ac1-5baef9df3467'
$drive='b!BVdcW1M1CUGvabWi-TiRu48Z8tWTR9xJhVeDlno8JlrPYQEct01cSIycOPVx2lWL'
$rootFolder='014EVAVLCI4L3PBJZT6RBIGOTVLXN3J76T'
$endpoint='https://func-jrb-sabana-dev-cjbufggmhqf3ane9.westus2-01.azurewebsites.net/api/consolidar-sabana'
$reportDir=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot $ReportName))
if(-not $reportDir.StartsWith($PSScriptRoot+'\',[StringComparison]::OrdinalIgnoreCase)){throw 'Carpeta de informe fuera del proyecto.'}
New-Item -ItemType Directory -Path $reportDir -Force | Out-Null
$connection=$null
$downloadClient=New-Object Net.Http.HttpClient
$downloadClient.Timeout=[TimeSpan]::FromSeconds(90)
$functionClient=New-Object Net.Http.HttpClient
$functionClient.Timeout=[TimeSpan]::FromSeconds(160)
$script:graphHeaders=$null
$script:graphExpiry=[DateTime]::MinValue

function Write-JsonFile([string]$Name,$Value){
    [IO.File]::WriteAllText((Join-Path $reportDir $Name),($Value | ConvertTo-Json -Depth 12),$utf8)
}
function Refresh-GraphToken {
    $token=az account get-access-token --resource 'https://graph.microsoft.com' --query accessToken --output tsv --only-show-errors
    if($LASTEXITCODE -ne 0 -or -not $token){throw 'No fue posible autenticar Microsoft Graph.'}
    $script:graphHeaders=@{Authorization='Bearer '+$token.Trim();Accept='application/json'}
    $script:graphExpiry=[DateTime]::UtcNow.AddMinutes(40)
    $token=$null
}
function Get-Graph([string]$Url){
    if(-not $Url.StartsWith('https://graph.microsoft.com/v1.0/')){throw 'Endpoint Graph fuera del alcance esperado.'}
    if([DateTime]::UtcNow -gt $script:graphExpiry){Refresh-GraphToken}
    for($retry=0;$retry -lt 4;$retry++){
        try{return Invoke-RestMethod -Method Get -Uri $Url -Headers $script:graphHeaders -TimeoutSec 60}
        catch{
            $status=0
            if($_.Exception.Response){$status=[int]$_.Exception.Response.StatusCode}
            if($status -eq 401 -and $retry -lt 3){Refresh-GraphToken;continue}
            if(($status -in @(0,429,500,502,503,504)) -and $retry -lt 3){Start-Sleep -Seconds ([Math]::Pow(2,$retry+1));continue}
            throw ('GRAPH_HTTP_'+$status)
        }
    }
}
function Read-Sql([string]$Query,[hashtable]$Parameters=@{}){
    if($connection.State -ne [Data.ConnectionState]::Open){$connection.Open()}
    $command=$connection.CreateCommand();$command.CommandText=$Query;$command.CommandTimeout=60
    foreach($key in $Parameters.Keys){$null=$command.Parameters.AddWithValue($key,$Parameters[$key])}
    try{
        $reader=$command.ExecuteReader();$rows=New-Object 'Collections.Generic.List[object]'
        try{
            while($reader.Read()){
                $row=[ordered]@{}
                for($i=0;$i -lt $reader.FieldCount;$i++){
                    $v=if($reader.IsDBNull($i)){$null}else{$reader.GetValue($i)}
                    $row[$reader.GetName($i)]=$v
                }
                $rows.Add([pscustomobject]$row)
            }
        }finally{$reader.Close()}
        return $rows.ToArray()
    }finally{$command.Dispose()}
}
function Get-Snapshot {
    $rows=@(Read-Sql @'
SELECT a.drive_id,a.item_id,a.id_archivo,a.nombre_actual,a.ruta_actual,
 a.version_origen_actual,a.fecha_modificacion_origen,a.id_carga_vigente,
 c.id_perfil,c.estado,c.filas_esperadas,LOWER(CONVERT(varchar(64),c.hash_contenido,2)) AS sha256
FROM ing.archivo a LEFT JOIN ing.carga c ON c.id_carga=a.id_carga_vigente;
'@)
    $map=@{}
    foreach($row in $rows){$map[$row.drive_id+'|'+$row.item_id]=$row}
    return $map
}
function Get-Stats {
    return @(Read-Sql @'
SELECT
 (SELECT COUNT_BIG(*) FROM ing.archivo) AS archivos_registrados,
 (SELECT COUNT_BIG(*) FROM ing.archivo WHERE id_carga_vigente IS NOT NULL) AS archivos_publicados,
 (SELECT COUNT_BIG(*) FROM ing.carga) AS cargas,
 (SELECT COUNT_BIG(*) FROM ing.carga WHERE estado='PUBLICADA') AS cargas_publicadas,
 (SELECT COUNT_BIG(*) FROM ing.fila_origen) AS filas_historicas,
 (SELECT COUNT_BIG(*) FROM rep.vw_consolidado_sabana) AS filas_consolidado,
 (SELECT COUNT_BIG(*) FROM core.caso) AS casos,
 (SELECT COUNT_BIG(*) FROM core.caso_dato) AS versiones_datos_caso,
 (SELECT COUNT_BIG(*) FROM core.entidad) AS entidades,
 (SELECT COUNT_BIG(*) FROM core.notificacion_declarada) AS notificaciones,
 (SELECT COUNT_BIG(*) FROM ing.incidencia WHERE severidad='E' AND resuelta=0) AS errores_activos,
 (SELECT COUNT_BIG(*) FROM ing.incidencia WHERE severidad='W' AND resuelta=0) AS advertencias_activas;
'@)[0]
}
function Get-Path($Metadata){
    return ($Metadata.parentReference.path -replace '^/drives/[^/]+/root:','')+'/'+$Metadata.name
}
function Is-Current($Metadata,$Published){
    if(-not $Published -or $Published.estado -ne 'PUBLICADA' -or $Published.id_perfil -ne 1){return $false}
    return $Published.version_origen_actual -ceq $Metadata.eTag -and
        $Published.nombre_actual -ceq $Metadata.name -and
        $Published.ruta_actual -ceq (Get-Path $Metadata) -and
        $Published.fecha_modificacion_origen.Ticks -eq ([DateTimeOffset]::Parse($Metadata.lastModifiedDateTime)).UtcDateTime.Ticks
}
function Get-Inventory {
    $files=New-Object 'Collections.Generic.List[object]'
    $ignored=New-Object 'Collections.Generic.List[object]'
    function Walk([string]$Folder,[int]$Room){
        $url='https://graph.microsoft.com/v1.0/drives/'+$drive+'/items/'+$Folder+'/children?$top=200'
        while($url){
            $page=Get-Graph $url
            foreach($item in $page.value){
                if($item.folder){Walk $item.id $Room}
                elseif($item.file -and $item.name -match '(?i)\.(xlsx|xlsm)$' -and -not $item.name.StartsWith('~$')){
                    $item | Add-Member -NotePropertyName sala_numero -NotePropertyValue $Room
                    $files.Add($item)
                }else{$ignored.Add([pscustomobject]@{nombre=$item.name;motivo='No es Excel de entrada'})}
            }
            $url=$page.'@odata.nextLink'
        }
    }
    $root=Get-Graph ('https://graph.microsoft.com/v1.0/drives/'+$drive+'/items/'+$rootFolder+'/children?$top=200')
    foreach($item in $root.value){
        if($item.folder -and $item.name -match '^Sala ([123])$'){Walk $item.id ([int]$Matches[1])}
        else{$ignored.Add([pscustomobject]@{nombre=$item.name;motivo='Fuera de las salas de entrada'})}
    }
    return [pscustomobject]@{files=$files.ToArray();ignored=$ignored.ToArray()}
}
function Public-Item($Item){
    return [pscustomobject]@{drive_id=$drive;item_id=$Item.id;nombre=$Item.name;sala=$Item.sala_numero;ruta=(Get-Path $Item);version=$Item.eTag;modificado=$Item.lastModifiedDateTime;bytes=$Item.size}
}
function Send-File($Item){
    $meta=$Item
    $byteSource='descarga_actual'
    for($downloadAttempt=0;$downloadAttempt -lt 3;$downloadAttempt++){
        $localRoot=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot ('..\analisis_estructura_sabana\inputs\Sala '+$Item.sala_numero)))
        $localPath=[IO.Path]::GetFullPath((Join-Path $localRoot $meta.name))
        if($localPath.StartsWith($localRoot+'\',[StringComparison]::OrdinalIgnoreCase) -and (Test-Path -LiteralPath $localPath)){
            $localBytes=[IO.File]::ReadAllBytes($localPath)
            $localChecksum=[SabanaContentChecksum]::QuickXor($localBytes)
            if($localBytes.Length -eq $meta.size -and $localChecksum -ceq $meta.file.hashes.quickXorHash){
                $after=Get-Graph ('https://graph.microsoft.com/v1.0/drives/'+$drive+'/items/'+$Item.id)
                if($localBytes.Length -eq $after.size -and $localChecksum -ceq $after.file.hashes.quickXorHash){
                    $bytes=$localBytes;$meta=$after;$byteSource='copia_local_verificada_quickxor_y_tamano';break
                }
                $meta=$after
                continue
            }
        }
        $download=$meta.'@microsoft.graph.downloadUrl'
        if(-not $download){$meta=Get-Graph ('https://graph.microsoft.com/v1.0/drives/'+$drive+'/items/'+$Item.id);$download=$meta.'@microsoft.graph.downloadUrl'}
        if(-not $download){throw 'DESCARGA_SIN_REFERENCIA'}
        try{$bytes=$downloadClient.GetByteArrayAsync($download).GetAwaiter().GetResult()}
        catch{
            if($downloadAttempt -lt 2){$meta=Get-Graph ('https://graph.microsoft.com/v1.0/drives/'+$drive+'/items/'+$Item.id);continue}
            throw 'DESCARGA_NO_DISPONIBLE'
        }
        $after=Get-Graph ('https://graph.microsoft.com/v1.0/drives/'+$drive+'/items/'+$Item.id)
        if($meta.eTag -ceq $after.eTag -and $bytes.Length -eq $after.size){$meta=$after;break}
        if($downloadAttempt -eq 2){throw 'ARCHIVO_CAMBIA_DURANTE_LECTURA'}
        $meta=$after
    }
    if($bytes.Length -gt 10485760){throw 'ARCHIVO_SUPERA_10_MIB'}
    $hashProvider=[Security.Cryptography.SHA256]::Create()
    try{$hash=[BitConverter]::ToString($hashProvider.ComputeHash($bytes)).Replace('-','').ToLowerInvariant()}finally{$hashProvider.Dispose()}
    $payload=@{drive_id=$drive;item_id=$meta.id;nombre_archivo=$meta.name;ruta_sharepoint=(Get-Path $meta);version_origen=$meta.eTag;fecha_modificacion_origen=$meta.lastModifiedDateTime;sala=[int]$Item.sala_numero;id_perfil=1;contenido_base64=[Convert]::ToBase64String($bytes)} | ConvertTo-Json -Compress
    $bytes=$null
    $watch=[Diagnostics.Stopwatch]::StartNew()
    for($retry=0;$retry -lt 3;$retry++){
        $content=New-Object Net.Http.StringContent($payload,[Text.Encoding]::UTF8,'application/json')
        $response=$null
        try{
            $response=$functionClient.PostAsync($endpoint,$content).GetAwaiter().GetResult()
            $status=[int]$response.StatusCode
            $text=$response.Content.ReadAsStringAsync().GetAwaiter().GetResult()
            try{$result=$text | ConvertFrom-Json}catch{$result=[pscustomobject]@{codigo='RESPUESTA_NO_JSON'}}
        }catch{$status=0;$result=[pscustomobject]@{codigo='HTTP_SIN_RESPUESTA'}}
        finally{if($response){$response.Dispose()};$content.Dispose()}
        if($status -notin @(0,408,429,500,502,503,504) -or $retry -eq 2){break}
        Start-Sleep -Seconds ([Math]::Pow(2,$retry+2))
    }
    $watch.Stop();$payload=$null
    $entry=[ordered]@{drive_id=$drive;item_id=$meta.id;nombre=$meta.name;sala=$Item.sala_numero;version=$meta.eTag;modificado=$meta.lastModifiedDateTime;origen_bytes=$byteSource;sha256=$hash;http=$status;segundos=[Math]::Round($watch.Elapsed.TotalSeconds,3);codigo=$result.codigo;estado=$result.estado;id_carga=$result.id_carga;filas=$result.filas_procesadas;reutilizada=$result.reutilizada;advertencias=$result.advertencias;correlacion=$result.correlacion;codigos_incidencias=@($result.incidencias | ForEach-Object {$_.codigo} | Sort-Object -Unique);verificado_sql=$false}
    if($status -eq 200){
        $check=@(Read-Sql @'
SELECT c.id_carga,c.estado,c.filas_esperadas,LOWER(CONVERT(varchar(64),c.hash_contenido,2)) AS sha256,
 (SELECT COUNT_BIG(*) FROM ing.fila_origen f WHERE f.id_carga=c.id_carga) AS filas
FROM ing.archivo a JOIN ing.carga c ON c.id_carga=a.id_carga_vigente
WHERE a.drive_id=@drive AND a.item_id=@item;
'@ @{'@drive'=$drive;'@item'=$meta.id})
        $entry.verificado_sql=$check.Count -eq 1 -and $check[0].estado -eq 'PUBLICADA' -and $check[0].sha256 -ceq $hash -and $check[0].filas -eq $result.filas_procesadas -and $check[0].filas_esperadas -eq $result.filas_procesadas
        if(-not $entry.verificado_sql){$entry.codigo='VERIFICACION_SQL_NO_COINCIDE'}
    }
    return [pscustomobject]$entry
}

try{
    Refresh-GraphToken
    $token=az account get-access-token --resource 'https://database.windows.net/' --query accessToken --output tsv --only-show-errors
    if($LASTEXITCODE -ne 0){throw 'No fue posible autenticar SQL.'}
    $connection=New-Object Data.SqlClient.SqlConnection('Server=tcp:sql-jrb-sabana-dev.database.windows.net,1433;Database=sqldb-jrb-sabana-dev;Encrypt=True;TrustServerCertificate=False;Connection Timeout=30;Application Name=ActualizacionSabana;')
    $connection.AccessToken=$token.Trim();$token=$null;$connection.Open()
    $before=Get-Stats
    $snapshot=Get-Snapshot
    $inventory=Get-Inventory
    $pending=@($inventory.files | Where-Object {-not (Is-Current $_ $snapshot[$drive+'|'+$_.id])} | Sort-Object sala_numero,name)
    $plan=[ordered]@{fecha_utc=[DateTime]::UtcNow.ToString('o');excel_actuales=$inventory.files.Count;ya_actualizados=$inventory.files.Count-$pending.Count;pendientes=$pending.Count;por_sala=@($inventory.files | Group-Object sala_numero | ForEach-Object {[pscustomobject]@{sala=$_.Name;archivos=$_.Count}});omitidos=$inventory.ignored;antes=$before}
    Write-JsonFile ('plan_'+$Mode.ToLower()+'.json') $plan
    Write-JsonFile ('inventario_'+$Mode.ToLower()+'.json') @($inventory.files | ForEach-Object {Public-Item $_})
    $plan | ConvertTo-Json -Depth 6 -Compress | Write-Output
    if($Mode -ne 'Run'){
        Write-JsonFile ('pendientes_'+$Mode.ToLower()+'.json') @($pending | ForEach-Object {Public-Item $_})
        exit 0
    }
    $keys=az functionapp function keys list --subscription $subscription --resource-group 'rg-jrb-sabana-dev' --name 'func-jrb-sabana-dev' --function-name 'ConsolidarSabana' --output json --only-show-errors | ConvertFrom-Json
    if($LASTEXITCODE -ne 0 -or -not $keys.default){throw 'No fue posible obtener la clave de la Function.'}
    $null=$functionClient.DefaultRequestHeaders.TryAddWithoutValidation('x-functions-key',$keys.default)
    $keys=$null
    $results=New-Object 'Collections.Generic.List[object]'
    $processed=0;$published=0;$failed=0;$rows=0;$lastProgress=[DateTime]::UtcNow
    $lastSnapshot=[DateTime]::UtcNow
    foreach($item in $pending){
        if($MaxFiles -gt 0 -and $processed -ge $MaxFiles){break}
        if(([DateTime]::UtcNow-$lastSnapshot).TotalMinutes -gt 35){
            $connection.Close()
            $renewedToken=az account get-access-token --resource 'https://database.windows.net/' --query accessToken --output tsv --only-show-errors
            if($LASTEXITCODE -ne 0){throw 'No fue posible renovar el token SQL; los resultados previos permiten reanudar.'}
            $connection.AccessToken=$renewedToken.Trim();$renewedToken=$null;$connection.Open()
            $lastSnapshot=[DateTime]::UtcNow
        }
        $processed++
        try{$entry=Send-File $item}
        catch{
            $message=$_.Exception.Message -replace 'https?://\S+','<URL>'
            if($message.Length -gt 350){$message=$message.Substring(0,350)}
            $entry=[pscustomobject]@{drive_id=$drive;item_id=$item.id;nombre=$item.name;sala=$item.sala_numero;http=0;codigo='ERROR_CLIENTE';tipo=$_.Exception.GetType().Name;detalle_tecnico=$message;verificado_sql=$false}
            Write-Output ('DIAGNOSTICO_CLIENTE: '+$message)
        }
        $results.Add($entry)
        [IO.File]::AppendAllText((Join-Path $reportDir 'resultados.jsonl'),(($entry | ConvertTo-Json -Depth 6 -Compress)+[Environment]::NewLine),$utf8)
        if($entry.verificado_sql){$published++;$rows+=[long]$entry.filas}else{$failed++}
        if($processed%10 -eq 0 -or ([DateTime]::UtcNow-$lastProgress).TotalSeconds -ge 30 -or -not $entry.verificado_sql){
            [pscustomobject]@{procesados=$processed;total=$pending.Count;publicados=$published;fallidos=$failed;filas_confirmadas=$rows;ultimo=$entry.nombre;http=$entry.http;codigo=$entry.codigo} | ConvertTo-Json -Compress | Write-Output
            $lastProgress=[DateTime]::UtcNow
        }
        if($entry.http -in @(401,403)){throw 'La autenticacion de la Function fallo; se detiene para no repetir fallos.'}
        if($failed -ge 3 -and $published -eq 0){throw 'Tres fallos iniciales; revisar antes de continuar.'}
    }
    $finalInventory=Get-Inventory
    $finalSnapshot=Get-Snapshot
    $remaining=@($finalInventory.files | Where-Object {-not (Is-Current $_ $finalSnapshot[$drive+'|'+$_.id])})
    $summary=[ordered]@{fecha_final_utc=[DateTime]::UtcNow.ToString('o');excel_iniciales=$inventory.files.Count;ya_actualizados_iniciales=$inventory.files.Count-$pending.Count;procesados=$processed;publicados_verificados=$published;fallidos=$failed;filas_confirmadas=$rows;excel_actuales_finales=$finalInventory.files.Count;pendientes_finales=$remaining.Count;despues=(Get-Stats)}
    Write-JsonFile 'resumen.json' $summary
    Write-JsonFile 'pendientes_finales.json' @($remaining | ForEach-Object {Public-Item $_})
    $summary | ConvertTo-Json -Depth 7 -Compress | Write-Output
}finally{
    if($connection){$connection.Dispose()}
    $downloadClient.Dispose();$functionClient.Dispose();$script:graphHeaders=$null
}
