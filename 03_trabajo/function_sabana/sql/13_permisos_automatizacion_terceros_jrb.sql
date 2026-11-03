/* Ejecutar administrativamente en JRB despues de las migraciones 09,10 y DDL 12.
   Asignar despues rol_carga_terceros a la identidad de ejecucion, no a GitHub. */
SET XACT_ABORT ON;
IF DB_NAME()<>N'sqldb-jrb-sabana-dev' THROW 51850,N'Base incorrecta.',1;
IF OBJECT_ID(N'ing.terceros_actualizacion',N'U') IS NULL THROW 51851,N'Falta DDL 12.',1;
IF DATABASE_PRINCIPAL_ID(N'rol_carga_terceros') IS NULL THROW 51852,N'Falta rol del catalogo.',1;
BEGIN TRANSACTION;
GRANT SELECT,INSERT,UPDATE ON OBJECT::ing.terceros_actualizacion TO rol_carga_terceros;
GRANT SELECT ON OBJECT::cfg.migracion_terceros TO rol_carga_terceros;
GRANT SELECT ON OBJECT::ing.archivo TO rol_carga_terceros;
GRANT SELECT ON OBJECT::ing.carga TO rol_carga_terceros;
GRANT SELECT ON OBJECT::ing.fila_origen TO rol_carga_terceros;
GRANT SELECT ON OBJECT::ing.parte_fila TO rol_carga_terceros;
GRANT SELECT ON OBJECT::core.caso TO rol_carga_terceros;
GRANT SELECT ON OBJECT::core.caso_parte TO rol_carga_terceros;
GRANT SELECT ON OBJECT::ing.pst_contenido TO rol_carga_terceros;
GRANT SELECT ON OBJECT::ing.pst_carga TO rol_carga_terceros;
GRANT SELECT ON OBJECT::mail.mensaje_origen TO rol_carga_terceros;
GRANT SELECT ON OBJECT::mail.mensaje TO rol_carga_terceros;
GRANT SELECT ON OBJECT::mail.destinatario TO rol_carga_terceros;
COMMIT TRANSACTION;
