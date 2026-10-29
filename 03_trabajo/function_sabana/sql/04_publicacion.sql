/* Operaciones de control. La Function inserta datos en UNA transaccion por carga.
   Publicar es la unica operacion que cambia el contenido visible del archivo. */
SET ANSI_NULLS ON;
SET QUOTED_IDENTIFIER ON;
GO
CREATE OR ALTER PROCEDURE ing.cambiar_estado_carga
    @id_carga bigint,
    @accion varchar(10),
    @filas_esperadas int=NULL,
    @detalle nvarchar(1000)=NULL
AS
BEGIN
 SET NOCOUNT ON;
 SET XACT_ABORT ON;
 BEGIN TRY
  BEGIN TRANSACTION;
  DECLARE @estado varchar(15),@perfil smallint;
  SELECT @estado=estado,@perfil=id_perfil FROM ing.carga WITH(UPDLOCK,HOLDLOCK) WHERE id_carga=@id_carga;
  IF @estado IS NULL THROW 51100,N'Carga inexistente.',1;
  IF @accion='INICIAR'
  BEGIN
   IF @estado NOT IN ('RECIBIDA','ERROR') THROW 51101,N'Estado incompatible con INICIAR.',1;
   IF NOT EXISTS(SELECT 1 FROM cfg.perfil_entrada WHERE id_perfil=@perfil AND publicado=1)
       THROW 51102,N'El perfil debe estar publicado.',1;
   IF EXISTS(SELECT 1 FROM ing.fila_origen WHERE id_carga=@id_carga)
       THROW 51103,N'La carga contiene filas. No se reabre una instantanea; revise la transaccion fallida.',1;
   UPDATE ing.incidencia SET resuelta=1
     WHERE id_carga=@id_carga AND codigo='ERROR_PROCESAMIENTO' AND resuelta=0;
   UPDATE ing.carga SET estado='PROCESANDO',filas_esperadas=NULL,fecha_validacion_utc=NULL WHERE id_carga=@id_carga;
  END
  ELSE IF @accion='VALIDAR'
  BEGIN
   IF @estado<>'PROCESANDO' THROW 51104,N'Estado incompatible con VALIDAR.',1;
   IF @filas_esperadas IS NULL OR @filas_esperadas<0 THROW 51105,N'Declare el numero de filas no vacias del origen.',1;
   IF (SELECT COUNT_BIG(*) FROM ing.fila_origen WHERE id_carga=@id_carga)<>CONVERT(bigint,@filas_esperadas)
       THROW 51106,N'No coincide el numero de filas de origen.',1;
   IF EXISTS(SELECT 1 FROM ing.incidencia WHERE id_carga=@id_carga AND severidad='E' AND resuelta=0)
       THROW 51107,N'La carga tiene errores bloqueantes sin resolver.',1;
   UPDATE ing.carga SET estado='VALIDADA',filas_esperadas=@filas_esperadas,fecha_validacion_utc=SYSUTCDATETIME()
     WHERE id_carga=@id_carga;
  END
  ELSE IF @accion='ERROR'
  BEGIN
   IF @estado='PUBLICADA' THROW 51108,N'Una carga publicada es inmutable.',1;
   UPDATE ing.carga SET estado='ERROR' WHERE id_carga=@id_carga;
   INSERT ing.incidencia(id_carga,codigo,severidad,detalle)
       VALUES(@id_carga,'ERROR_PROCESAMIENTO','E',COALESCE(@detalle,N'Error de procesamiento.'));
  END
  ELSE THROW 51109,N'Accion desconocida.',1;
  COMMIT TRANSACTION;
 END TRY
 BEGIN CATCH
  IF XACT_STATE()<>0 ROLLBACK TRANSACTION;
  THROW;
 END CATCH;
END;
GO
CREATE OR ALTER PROCEDURE ing.publicar_carga
    @id_carga bigint,
    @version_origen nvarchar(128),
    @fecha_modificacion_origen datetime2(7),
    @nombre_archivo nvarchar(255),
    @ruta_sharepoint nvarchar(2048),
    @permitir_vacio bit=0
