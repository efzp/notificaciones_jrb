/* Ejecutar administrativamente en JNC. Concede solo lectura de estas consultas.
   Asignar rol_lectura_terceros_jrb a la identidad de ejecucion de JRB. */
SET XACT_ABORT ON;
IF DB_NAME()<>N'sqldb-jnc-notif-dev' THROW 51853,N'Base incorrecta.',1;
BEGIN TRANSACTION;
IF DATABASE_PRINCIPAL_ID(N'rol_lectura_terceros_jrb') IS NULL
    CREATE ROLE rol_lectura_terceros_jrb AUTHORIZATION dbo;
GRANT SELECT ON OBJECT::jnc.calificacion_sistema_envio_entidad TO rol_lectura_terceros_jrb;
GRANT SELECT ON OBJECT::jnc.calificacion_sistema_caso TO rol_lectura_terceros_jrb;
GRANT SELECT ON OBJECT::jnc.notificacion_esperada TO rol_lectura_terceros_jrb;
GRANT SELECT ON OBJECT::jnc.caso_calificado TO rol_lectura_terceros_jrb;
GRANT SELECT ON OBJECT::jnc.resultado_cruce_notificacion TO rol_lectura_terceros_jrb;
GRANT SELECT ON OBJECT::jnc.notificacion_correo_certificado TO rol_lectura_terceros_jrb;
COMMIT TRANSACTION;
