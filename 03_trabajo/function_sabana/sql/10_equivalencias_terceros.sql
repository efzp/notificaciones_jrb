/* Agrupacion propuesta por regex/proximidad. Conserva entidades originales.
   Ejecutar con tools/cargar_terceros.py migrar --aplicar. */
SET NOCOUNT ON;
SET XACT_ABORT ON;
IF DB_NAME()<>N'sqldb-jrb-sabana-dev' THROW 51810,N'Base incorrecta.',1;
IF OBJECT_ID(N'cfg.migracion_terceros',N'U') IS NULL THROW 51811,N'Falta migracion 09.',1;
DECLARE @hash binary(32)=TRY_CONVERT(binary(32),SESSION_CONTEXT(N'terceros_migration_hash'));
IF @hash IS NULL THROW 51812,N'Utilice el ejecutor de migraciones.',1;
BEGIN TRY
 BEGIN TRANSACTION;
 DECLARE @lock int;
 EXEC @lock=sys.sp_getapplock @Resource=N'terceros:migracion',@LockMode='Exclusive',@LockOwner='Transaction',@LockTimeout=10000;
 IF @lock<0 THROW 51813,N'Migracion ocupada.',1;
 IF EXISTS(SELECT 1 FROM cfg.migracion_terceros WHERE version=2 AND sha256<>@hash)
  THROW 51814,N'La migracion aplicada tiene otra huella.',1;
 IF EXISTS(SELECT 1 FROM cfg.migracion_terceros WHERE version=2)
 BEGIN
  COMMIT TRANSACTION;
  RETURN;
 END;
 CREATE TABLE ref.entidad_equivalencia (
  id_carga_terceros int NOT NULL REFERENCES ing.terceros_carga(id_carga_terceros),
  hash_clave binary(32) NOT NULL,
  origen varchar(3) NOT NULL CHECK(origen IN ('JRB','JNC')),
  tipo_parte varchar(20) NOT NULL CHECK(tipo_parte IN ('arl','afp','eps','aseguradora','ultimoempleador','remitente','regional')),
  nombre_origen nvarchar(500) NOT NULL,
  id_entidad_origen int NULL REFERENCES core.entidad(id_entidad),
  id_entidad_representante int NULL REFERENCES core.entidad(id_entidad),
  id_entidad_candidata int NULL REFERENCES core.entidad(id_entidad),
  metodo varchar(20) NOT NULL CHECK(metodo IN ('EXACTA','REGEX','PROXIMIDAD','REVISION','AMBIGUA','SIN_EQUIVALENCIA')),
  puntaje decimal(5,2) NOT NULL CHECK(puntaje BETWEEN 0 AND 100),
  segundo_puntaje decimal(5,2) NOT NULL CHECK(segundo_puntaje BETWEEN 0 AND 100),
  umbral decimal(5,2) NOT NULL CHECK(umbral BETWEEN 90 AND 100),
  margen decimal(5,2) NOT NULL CHECK(margen>0 AND margen<=20),
  version_normalizacion varchar(50) NOT NULL,
  CONSTRAINT PK_entidad_equivalencia PRIMARY KEY(id_carga_terceros,hash_clave),
  CONSTRAINT CK_entidad_equivalencia_estado CHECK(
   (metodo IN ('EXACTA','REGEX','PROXIMIDAD') AND id_entidad_representante IS NOT NULL AND puntaje>=umbral)
   OR (metodo IN ('REVISION','AMBIGUA','SIN_EQUIVALENCIA') AND id_entidad_representante IS NULL))
 );
 CREATE INDEX IX_entidad_equivalencia_canonica ON ref.entidad_equivalencia(id_carga_terceros,id_entidad_representante,tipo_parte);
 EXEC(N'CREATE VIEW rep.vw_equivalencia_entidad AS
 SELECT x.*,e.nombre AS entidad_representante,c.nombre AS entidad_candidata
 FROM ing.terceros_publicacion p
 JOIN ref.entidad_equivalencia x ON x.id_carga_terceros=p.id_carga_terceros
 LEFT JOIN core.entidad e ON e.id_entidad=x.id_entidad_representante
 LEFT JOIN core.entidad c ON c.id_entidad=x.id_entidad_candidata;');
 GRANT SELECT,INSERT ON OBJECT::ref.entidad_equivalencia TO rol_carga_terceros;
 GRANT SELECT ON OBJECT::rep.vw_equivalencia_entidad TO rol_carga_terceros;
 GRANT SELECT ON OBJECT::rep.vw_equivalencia_entidad TO rol_revision_terceros;
 INSERT cfg.migracion_terceros(version,sha256) VALUES(2,@hash);
 COMMIT TRANSACTION;
END TRY
BEGIN CATCH
 IF XACT_STATE()<>0 ROLLBACK TRANSACTION;
 THROW;
END CATCH;