AS
BEGIN
 SET NOCOUNT ON;
 SET XACT_ABORT ON;
 BEGIN TRY
  BEGIN TRANSACTION;
  DECLARE @archivo bigint,@estado varchar(15),@esperadas int,@perfil smallint,@hash binary(32),
          @actual bigint,@modificacion_actual datetime2(7),@hash_actual binary(32),
          @min date,@max date,@calendario smallint;
  IF @fecha_modificacion_origen IS NULL OR LEN(COALESCE(@version_origen,N''))=0
     OR LEN(COALESCE(@nombre_archivo,N''))=0 OR LEN(COALESCE(@ruta_sharepoint,N''))=0
       THROW 51200,N'Faltan metadatos de origen para publicar.',1;
  SELECT @archivo=id_archivo FROM ing.carga WHERE id_carga=@id_carga;
  IF @archivo IS NULL THROW 51201,N'Carga inexistente.',1;
  /* Serializa la publicacion de un mismo archivo. Otros archivos no se bloquean entre si. */
  SELECT @actual=id_carga_vigente,@modificacion_actual=fecha_modificacion_origen
    FROM ing.archivo WITH(UPDLOCK,HOLDLOCK) WHERE id_archivo=@archivo;
  SELECT @estado=estado,@esperadas=filas_esperadas,@perfil=id_perfil,@hash=hash_contenido
    FROM ing.carga WITH(UPDLOCK,HOLDLOCK) WHERE id_carga=@id_carga;
  IF @estado NOT IN ('VALIDADA','PUBLICADA') THROW 51202,N'Primero debe validar la carga.',1;
  SELECT @min=p.fecha_minima,@max=p.fecha_maxima,@calendario=p.id_calendario
    FROM cfg.perfil_entrada p JOIN cfg.calendario c ON c.id_calendario=p.id_calendario
    WHERE p.id_perfil=@perfil AND p.publicado=1 AND c.publicado=1
      AND c.fecha_desde<=p.fecha_minima AND c.fecha_hasta>=p.fecha_maxima;
  IF @calendario IS NULL THROW 51203,N'Perfil o calendario no publicado, o rango sin cobertura.',1;
  IF @esperadas IS NULL OR @esperadas<>(SELECT COUNT_BIG(*) FROM ing.fila_origen WHERE id_carga=@id_carga)
       THROW 51204,N'El conteo de filas no coincide con la validacion.',1;
  IF @esperadas=0 AND ISNULL(@permitir_vacio,0)=0 THROW 51205,N'Publicar un archivo vacio requiere permitir_vacio=1.',1;
  IF EXISTS(SELECT 1 FROM ing.incidencia WHERE id_carga=@id_carga AND severidad='E' AND resuelta=0)
       THROW 51206,N'Existen errores bloqueantes sin resolver.',1;
  IF @modificacion_actual IS NOT NULL AND @fecha_modificacion_origen<@modificacion_actual
       THROW 51207,N'Evento atrasado: no sustituye una version de origen mas reciente.',1;
  SELECT @hash_actual=hash_contenido FROM ing.carga WHERE id_carga=@actual;
  IF @modificacion_actual=@fecha_modificacion_origen AND @hash_actual<>@hash
       THROW 51208,N'Misma fecha de origen con contenido distinto: resolver conflicto.',1;
  IF EXISTS(
      SELECT 1 FROM ing.fila_origen f
      JOIN core.caso c ON c.id_caso=f.id_caso JOIN core.caso_dato cd ON cd.id_caso_dato=f.id_caso_dato
      CROSS APPLY(VALUES(c.fecha_audiencia),(cd.fecha_fijacion),(cd.fecha_desfijacion)) v(fecha)
      WHERE f.id_carga=@id_carga AND v.fecha IS NOT NULL
        AND (v.fecha<@min OR v.fecha>@max OR NOT EXISTS(
            SELECT 1 FROM cfg.dia_calendario d WHERE d.id_calendario=@calendario AND d.fecha=v.fecha))
      UNION ALL
      SELECT 1 FROM ing.fila_origen f JOIN ing.parte_fila pf ON pf.id_fila=f.id_fila
      JOIN core.notificacion_declarada n ON n.id_notificacion=pf.id_notificacion
      CROSS APPLY(VALUES(n.fecha_envio_reportada),(n.fecha_notificacion_reportada),(n.fecha_recibido_reportada)) v(fecha)
      WHERE f.id_carga=@id_carga AND v.fecha IS NOT NULL
        AND (v.fecha<@min OR v.fecha>@max OR NOT EXISTS(
            SELECT 1 FROM cfg.dia_calendario d WHERE d.id_calendario=@calendario AND d.fecha=v.fecha))
  ) THROW 51209,N'Hay fechas tipadas fuera del perfil/calendario. Conserve el original como excepcion y revise.',1;
  IF EXISTS(
      SELECT 1 FROM ing.parte_fila pf JOIN ing.fila_origen f ON f.id_fila=pf.id_fila
      LEFT JOIN core.notificacion_declarada n ON n.id_notificacion=pf.id_notificacion
      LEFT JOIN core.caso_parte cp ON cp.id_caso_parte=pf.id_caso_parte
      WHERE f.id_carga=@id_carga AND (
          ((pf.mascara_no_aplica & 1)=1 AND cp.id_entidad IS NOT NULL) OR
          ((pf.mascara_no_aplica & 2)=2 AND n.documento_notificacion IS NOT NULL) OR
          ((pf.mascara_no_aplica & 4)=4 AND n.estado_reportado IS NOT NULL) OR
          ((pf.mascara_no_aplica & 8)=8 AND n.fecha_notificacion_reportada IS NOT NULL) OR
          ((pf.mascara_no_aplica & 16)=16 AND n.fecha_envio_reportada IS NOT NULL) OR
          ((pf.mascara_no_aplica & 32)=32 AND n.guia_reportada IS NOT NULL) OR
          ((pf.mascara_no_aplica & 64)=64 AND n.fecha_recibido_reportada IS NOT NULL)))
      THROW 51210,N'Un campo no puede tener simultaneamente dato tipado y marcador NO APLICA.',1;
  UPDATE ing.carga SET estado='PUBLICADA',fecha_publicacion_utc=COALESCE(fecha_publicacion_utc,SYSUTCDATETIME())
    WHERE id_carga=@id_carga;
  UPDATE ing.archivo SET id_carga_vigente=@id_carga,version_origen_actual=@version_origen,
      fecha_modificacion_origen=@fecha_modificacion_origen,nombre_actual=@nombre_archivo,ruta_actual=@ruta_sharepoint
    WHERE id_archivo=@archivo;
  COMMIT TRANSACTION;
  SELECT @id_carga AS id_carga,CONVERT(bit,CASE WHEN @actual=@id_carga THEN 0 ELSE 1 END) AS cambio_carga_vigente;
 END TRY
 BEGIN CATCH
  IF XACT_STATE()<>0 ROLLBACK TRANSACTION;
  THROW;
 END CATCH;
