/* Solo para instalaciones con cfg.campo_entrada.tipo_dato varchar(12).
   Ejecutar antes de reintentar 02_catalogos.sql. No recrea ni elimina tablas.
   Las instalaciones nuevas con 01_tablas.sql corregido no requieren este ajuste. */
SET NOCOUNT ON;
SET XACT_ABORT ON;
IF DB_NAME() IN (N'master', N'tempdb', N'model', N'msdb', N'sqldb-jnc-notif-dev')
    THROW 51000, N'Utilice la base exclusiva del nuevo proyecto.', 1;
IF OBJECT_ID(N'cfg.campo_entrada', N'U') IS NULL
    THROW 51003, N'No existe cfg.campo_entrada. Ejecute primero 01_tablas.sql.', 1;
BEGIN TRY
    BEGIN TRANSACTION;
    IF COL_LENGTH(N'cfg.campo_entrada', N'tipo_dato') < 16
        ALTER TABLE cfg.campo_entrada ALTER COLUMN tipo_dato varchar(16) NOT NULL;
    COMMIT TRANSACTION;
END TRY
BEGIN CATCH
    IF XACT_STATE() <> 0 ROLLBACK TRANSACTION;
    THROW;
END CATCH;
SELECT COL_LENGTH(N'cfg.campo_entrada', N'tipo_dato') AS capacidad_tipo_dato;
