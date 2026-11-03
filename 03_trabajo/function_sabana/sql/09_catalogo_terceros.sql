/* Aplicar con tools/cargar_terceros.py migrar. Solo base JRB; transaccion atomica.
   Pacientes excluidos. Los respaldos JNC son posteriores, nunca autorizaciones.
   El ejecutor proporciona la huella del script mediante SESSION_CONTEXT. */
SET NOCOUNT ON;
SET XACT_ABORT ON;
IF DB_NAME()<>N'sqldb-jrb-sabana-dev' THROW 51800,N'Base incorrecta.',1;
IF OBJECT_ID(N'core.entidad',N'U') IS NULL OR OBJECT_ID(N'mail.direccion',N'U') IS NULL
 THROW 51801,N'Falta el modelo Sabana/PST.',1;
DECLARE @hash binary(32)=TRY_CONVERT(binary(32),SESSION_CONTEXT(N'terceros_migration_hash'));
IF @hash IS NULL THROW 51802,N'Utilice el ejecutor de migraciones.',1;
BEGIN TRY
 BEGIN TRANSACTION;
 DECLARE @lock int;
 EXEC @lock=sys.sp_getapplock @Resource=N'terceros:migracion',@LockMode='Exclusive',@LockOwner='Transaction',@LockTimeout=10000;
 IF @lock<0 THROW 51803,N'Migracion ocupada.',1;
 IF OBJECT_ID(N'cfg.migracion_terceros',N'U') IS NOT NULL
 BEGIN
  IF EXISTS(SELECT 1 FROM cfg.migracion_terceros WHERE version=1 AND sha256<>@hash)
   THROW 51804,N'La migracion aplicada tiene otra huella.',1;
  IF EXISTS(SELECT 1 FROM cfg.migracion_terceros WHERE version=1)
  BEGIN
   COMMIT TRANSACTION;
   RETURN;
  END;
 END;
 IF SCHEMA_ID(N'ref') IS NULL EXEC(N'CREATE SCHEMA ref AUTHORIZATION dbo;');
 IF OBJECT_ID(N'cfg.migracion_terceros',N'U') IS NULL
  CREATE TABLE cfg.migracion_terceros (
   version int NOT NULL PRIMARY KEY,
   sha256 binary(32) NOT NULL,
   aplicado_utc datetime2(3) NOT NULL DEFAULT SYSUTCDATETIME()
  );

 CREATE TABLE ing.terceros_carga (
  id_carga_terceros int IDENTITY NOT NULL CONSTRAINT PK_terceros_carga PRIMARY KEY,
  sha256 binary(32) NOT NULL CONSTRAINT UQ_terceros_carga_hash UNIQUE,
  version_regla varchar(50) NOT NULL,
  capturado_utc datetime2(3) NOT NULL,
  aplicado_utc datetime2(3) NOT NULL DEFAULT SYSUTCDATETIME(),
  asociaciones int NOT NULL,
  pendientes int NOT NULL,
  CONSTRAINT CK_terceros_carga_conteos CHECK(asociaciones>=0 AND pendientes>=0)
 );
 CREATE TABLE ing.terceros_publicacion (
  id tinyint NOT NULL CONSTRAINT PK_terceros_publicacion PRIMARY KEY CHECK(id=1),
  id_carga_terceros int NOT NULL REFERENCES ing.terceros_carga(id_carga_terceros)
 );
 CREATE TABLE ref.entidad_correo (
  id_entidad_correo int IDENTITY NOT NULL CONSTRAINT PK_entidad_correo PRIMARY KEY,
  id_entidad int NOT NULL REFERENCES core.entidad(id_entidad),
  tipo_parte varchar(20) NOT NULL,
  id_direccion int NOT NULL REFERENCES mail.direccion(id_direccion),
  creado_utc datetime2(3) NOT NULL DEFAULT SYSUTCDATETIME(),
  CONSTRAINT UQ_entidad_correo UNIQUE(id_entidad,tipo_parte,id_direccion),
  CONSTRAINT CK_entidad_correo_rol CHECK(tipo_parte IN ('arl','afp','eps','aseguradora','ultimoempleador','remitente','regional'))
 );
 CREATE TABLE ref.entidad_correo_fuente (
  id_carga_terceros int NOT NULL REFERENCES ing.terceros_carga(id_carga_terceros),
  id_entidad_correo int NOT NULL REFERENCES ref.entidad_correo(id_entidad_correo),
  origen varchar(10) NOT NULL CHECK(origen IN ('JNC','JRB_PST')),
  casos_distintos int NOT NULL CHECK(casos_distintos>0),
  casos_entidad_con_correo int NOT NULL,
  observaciones int NOT NULL,
  casos_certificado_exacto int NOT NULL,
  primera_observacion date NULL,
  ultima_observacion date NULL,
  hash_soporte binary(32) NOT NULL,
  referencia_muestra nvarchar(200) NOT NULL,
  detalle_fuentes nvarchar(1000) NOT NULL CHECK(ISJSON(detalle_fuentes)=1),
  CONSTRAINT PK_entidad_correo_fuente PRIMARY KEY(id_carga_terceros,id_entidad_correo,origen),
  CONSTRAINT CK_entidad_correo_fuente_conteos CHECK(observaciones>=casos_distintos AND casos_entidad_con_correo>=casos_distintos AND casos_certificado_exacto BETWEEN 0 AND casos_distintos),
  CONSTRAINT CK_entidad_correo_fuente_fechas CHECK((primera_observacion IS NULL AND ultima_observacion IS NULL)
    OR (primera_observacion IS NOT NULL AND ultima_observacion IS NOT NULL AND primera_observacion<=ultima_observacion))
 );
 CREATE TABLE ing.terceros_pendiente (
  id_pendiente bigint IDENTITY NOT NULL CONSTRAINT PK_terceros_pendiente PRIMARY KEY,
  id_carga_terceros int NOT NULL REFERENCES ing.terceros_carga(id_carga_terceros),
  origen varchar(10) NOT NULL CHECK(origen IN ('JNC','JRB_PST')),
  tipo_parte varchar(20) NOT NULL CHECK(tipo_parte IN ('arl','afp','eps','aseguradora','ultimoempleador','remitente','regional')),
  nombre_origen nvarchar(500) NOT NULL,
  correo_origen nvarchar(1024) NOT NULL,
  codigo varchar(50) NOT NULL,
  ocurrencias int NOT NULL CHECK(ocurrencias>0),
  referencia nvarchar(200) NOT NULL,
  hash_soporte binary(32) NOT NULL
 );
 CREATE INDEX IX_terceros_pendiente_carga ON ing.terceros_pendiente(id_carga_terceros,codigo);
 CREATE TABLE ref.entidad_correo_revision (
  id_revision bigint IDENTITY NOT NULL CONSTRAINT PK_entidad_correo_revision PRIMARY KEY,
  id_entidad_correo int NOT NULL REFERENCES ref.entidad_correo(id_entidad_correo),
  id_carga_terceros int NOT NULL REFERENCES ing.terceros_carga(id_carga_terceros),
  origen_soporte varchar(10) NOT NULL CHECK(origen_soporte IN ('JNC','JRB_PST')),
  decision varchar(15) NOT NULL CHECK(decision IN ('ACEPTADA','RECHAZADA','PENDIENTE')),
  aplica_desde date NOT NULL,
  aplica_hasta date NULL,
  motivo nvarchar(1000) NOT NULL CHECK(LEN(LTRIM(RTRIM(motivo)))>0),
  referencia_evidencia nvarchar(1000) NOT NULL CHECK(LEN(LTRIM(RTRIM(referencia_evidencia)))>0),
  revisado_por nvarchar(255) NOT NULL CHECK(LEN(LTRIM(RTRIM(revisado_por)))>0),
  revisado_utc datetime2(3) NOT NULL DEFAULT SYSUTCDATETIME(),
  CONSTRAINT CK_entidad_correo_revision_fechas CHECK(aplica_hasta IS NULL OR aplica_hasta>=aplica_desde),
  CONSTRAINT FK_entidad_correo_revision_soporte FOREIGN KEY(id_carga_terceros,id_entidad_correo,origen_soporte)
    REFERENCES ref.entidad_correo_fuente(id_carga_terceros,id_entidad_correo,origen)
 );
 CREATE INDEX IX_entidad_correo_revision ON ref.entidad_correo_revision(id_entidad_correo,id_revision DESC);
 EXEC(N'CREATE VIEW rep.vw_revision_entidad_correo AS
 WITH f AS (
 SELECT e.id_entidad_correo,e.id_entidad,e.tipo_parte,e.id_direccion,p.id_carga_terceros,
  MAX(CASE WHEN x.origen=''JRB_PST'' THEN x.casos_distintos ELSE 0 END) casos_jrb,
  MAX(CASE WHEN x.origen=''JNC'' THEN x.casos_distintos ELSE 0 END) casos_jnc,
  CAST(100.0*MAX(CASE WHEN x.origen=''JRB_PST'' THEN x.casos_distintos END)
       /NULLIF(MAX(CASE WHEN x.origen=''JRB_PST'' THEN x.casos_entidad_con_correo END),0) AS decimal(6,2)) porcentaje_casos_jrb,
  CAST(100.0*MAX(CASE WHEN x.origen=''JNC'' THEN x.casos_distintos END)
       /NULLIF(MAX(CASE WHEN x.origen=''JNC'' THEN x.casos_entidad_con_correo END),0) AS decimal(6,2)) porcentaje_casos_jnc,
  MAX(CASE WHEN x.origen=''JNC'' THEN x.casos_certificado_exacto ELSE 0 END) casos_jnc_certificado_exacto,
  MIN(CASE WHEN x.origen=''JRB_PST'' THEN x.primera_observacion END) primera_observacion_jrb,
  MAX(CASE WHEN x.origen=''JRB_PST'' THEN x.ultima_observacion END) ultima_observacion_jrb,
  MIN(CASE WHEN x.origen=''JNC'' THEN x.primera_observacion END) primera_observacion_jnc,
  MAX(CASE WHEN x.origen=''JNC'' THEN x.ultima_observacion END) ultima_observacion_jnc
 FROM ing.terceros_publicacion p
 JOIN ref.entidad_correo_fuente x ON x.id_carga_terceros=p.id_carga_terceros
 JOIN ref.entidad_correo e ON e.id_entidad_correo=x.id_entidad_correo
 GROUP BY e.id_entidad_correo,e.id_entidad,e.tipo_parte,e.id_direccion,p.id_carga_terceros
 ) SELECT f.*,n.nombre AS entidad,d.direccion AS correo,
 CASE WHEN casos_jrb>0 AND casos_jnc>0 THEN ''COINCIDE_JRB_JNC''
      WHEN casos_jrb>0 AND EXISTS(SELECT 1 FROM f b WHERE b.id_entidad=f.id_entidad AND b.tipo_parte=f.tipo_parte AND b.casos_jnc>0)
       THEN ''DIFIERE_DE_JNC''
      WHEN casos_jrb>0 THEN ''SOLO_OBSERVADO_JRB'' ELSE ''SOLO_REFERENCIA_JNC'' END resultado_comparacion,
 r.decision AS ultima_decision_registrada,r.aplica_desde,r.aplica_hasta,r.revisado_por,r.revisado_utc,
 c.capturado_utc,c.version_regla
 FROM f JOIN core.entidad n ON n.id_entidad=f.id_entidad
 JOIN mail.direccion d ON d.id_direccion=f.id_direccion
 JOIN ing.terceros_carga c ON c.id_carga_terceros=f.id_carga_terceros
 OUTER APPLY(SELECT TOP(1) decision,aplica_desde,aplica_hasta,revisado_por,revisado_utc
   FROM ref.entidad_correo_revision r WHERE r.id_entidad_correo=f.id_entidad_correo ORDER BY id_revision DESC)r;');
 IF DATABASE_PRINCIPAL_ID(N'rol_carga_terceros') IS NULL CREATE ROLE rol_carga_terceros AUTHORIZATION dbo;
 GRANT SELECT ON OBJECT::core.entidad TO rol_carga_terceros;
 GRANT SELECT,INSERT ON OBJECT::mail.direccion TO rol_carga_terceros;
 GRANT SELECT,INSERT ON OBJECT::ing.terceros_carga TO rol_carga_terceros;
 GRANT SELECT,INSERT,UPDATE ON OBJECT::ing.terceros_publicacion TO rol_carga_terceros;
 GRANT SELECT,INSERT ON OBJECT::ref.entidad_correo TO rol_carga_terceros;
 GRANT SELECT,INSERT ON OBJECT::ref.entidad_correo_fuente TO rol_carga_terceros;
 GRANT SELECT,INSERT ON OBJECT::ing.terceros_pendiente TO rol_carga_terceros;
 GRANT SELECT ON OBJECT::rep.vw_revision_entidad_correo TO rol_carga_terceros;
 IF DATABASE_PRINCIPAL_ID(N'rol_revision_terceros') IS NULL CREATE ROLE rol_revision_terceros AUTHORIZATION dbo;
 GRANT SELECT ON OBJECT::rep.vw_revision_entidad_correo TO rol_revision_terceros;
 GRANT SELECT,INSERT ON OBJECT::ref.entidad_correo_revision TO rol_revision_terceros;
 INSERT cfg.migracion_terceros(version,sha256) VALUES(1,@hash);
 COMMIT TRANSACTION;
END TRY
BEGIN CATCH
 IF XACT_STATE()<>0 ROLLBACK TRANSACTION;
 THROW;
END CATCH;
