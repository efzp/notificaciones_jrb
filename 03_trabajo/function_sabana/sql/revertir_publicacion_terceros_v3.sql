/* Reversion solicitada: volver del corte 6 al corte 4.
   Mantiene las evidencias y la migracion 11 como historico inactivo.
   Reejecucion permitida si el corte 4 ya esta publicado. */
SET NOCOUNT ON;
SET XACT_ABORT ON;
IF DB_NAME()<>N'sqldb-jrb-sabana-dev' THROW 51830,N'Base incorrecta.',1;
BEGIN TRY
 BEGIN TRANSACTION;
 DECLARE @lock int,@actual int;
 EXEC @lock=sys.sp_getapplock @Resource=N'terceros:carga',@LockMode='Exclusive',@LockOwner='Transaction',@LockTimeout=10000;
 IF @lock<0 THROW 51831,N'Carga ocupada.',1;
 SELECT @actual=id_carga_terceros FROM ing.terceros_publicacion WITH(UPDLOCK,HOLDLOCK) WHERE id=1;
 IF @actual IS NULL OR @actual NOT IN(4,6) THROW 51832,N'La publicacion cambio; no se revierte automaticamente.',1;
 IF NOT EXISTS(SELECT 1 FROM ing.terceros_carga WHERE id_carga_terceros=4 AND version_regla='terceros-v2'
  AND sha256=0x36ea8b55c2a47b5c41e702a5bb74ab523a3a80cf0ca29feeb1f2de29a137bf5f
  AND asociaciones=1393 AND pendientes=12568) THROW 51833,N'El corte anterior no coincide.',1;
 IF @actual=6 AND NOT EXISTS(SELECT 1 FROM ing.terceros_carga WHERE id_carga_terceros=6
  AND sha256=0xd1f45ab8a5826394206fcf72b09d43542b868b0241e299b4b1d69f58c9872d1c)
  THROW 51834,N'El corte a revertir no coincide.',1;
 UPDATE ing.terceros_publicacion SET id_carga_terceros=4 WHERE id=1;
 IF (SELECT COUNT_BIG(*) FROM rep.vw_revision_entidad_correo)<>1393
  THROW 51835,N'Conteo del maestro restaurado incorrecto.',1;
 IF (SELECT COUNT_BIG(*) FROM ing.terceros_pendiente WHERE id_carga_terceros=4)<>12568
  THROW 51836,N'Conteo de pendientes restaurado incorrecto.',1;
 COMMIT TRANSACTION;
END TRY
BEGIN CATCH
 IF XACT_STATE()<>0 ROLLBACK TRANSACTION;
 THROW;
END CATCH;
