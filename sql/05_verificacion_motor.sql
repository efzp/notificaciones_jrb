/* Pruebas para ejecutar DESPUES de 01..04 en la base NUEVA, con un usuario de pruebas dbo.
   Datos sinteticos. Se revierten todos al terminar, tambien en caso de fallo.
   No contiene datos personales ni conexiones a servicios externos. */
SET NOCOUNT ON;
SET XACT_ABORT OFF;
SET ANSI_NULLS ON;
SET ANSI_PADDING ON;
SET ANSI_WARNINGS ON;
SET ARITHABORT ON;
SET CONCAT_NULL_YIELDS_NULL ON;
SET QUOTED_IDENTIFIER ON;
SET NUMERIC_ROUNDABORT OFF;
IF DB_NAME() IN (N'master',N'sqldb-jnc-notif-dev') THROW 51400,N'Use la base nueva del proyecto.',1;
IF (SELECT COUNT(*) FROM sys.columns WHERE object_id=OBJECT_ID(N'rep.vw_consolidado_sabana'))<>85
    THROW 51401,N'La vista debe exponer exactamente 85 columnas.',1;
IF NOT EXISTS(SELECT 1 FROM cfg.perfil_entrada WHERE id_perfil=1 AND publicado=1)
    THROW 51402,N'Falta el perfil de prueba publicado.',1;
DECLARE @drive nvarchar(128)=CONCAT(N'prueba-ddl-',CONVERT(nvarchar(36),NEWID()));
DECLARE @archivo bigint,@carga bigint,@carga2 bigint,@caso bigint,@otro_caso bigint,@dato bigint,
        @entidad_a int,@entidad_b int,@parte_a bigint,@parte_b bigint,@parte_otra bigint,
        @not_a bigint,@not_b bigint,@fila1 bigint,@fila2 bigint,@fila3 bigint,@rechazado bit;
