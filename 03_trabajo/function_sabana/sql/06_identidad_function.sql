/* Ejecutar como administrador Microsoft Entra de la base nueva,
   DESPUES de crear la Function y activar su identidad administrada.
   Reemplazar el nombre por el nombre real de esa identidad.
   No otorga db_owner ni configura acceso de red. */
SET NOCOUNT ON;
IF DB_NAME() <> N'sqldb-jrb-sabana-dev'
    THROW 51500, N'Use sqldb-jrb-sabana-dev.', 1;
DECLARE @identidad sysname = N'REEMPLAZAR_NOMBRE_IDENTIDAD_FUNCTION';
DECLARE @sql nvarchar(max);
IF @identidad = N'REEMPLAZAR_NOMBRE_IDENTIDAD_FUNCTION'
    THROW 51501, N'Indique el nombre real de la identidad administrada.', 1;
IF DATABASE_PRINCIPAL_ID(N'rol_carga_sabana') IS NULL
    THROW 51502, N'Primero ejecute 04_publicacion.sql.', 1;
IF DATABASE_PRINCIPAL_ID(@identidad) IS NULL
BEGIN
    SET @sql = N'CREATE USER ' + QUOTENAME(@identidad) + N' FROM EXTERNAL PROVIDER;';
    EXEC sys.sp_executesql @sql;
END;
IF NOT EXISTS (
    SELECT 1 FROM sys.database_role_members AS rm
    JOIN sys.database_principals AS r ON r.principal_id=rm.role_principal_id
    JOIN sys.database_principals AS m ON m.principal_id=rm.member_principal_id
    WHERE r.name=N'rol_carga_sabana' AND m.name=@identidad
)
BEGIN
    SET @sql = N'ALTER ROLE rol_carga_sabana ADD MEMBER ' + QUOTENAME(@identidad) + N';';
    EXEC sys.sp_executesql @sql;
END;