END;
GO
CREATE OR ALTER TRIGGER ing.tr_fila_solo_en_proceso ON ing.fila_origen AFTER INSERT
AS
BEGIN
 SET NOCOUNT ON;
 IF EXISTS(SELECT 1 FROM inserted i JOIN ing.carga c ON c.id_carga=i.id_carga WHERE c.estado<>'PROCESANDO')
     THROW 51300,N'Solo se agregan filas a una carga PROCESANDO.',1;
END;
GO
CREATE OR ALTER TRIGGER ing.tr_parte_solo_en_proceso ON ing.parte_fila AFTER INSERT
AS
BEGIN
 SET NOCOUNT ON;
 IF EXISTS(SELECT 1 FROM inserted i JOIN ing.fila_origen f ON f.id_fila=i.id_fila
           JOIN ing.carga c ON c.id_carga=f.id_carga WHERE c.estado<>'PROCESANDO')
     THROW 51301,N'Solo se agregan partes a una carga PROCESANDO.',1;
END;
GO
CREATE OR ALTER TRIGGER ing.tr_original_solo_en_proceso ON ing.valor_original AFTER INSERT
AS
BEGIN
 SET NOCOUNT ON;
 IF EXISTS(SELECT 1 FROM inserted i JOIN ing.fila_origen f ON f.id_fila=i.id_fila
           JOIN ing.carga c ON c.id_carga=f.id_carga WHERE c.estado<>'PROCESANDO')
     THROW 51302,N'Solo se agregan excepciones a una carga PROCESANDO.',1;
END;
GO
/* Roles sin usuarios. Asociar la identidad administrada de la futura Function por separado.
   El rol de carga no puede actualizar hechos, perfiles ni el puntero de publicacion directamente. */
IF DATABASE_PRINCIPAL_ID(N'rol_carga_sabana') IS NULL CREATE ROLE rol_carga_sabana AUTHORIZATION dbo;
IF DATABASE_PRINCIPAL_ID(N'rol_consulta_sabana') IS NULL CREATE ROLE rol_consulta_sabana AUTHORIZATION dbo;
GRANT SELECT ON SCHEMA::cfg TO rol_carga_sabana;
GRANT SELECT,INSERT ON SCHEMA::core TO rol_carga_sabana;
GRANT SELECT,INSERT ON OBJECT::ing.archivo TO rol_carga_sabana;
GRANT SELECT,INSERT ON OBJECT::ing.carga TO rol_carga_sabana;
GRANT SELECT,INSERT ON OBJECT::ing.fila_origen TO rol_carga_sabana;
GRANT SELECT,INSERT ON OBJECT::ing.parte_fila TO rol_carga_sabana;
GRANT SELECT,INSERT ON OBJECT::ing.valor_original TO rol_carga_sabana;
GRANT SELECT,INSERT ON OBJECT::ing.incidencia TO rol_carga_sabana;
GRANT EXECUTE ON OBJECT::ing.cambiar_estado_carga TO rol_carga_sabana;
GRANT EXECUTE ON OBJECT::ing.publicar_carga TO rol_carga_sabana;
GRANT SELECT ON SCHEMA::rep TO rol_consulta_sabana;
GO