BEGIN TRY
 BEGIN TRANSACTION;
 INSERT ing.archivo(drive_id,item_id,nombre_actual,ruta_actual)
 VALUES(@drive,N'item-prueba',N'control.xlsx',N'https://example.invalid/control.xlsx');
 SET @archivo=SCOPE_IDENTITY();
 INSERT ing.carga(id_archivo,id_perfil,hash_contenido,tamano_bytes,version_origen_inicial,nombre_origen_inicial,ruta_origen_inicial)
 VALUES(@archivo,1,HASHBYTES('SHA2_256','fixture-1'),100,N'1.0',N'control.xlsx',N'https://example.invalid/control.xlsx');
 SET @carga=SCOPE_IDENTITY();
 EXEC ing.cambiar_estado_carga @carga,'INICIAR';
 INSERT core.caso(sala,fecha_audiencia,reparto_paciente,identificacion_paciente)
 VALUES(1,'20260304',CONCAT(N'PRUEBA-',@archivo),N'IDENTIFICADOR-SINTETICO');
 SET @caso=SCOPE_IDENTITY();
 INSERT core.caso_dato(id_caso,tipo_de_dictamen,nombre_paciente,medico_ponente,fecha_fijacion,fecha_desfijacion,hash_datos)
 VALUES(@caso,N'AUDIENCIA',N'PACIENTE SINTETICO',N'MEDICO SINTETICO','20260313','20260327',HASHBYTES('SHA2_256','datos-caso'));
 SET @dato=SCOPE_IDENTITY();
 INSERT core.entidad(nombre) VALUES(CONCAT(N'ASEGURADORA A PRUEBA ',@drive)); SET @entidad_a=SCOPE_IDENTITY();
 INSERT core.entidad(nombre) VALUES(CONCAT(N'ASEGURADORA B PRUEBA ',@drive)); SET @entidad_b=SCOPE_IDENTITY();
 INSERT core.caso_parte(id_caso,tipo_parte,id_entidad) VALUES(@caso,'aseguradora',@entidad_a); SET @parte_a=SCOPE_IDENTITY();
 INSERT core.caso_parte(id_caso,tipo_parte,id_entidad) VALUES(@caso,'aseguradora',@entidad_b); SET @parte_b=SCOPE_IDENTITY();
 INSERT core.notificacion_declarada(id_caso_parte,documento_notificacion,estado_reportado,fecha_envio_reportada,fecha_notificacion_reportada,guia_reportada,hash_datos)
 VALUES(@parte_a,N'NOT-PRUEBA-A',N'Se notifico','20260312','20260314',N'EMAIL',HASHBYTES('SHA2_256','notificacion-a'));
 SET @not_a=SCOPE_IDENTITY();
 INSERT core.notificacion_declarada(id_caso_parte,documento_notificacion,estado_reportado,fecha_envio_reportada,fecha_notificacion_reportada,guia_reportada,hash_datos)
 VALUES(@parte_b,N'NOT-PRUEBA-B',N'Aviso','20260312','20260327',N'EMAIL',HASHBYTES('SHA2_256','notificacion-b'));
 SET @not_b=SCOPE_IDENTITY();
 INSERT ing.fila_origen(id_carga,id_perfil,hoja,numero_fila,id_caso,id_caso_dato) VALUES(@carga,1,N'hoja1',2,@caso,@dato);
 SET @fila1=SCOPE_IDENTITY();
 INSERT ing.fila_origen(id_carga,id_perfil,hoja,numero_fila,id_caso,id_caso_dato) VALUES(@carga,1,N'hoja1',3,@caso,@dato);
 SET @fila2=SCOPE_IDENTITY();
 INSERT ing.parte_fila(id_fila,tipo_parte,id_caso,id_caso_parte,id_notificacion)
 VALUES(@fila1,'aseguradora',@caso,@parte_a,@not_a),(@fila2,'aseguradora',@caso,@parte_b,@not_b);
 /* Marcadores compactos: arl completa NO APLICA, sin entidad ficticia ni siete cadenas repetidas. */
 INSERT ing.parte_fila(id_fila,tipo_parte,id_caso,mascara_no_aplica) VALUES(@fila1,'arl',@caso,127);
 EXEC ing.cambiar_estado_carga @carga,'VALIDAR',2;
 EXEC ing.publicar_carga @carga,N'1.0','20260328',N'control.xlsx',N'https://example.invalid/control.xlsx';
 IF (SELECT COUNT(*) FROM rep.vw_consolidado_sabana WHERE archivo_origen=N'https://example.invalid/control.xlsx' AND reparto_paciente=CONCAT(N'PRUEBA-',@archivo))<>2
     THROW 51403,N'Deben aparecer las dos filas originales.',1;
 IF (SELECT COUNT(*) FROM rep.vw_notificacion_declarada_vigente WHERE id_caso=@caso AND tipo_parte='aseguradora')<>2
     THROW 51404,N'Deben mantenerse separadas las dos aseguradoras.',1;
 IF NOT EXISTS(SELECT 1 FROM rep.vw_consolidado_sabana WHERE reparto_paciente=CONCAT(N'PRUEBA-',@archivo)
      AND fila_origen=2 AND arl=N'NO APLICA' AND guia_arl=N'NO APLICA' AND dias_cal_audiencia_a_envio_aseguradora=8)
     THROW 51405,N'La representacion de marcadores o el calculo calendario es incorrecto.',1;
 IF (SELECT dias_habiles FROM cfg.fn_dias_habiles(1,'20260306','20260309'))<>1
     OR (SELECT dias_habiles FROM cfg.fn_dias_habiles(1,'20260309','20260306'))<>-1
     OR (SELECT dias_habiles FROM cfg.fn_dias_habiles(1,'20260323','20260320'))<>-1
     OR (SELECT dias_habiles FROM cfg.fn_dias_habiles(1,'20260320','20260323'))<>0
     THROW 51406,N'No se respeta excluir inicio e incluir fin con signo y festivo.',1;
 EXEC ing.publicar_carga @carga,N'1.0','20260328',N'control.xlsx',N'https://example.invalid/control.xlsx';
 IF (SELECT COUNT(*) FROM ing.fila_origen WHERE id_carga=@carga)<>2 THROW 51407,N'Un reintento duplico filas.',1;
 SET @rechazado=0;
 BEGIN TRY
  INSERT core.caso(sala,fecha_audiencia,reparto_paciente,identificacion_paciente)
  VALUES(1,'20260304',CONCAT(N'PRUEBA-',@archivo),N'IDENTIFICADOR-SINTETICO');
 END TRY
 BEGIN CATCH
  IF ERROR_NUMBER() NOT IN (2601,2627) THROW;
  SET @rechazado=1;
 END CATCH;
 IF @rechazado=0 THROW 51408,N'No se rechazo el caso duplicado.',1;
 SET @rechazado=0;
 BEGIN TRY
  INSERT ing.carga(id_archivo,id_perfil,hash_contenido,tamano_bytes,version_origen_inicial,nombre_origen_inicial,ruta_origen_inicial)
  VALUES(@archivo,1,HASHBYTES('SHA2_256','fixture-1'),100,N'1.0',N'control.xlsx',N'https://example.invalid/control.xlsx');
 END TRY
 BEGIN CATCH
  IF ERROR_NUMBER() NOT IN (2601,2627) THROW;
  SET @rechazado=1;
 END CATCH;
 IF @rechazado=0 THROW 51409,N'No se rechazo la carga repetida.',1;
 INSERT core.caso(sala,fecha_audiencia,reparto_paciente,identificacion_paciente)
 VALUES(1,'20260304',CONCAT(N'OTRO-',@archivo),N'OTRA-IDENTIFICACION'); SET @otro_caso=SCOPE_IDENTITY();
 INSERT core.caso_parte(id_caso,tipo_parte,id_entidad) VALUES(@otro_caso,'eps',@entidad_a); SET @parte_otra=SCOPE_IDENTITY();
 SET @rechazado=0;
 BEGIN TRY
  INSERT ing.parte_fila(id_fila,tipo_parte,id_caso,id_caso_parte) VALUES(@fila1,'eps',@otro_caso,@parte_otra);
 END TRY
 BEGIN CATCH
  IF ERROR_NUMBER()<>547 THROW;
  SET @rechazado=1;
 END CATCH;
 IF @rechazado=0 THROW 51410,N'No se rechazo la asociacion de una fila con otro caso.',1;
 INSERT ing.carga(id_archivo,id_perfil,hash_contenido,tamano_bytes,version_origen_inicial,nombre_origen_inicial,ruta_origen_inicial)
 VALUES(@archivo,1,HASHBYTES('SHA2_256','fixture-2'),101,N'2.0',N'control.xlsx',N'https://example.invalid/control.xlsx'); SET @carga2=SCOPE_IDENTITY();
 EXEC ing.cambiar_estado_carga @carga2,'INICIAR';
 INSERT ing.fila_origen(id_carga,id_perfil,hoja,numero_fila,id_caso,id_caso_dato) VALUES(@carga2,1,N'hoja1',2,@caso,@dato); SET @fila3=SCOPE_IDENTITY();
 INSERT ing.parte_fila(id_fila,tipo_parte,id_caso,id_caso_parte,id_notificacion) VALUES(@fila3,'aseguradora',@caso,@parte_a,@not_a);
 EXEC ing.cambiar_estado_carga @carga2,'VALIDAR',1;
 EXEC ing.publicar_carga @carga2,N'2.0','20260329',N'control.xlsx',N'https://example.invalid/control.xlsx';
 IF (SELECT COUNT(*) FROM rep.vw_consolidado_sabana WHERE reparto_paciente=CONCAT(N'PRUEBA-',@archivo))<>1
    OR (SELECT COUNT(*) FROM core.caso_dato WHERE id_caso=@caso)<>1
     THROW 51411,N'La nueva carga no sustituye la proyeccion o duplica datos de caso.',1;
 /* Ultima prueba: el procedimiento revierte la transaccion completa al rechazar un evento atrasado. */
 SET @rechazado=0;
 BEGIN TRY
  EXEC ing.publicar_carga @carga,N'1.0','20260328',N'control.xlsx',N'https://example.invalid/control.xlsx';
 END TRY
 BEGIN CATCH
  IF ERROR_NUMBER()<>51207 THROW;
  SET @rechazado=1;
 END CATCH;
 IF @rechazado=0 THROW 51412,N'No se rechazo un evento atrasado.',1;
 IF @@TRANCOUNT>0 ROLLBACK TRANSACTION;
 IF EXISTS(SELECT 1 FROM ing.archivo WHERE drive_id=@drive) THROW 51413,N'Quedaron datos de prueba.',1;
 SELECT N'OK: contrato de 85 columnas, dos aseguradoras, no aplica, calendario, idempotencia, FK y actualizacion atomica.' AS resultado;
END TRY
BEGIN CATCH
 IF XACT_STATE()<>0 ROLLBACK TRANSACTION;
 THROW;
END CATCH;
