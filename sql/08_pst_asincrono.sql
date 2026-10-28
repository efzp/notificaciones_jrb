/* Ejecutar despues de 07_tablas_pst.sql. Preparacion para Durable Functions.
   No modifica Sabana ni asigna usuarios. Migracion reejecutable.
*/
SET NOCOUNT ON;
SET XACT_ABORT ON;
IF DB_NAME()<>N'sqldb-jrb-sabana-dev' THROW 51700,N'Base incorrecta.',1;
IF OBJECT_ID(N'ing.pst_carga',N'U') IS NULL THROW 51701,N'Ejecute primero 07_tablas_pst.sql.',1;
BEGIN TRY
 BEGIN TRANSACTION;
 IF COL_LENGTH(N'ing.pst_carga',N'inventario_completo') IS NULL
  ALTER TABLE ing.pst_carga ADD inventario_completo bit NOT NULL CONSTRAINT DF_pst_inventario DEFAULT 0;
 IF COL_LENGTH(N'ing.pst_carga',N'latido_utc') IS NULL
  ALTER TABLE ing.pst_carga ADD latido_utc datetime2(7) NULL;
 IF COL_LENGTH(N'mail.pst_carpeta',N'ruta_indices') IS NULL
  ALTER TABLE mail.pst_carpeta ADD ruta_indices nvarchar(2048) NULL;
 IF COL_LENGTH(N'mail.pst_carpeta',N'siguiente_indice') IS NULL
  ALTER TABLE mail.pst_carpeta ADD siguiente_indice int NOT NULL CONSTRAINT DF_pst_siguiente DEFAULT 0,
   CONSTRAINT CK_pst_siguiente CHECK(siguiente_indice>=0);
 IF OBJECT_ID(N'ing.pst_elemento',N'U') IS NULL
 BEGIN
  CREATE TABLE ing.pst_elemento (
   id_carga int NOT NULL,
   id_carpeta int NOT NULL,
   indice int NOT NULL,
   identificador_pst bigint NULL,
   id_origen int NULL,
   estado varchar(8) NOT NULL,
   codigo varchar(64) NULL,
   CONSTRAINT PK_pst_elemento PRIMARY KEY(id_carga,id_carpeta,indice),
   CONSTRAINT FK_pst_elemento_carpeta FOREIGN KEY(id_carga,id_carpeta) REFERENCES mail.pst_carpeta(id_carga,id_carpeta),
   CONSTRAINT FK_pst_elemento_origen FOREIGN KEY(id_carga,id_origen) REFERENCES mail.mensaje_origen(id_carga,id_origen),
   CONSTRAINT CK_pst_elemento_estado CHECK(estado IN ('EXTRAIDO','OMITIDO','ERROR')),
   CONSTRAINT CK_pst_elemento_indice CHECK(indice>=0),
   CONSTRAINT CK_pst_elemento_origen CHECK((estado='EXTRAIDO' AND id_origen IS NOT NULL) OR (estado<>'EXTRAIDO' AND id_origen IS NULL))
  );
 END;
 IF DATABASE_PRINCIPAL_ID(N'rol_carga_pst') IS NULL CREATE ROLE rol_carga_pst AUTHORIZATION dbo;
 GRANT SELECT,INSERT ON OBJECT::ing.pst_contenido TO rol_carga_pst;
 GRANT UPDATE(id_carga_vigente) ON OBJECT::ing.pst_contenido TO rol_carga_pst;
 GRANT SELECT,INSERT ON OBJECT::ing.pst_archivo TO rol_carga_pst;
 GRANT SELECT,INSERT,UPDATE ON OBJECT::ing.pst_carga TO rol_carga_pst;
 GRANT SELECT,INSERT ON OBJECT::ing.pst_elemento TO rol_carga_pst;
 GRANT SELECT,INSERT ON OBJECT::ing.pst_incidencia TO rol_carga_pst;
 GRANT UPDATE(resuelta) ON OBJECT::ing.pst_incidencia TO rol_carga_pst;
 GRANT SELECT,INSERT,UPDATE ON OBJECT::mail.pst_carpeta TO rol_carga_pst;
 GRANT SELECT,INSERT ON OBJECT::mail.direccion TO rol_carga_pst;
 GRANT SELECT,INSERT ON OBJECT::mail.mensaje TO rol_carga_pst;
 GRANT SELECT,INSERT ON OBJECT::mail.mensaje_origen TO rol_carga_pst;
 GRANT SELECT,INSERT ON OBJECT::mail.destinatario TO rol_carga_pst;
 GRANT SELECT,INSERT ON OBJECT::mail.adjunto TO rol_carga_pst;
 GRANT SELECT,INSERT ON OBJECT::mail.referencia_mensaje TO rol_carga_pst;
 GRANT SELECT,INSERT ON OBJECT::mail.reporte_destinatario TO rol_carga_pst;
 COMMIT TRANSACTION;
END TRY
BEGIN CATCH
 IF XACT_STATE()<>0 ROLLBACK TRANSACTION;
 THROW;
END CATCH;
