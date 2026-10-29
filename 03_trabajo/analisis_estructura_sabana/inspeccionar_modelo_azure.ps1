$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Data
$azureCli = 'C:\Program Files\Microsoft SDKs\Azure\CLI2\wbin\az.cmd'
$sqlToken = & $azureCli account get-access-token --subscription '84e56f9e-b50d-4e86-9ac1-5baef9df3467' --resource 'https://database.windows.net/' --query accessToken --output tsv
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($sqlToken)) { throw 'No se obtuvo un token para Azure SQL.' }
$sqlConnection = New-Object System.Data.SqlClient.SqlConnection
$sqlConnection.ConnectionString = 'Server=tcp:sql-jnc-notif-dev.database.windows.net,1433;Initial Catalog=sqldb-jnc-notif-dev;Encrypt=True;TrustServerCertificate=False;Connection Timeout=45;Application Name=CodexSchemaReadOnly'
$sqlConnection.AccessToken = $sqlToken.Trim()
$sqlToken = $null
try {
    $sqlConnection.Open()
    $queries = [ordered]@{
        visibility = @'
SELECT DB_NAME() AS database_name,
       HAS_PERMS_BY_NAME(DB_NAME(),'DATABASE','VIEW DEFINITION') AS can_view_definition,
       HAS_PERMS_BY_NAME(DB_NAME(),'DATABASE','SELECT') AS can_select_database,
       IS_MEMBER('db_owner') AS is_db_owner;
'@
        tables = @'
SELECT s.name AS schema_name, t.name AS table_name, t.temporal_type_desc,
       COALESCE(SUM(CASE WHEN p.index_id IN (0,1) THEN p.rows ELSE 0 END),0) AS approximate_rows
FROM sys.tables t JOIN sys.schemas s ON s.schema_id=t.schema_id
LEFT JOIN sys.partitions p ON p.object_id=t.object_id
WHERE t.is_ms_shipped=0 GROUP BY s.name,t.name,t.temporal_type_desc ORDER BY s.name,t.name;
'@
        columns = @'
SELECT s.name AS schema_name,t.name AS table_name,c.column_id,c.name AS column_name,
       ty.name AS data_type,c.max_length,c.precision,c.scale,c.is_nullable,c.is_identity,c.is_computed,
       dc.definition AS default_definition,cc.definition AS computed_definition
FROM sys.tables t JOIN sys.schemas s ON s.schema_id=t.schema_id
JOIN sys.columns c ON c.object_id=t.object_id JOIN sys.types ty ON ty.user_type_id=c.user_type_id
LEFT JOIN sys.default_constraints dc ON dc.object_id=c.default_object_id
LEFT JOIN sys.computed_columns cc ON cc.object_id=c.object_id AND cc.column_id=c.column_id
WHERE t.is_ms_shipped=0 ORDER BY s.name,t.name,c.column_id;
'@
        indexes = @'
SELECT s.name AS schema_name,t.name AS table_name,i.name AS index_name,i.type_desc,
       i.is_unique,i.is_primary_key,i.is_unique_constraint,i.has_filter,i.filter_definition,
       ic.key_ordinal,ic.is_included_column,ic.is_descending_key,c.name AS column_name
FROM sys.tables t JOIN sys.schemas s ON s.schema_id=t.schema_id
JOIN sys.indexes i ON i.object_id=t.object_id AND i.index_id>0
JOIN sys.index_columns ic ON ic.object_id=i.object_id AND ic.index_id=i.index_id
JOIN sys.columns c ON c.object_id=ic.object_id AND c.column_id=ic.column_id
WHERE t.is_ms_shipped=0 ORDER BY s.name,t.name,i.name,ic.is_included_column,ic.key_ordinal;
'@
        foreign_keys = @'
SELECT fk.name AS fk_name,ps.name AS parent_schema,pt.name AS parent_table,pc.name AS parent_column,
       rs.name AS referenced_schema,rt.name AS referenced_table,rc.name AS referenced_column,
       fk.delete_referential_action_desc,fk.update_referential_action_desc,fk.is_disabled,fk.is_not_trusted
FROM sys.foreign_keys fk JOIN sys.foreign_key_columns fkc ON fkc.constraint_object_id=fk.object_id
JOIN sys.tables pt ON pt.object_id=fk.parent_object_id JOIN sys.schemas ps ON ps.schema_id=pt.schema_id
JOIN sys.columns pc ON pc.object_id=pt.object_id AND pc.column_id=fkc.parent_column_id
JOIN sys.tables rt ON rt.object_id=fk.referenced_object_id JOIN sys.schemas rs ON rs.schema_id=rt.schema_id
JOIN sys.columns rc ON rc.object_id=rt.object_id AND rc.column_id=fkc.referenced_column_id
ORDER BY ps.name,pt.name,fk.name,fkc.constraint_column_id;
'@
        modules = @'
SELECT s.name AS schema_name,o.name AS object_name,o.type_desc,sm.definition
FROM sys.objects o JOIN sys.schemas s ON s.schema_id=o.schema_id
LEFT JOIN sys.sql_modules sm ON sm.object_id=o.object_id
WHERE o.type IN ('V','P','FN','IF','TF','TR') AND o.is_ms_shipped=0 ORDER BY s.name,o.type,o.name;
'@
        checks = @'
SELECT s.name AS schema_name,t.name AS table_name,c.name AS constraint_name,c.definition,c.is_disabled,c.is_not_trusted
FROM sys.check_constraints c JOIN sys.tables t ON t.object_id=c.parent_object_id
JOIN sys.schemas s ON s.schema_id=t.schema_id ORDER BY s.name,t.name,c.name;
'@
        storage = @'
SELECT s.name AS schema_name,t.name AS table_name,
       CAST(SUM(a.total_pages)*8.0/1024 AS decimal(18,3)) AS reserved_mb,
       CAST(SUM(a.used_pages)*8.0/1024 AS decimal(18,3)) AS used_mb
FROM sys.tables t JOIN sys.schemas s ON s.schema_id=t.schema_id
JOIN sys.indexes i ON i.object_id=t.object_id JOIN sys.partitions p ON p.object_id=i.object_id AND p.index_id=i.index_id
JOIN sys.allocation_units a ON (a.type IN (1,3) AND a.container_id=p.hobt_id) OR (a.type=2 AND a.container_id=p.partition_id)
WHERE t.is_ms_shipped=0 GROUP BY s.name,t.name ORDER BY used_mb DESC;
'@
    }
    $schemaResult = [ordered]@{}
    foreach ($entry in $queries.GetEnumerator()) {
        $cmd = $sqlConnection.CreateCommand()
        $cmd.CommandText = $entry.Value
        $cmd.CommandTimeout = 30
        $reader = $cmd.ExecuteReader()
        $rows = New-Object System.Collections.Generic.List[object]
        while ($reader.Read()) {
            $row = [ordered]@{}
            for ($columnIndex=0; $columnIndex -lt $reader.FieldCount; $columnIndex++) {
                $value = $reader.GetValue($columnIndex)
                if ($value -is [DBNull]) { $value = $null }
                $row[$reader.GetName($columnIndex)] = $value
            }
            $rows.Add([PSCustomObject]$row)
        }
        $reader.Close()
        $schemaResult[$entry.Key] = @($rows.ToArray())
        $cmd.Dispose()
    }
    $outputPath = Join-Path (Resolve-Path -LiteralPath '03_trabajo\analisis_estructura_sabana').Path 'modelo_sqldb_jnc_notif_dev.json'
    $schemaResult | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath $outputPath -Encoding UTF8
    [PSCustomObject]@{ archivo=$outputPath; tablas=$schemaResult.tables.Count; columnas=$schemaResult.columns.Count; relaciones=$schemaResult.foreign_keys.Count; modulos=$schemaResult.modules.Count } | ConvertTo-Json
} finally {
    $sqlConnection.Close()
    $sqlConnection.Dispose()
}
