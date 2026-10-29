param(
    [Parameter(Mandatory=$true)][string]$Archivo,
    [Parameter(Mandatory=$true)][uri]$Endpoint,
    [Parameter(Mandatory=$true)][string]$DriveId,
    [Parameter(Mandatory=$true)][string]$ItemId,
    [Parameter(Mandatory=$true)][string]$RutaSharePoint,
    [Parameter(Mandatory=$true)][string]$VersionOrigen,
    [Parameter(Mandatory=$true)][datetimeoffset]$ModificadoOrigen,
    [Parameter(Mandatory=$true)][ValidateRange(1,99)][int]$Sala,
    [int]$IdPerfil = 1
)
$ErrorActionPreference = 'Stop'
$inputFile = Get-Item -LiteralPath $Archivo
$headers = @{}
if ($env:SABANA_FUNCTION_KEY) { $headers['x-functions-key'] = $env:SABANA_FUNCTION_KEY }
if ($Endpoint.Host -notin @('localhost','127.0.0.1') -and $Endpoint.Scheme -ne 'https') {
    throw 'Use HTTPS para enviar el archivo a un endpoint remoto.'
}
$body = @{
    drive_id = $DriveId
    item_id = $ItemId
    nombre_archivo = $inputFile.Name
    ruta_sharepoint = $RutaSharePoint
    version_origen = $VersionOrigen
    fecha_modificacion_origen = $ModificadoOrigen.ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ss.fffffffZ')
    sala = $Sala
    id_perfil = $IdPerfil
    contenido_base64 = [Convert]::ToBase64String([IO.File]::ReadAllBytes($inputFile.FullName))
} | ConvertTo-Json -Depth 3 -Compress
Invoke-RestMethod -Method Post -Uri $Endpoint -Headers $headers `
    -ContentType 'application/json; charset=utf-8' `
    -Body ([Text.Encoding]::UTF8.GetBytes($body)) -TimeoutSec 120
