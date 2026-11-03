/* Corte de frecuencia y mapeo inverso JNC por dominio/rol.
   Preserva publicaciones anteriores y nombres originales. */
SET NOCOUNT ON;
SET XACT_ABORT ON;
IF DB_NAME()<>N'sqldb-jrb-sabana-dev' THROW 51820,N'Base incorrecta.',1;
IF NOT EXISTS(SELECT 1 FROM cfg.migracion_terceros WHERE version=2) THROW 51821,N'Falta migracion 10.',1;
DECLARE @hash binary(32)=TRY_CONVERT(binary(32),SESSION_CONTEXT(N'terceros_migration_hash'));
IF @hash IS NULL THROW 51822,N'Utilice el ejecutor de migraciones.',1;
BEGIN TRY
 BEGIN TRANSACTION;
 DECLARE @lock int;
 EXEC @lock=sys.sp_getapplock @Resource=N'terceros:migracion',@LockMode='Exclusive',@LockOwner='Transaction',@LockTimeout=10000;
 IF @lock<0 THROW 51823,N'Migracion ocupada.',1;
 IF EXISTS(SELECT 1 FROM cfg.migracion_terceros WHERE version=3 AND sha256<>@hash)
  THROW 51824,N'La migracion aplicada tiene otra huella.',1;
 IF EXISTS(SELECT 1 FROM cfg.migracion_terceros WHERE version=3)
 BEGIN
  COMMIT TRANSACTION;
  RETURN;
 END;
 CREATE TABLE ref.dominio_corte (
  id_carga_terceros int NOT NULL REFERENCES ing.terceros_carga(id_carga_terceros),
  dominio varchar(253) COLLATE Latin1_General_100_BIN2 NOT NULL,
  casos_jnc int NOT NULL CHECK(casos_jnc>=0),
  observaciones_jnc int NOT NULL,
  minimo_casos_jnc int NOT NULL CHECK(minimo_casos_jnc>=1),
  incluido bit NOT NULL,
  proveedor_compartido bit NOT NULL,
  CONSTRAINT PK_dominio_corte PRIMARY KEY(id_carga_terceros,dominio),
  CONSTRAINT CK_dominio_corte_observaciones CHECK(observaciones_jnc>=casos_jnc),
  CONSTRAINT CK_dominio_corte_umbral CHECK((incluido=1 AND casos_jnc>=minimo_casos_jnc) OR (incluido=0 AND casos_jnc<minimo_casos_jnc))
 );
 CREATE TABLE ref.dominio_entidad (
  id_carga_terceros int NOT NULL,
  dominio varchar(253) COLLATE Latin1_General_100_BIN2 NOT NULL,
  tipo_parte varchar(20) NOT NULL CHECK(tipo_parte IN ('arl','afp','eps','aseguradora','ultimoempleador','remitente','regional')),
  id_entidad_dominante int NULL REFERENCES core.entidad(id_entidad),
  nombre_dominante_jnc nvarchar(500) NOT NULL,
  casos_total int NOT NULL CHECK(casos_total>0),
  casos_dominante int NOT NULL,
  casos_segundo int NOT NULL,
  porcentaje decimal(5,2) NOT NULL CHECK(porcentaje BETWEEN 0 AND 100),
  ventaja decimal(5,2) NOT NULL CHECK(ventaja BETWEEN 0 AND 100),
  dominancia_minima decimal(5,2) NOT NULL CHECK(dominancia_minima>50 AND dominancia_minima<=100),
  ventaja_minima decimal(5,2) NOT NULL CHECK(ventaja_minima>0 AND ventaja_minima<=100),
  estado varchar(25) NOT NULL CHECK(estado IN ('DOMINANTE','PROVEEDOR_COMPARTIDO','SOPORTE_INSUFICIENTE','SIN_DOMINANCIA','ENTIDAD_SIN_EQUIVALENCIA')),
  CONSTRAINT PK_dominio_entidad PRIMARY KEY(id_carga_terceros,dominio,tipo_parte),
  CONSTRAINT FK_dominio_entidad_corte FOREIGN KEY(id_carga_terceros,dominio) REFERENCES ref.dominio_corte(id_carga_terceros,dominio),
  CONSTRAINT CK_dominio_entidad_casos CHECK(casos_segundo>=0 AND casos_segundo<=casos_dominante AND casos_dominante<=casos_total),
  CONSTRAINT CK_dominio_entidad_aceptado CHECK(estado<>'DOMINANTE' OR (id_entidad_dominante IS NOT NULL AND porcentaje>=dominancia_minima AND ventaja>=ventaja_minima))
 );
 CREATE TABLE ref.entidad_correo_mapeo (
  id_carga_terceros int NOT NULL,
  dominio varchar(253) COLLATE Latin1_General_100_BIN2 NOT NULL,
  tipo_parte varchar(20) NOT NULL,
  hash_nombre binary(32) NOT NULL,
  nombre_origen nvarchar(500) NOT NULL,
  id_entidad_previa int NULL REFERENCES core.entidad(id_entidad),
  id_entidad_destino int NOT NULL REFERENCES core.entidad(id_entidad),
  puntaje_nombre decimal(5,2) NOT NULL CHECK(puntaje_nombre BETWEEN 0 AND 100),
  ocurrencias int NOT NULL CHECK(ocurrencias>0),
  hash_soporte binary(32) NOT NULL,
  referencia_muestra nvarchar(200) NOT NULL,
  CONSTRAINT PK_entidad_correo_mapeo PRIMARY KEY(id_carga_terceros,dominio,tipo_parte,hash_nombre),
  CONSTRAINT FK_entidad_correo_mapeo_dominio FOREIGN KEY(id_carga_terceros,dominio,tipo_parte) REFERENCES ref.dominio_entidad(id_carga_terceros,dominio,tipo_parte),
  CONSTRAINT CK_entidad_correo_mapeo_destino CHECK(id_entidad_previa IS NULL OR id_entidad_previa<>id_entidad_destino)
 );
 EXEC(N'CREATE VIEW rep.vw_dominio_tercero AS
 SELECT d.*,x.tipo_parte,x.nombre_dominante_jnc,x.id_entidad_dominante,e.nombre AS entidad_representante,
 x.casos_total,x.casos_dominante,x.casos_segundo,x.porcentaje,x.ventaja,x.dominancia_minima,x.ventaja_minima,x.estado
 FROM ing.terceros_publicacion p JOIN ref.dominio_corte d ON d.id_carga_terceros=p.id_carga_terceros
 LEFT JOIN ref.dominio_entidad x ON x.id_carga_terceros=d.id_carga_terceros AND x.dominio=d.dominio
 LEFT JOIN core.entidad e ON e.id_entidad=x.id_entidad_dominante;');
 EXEC(N'CREATE VIEW rep.vw_mapeo_dominio AS
 SELECT m.*,e.nombre AS entidad_destino,a.nombre AS entidad_previa,
 x.nombre_dominante_jnc,x.porcentaje,x.ventaja,x.casos_dominante,x.casos_total
 FROM ing.terceros_publicacion p JOIN ref.entidad_correo_mapeo m ON m.id_carga_terceros=p.id_carga_terceros
 JOIN ref.dominio_entidad x ON x.id_carga_terceros=m.id_carga_terceros AND x.dominio=m.dominio AND x.tipo_parte=m.tipo_parte
 JOIN core.entidad e ON e.id_entidad=m.id_entidad_destino
 LEFT JOIN core.entidad a ON a.id_entidad=m.id_entidad_previa;');
 GRANT SELECT,INSERT ON OBJECT::ref.dominio_corte TO rol_carga_terceros;
 GRANT SELECT,INSERT ON OBJECT::ref.dominio_entidad TO rol_carga_terceros;
 GRANT SELECT,INSERT ON OBJECT::ref.entidad_correo_mapeo TO rol_carga_terceros;
 GRANT SELECT ON OBJECT::rep.vw_dominio_tercero TO rol_carga_terceros;
 GRANT SELECT ON OBJECT::rep.vw_mapeo_dominio TO rol_carga_terceros;
 GRANT SELECT ON OBJECT::rep.vw_dominio_tercero TO rol_revision_terceros;
 GRANT SELECT ON OBJECT::rep.vw_mapeo_dominio TO rol_revision_terceros;
 INSERT cfg.migracion_terceros(version,sha256) VALUES(3,@hash);
 COMMIT TRANSACTION;
END TRY
BEGIN CATCH
 IF XACT_STATE()<>0 ROLLBACK TRANSACTION;
 THROW;
END CATCH;
