/* Vistas sin almacenamiento adicional. Ejecutar cada lote separado por GO. */
SET ANSI_NULLS ON;
SET QUOTED_IDENTIFIER ON;
GO
CREATE OR ALTER FUNCTION cfg.fn_dias_habiles(@id_calendario smallint,@inicio date,@fin date)
RETURNS TABLE WITH SCHEMABINDING
AS RETURN (
 SELECT CASE WHEN @inicio IS NULL OR @fin IS NULL OR i.fecha IS NULL OR f.fecha IS NULL THEN CONVERT(int,NULL)
   WHEN @fin>=@inicio THEN f.acumulado_habil-i.acumulado_habil
   ELSE f.acumulado_habil-i.acumulado_habil+CONVERT(int,i.es_habil)-CONVERT(int,f.es_habil)
   END AS dias_habiles
 FROM (VALUES(1)) AS semilla(n)
 LEFT JOIN cfg.dia_calendario i ON i.id_calendario=@id_calendario AND i.fecha=@inicio
 LEFT JOIN cfg.dia_calendario f ON f.id_calendario=@id_calendario AND f.fecha=@fin
);
GO
CREATE OR ALTER VIEW rep.vw_fila_base
AS
SELECT f.id_fila,f.id_carga,f.id_caso,f.id_perfil,pe.id_calendario,
       CONCAT(N'Sala ',c.sala) AS sala,a.ruta_actual AS archivo_origen,f.numero_fila AS fila_origen,
       CONCAT(N'Sala ',c.sala,N'|',CONVERT(nvarchar(10),c.fecha_audiencia,23),N'|',c.reparto_paciente,N'|',c.identificacion_paciente) AS llave_caso,
       COALESCE(x.[fecha_audiencia],CONVERT(nvarchar(2000),CONVERT(nvarchar(10),c.fecha_audiencia,23)) COLLATE DATABASE_DEFAULT) AS [fecha_audiencia],
       COALESCE(x.[tipo_de_dictamen],CONVERT(nvarchar(2000),cd.tipo_de_dictamen) COLLATE DATABASE_DEFAULT) AS [tipo_de_dictamen],
       COALESCE(x.[reparto_paciente],CONVERT(nvarchar(2000),c.reparto_paciente) COLLATE DATABASE_DEFAULT) AS [reparto_paciente],
       COALESCE(x.[identificacion_paciente],CONVERT(nvarchar(2000),c.identificacion_paciente) COLLATE DATABASE_DEFAULT) AS [identificacion_paciente],
       COALESCE(x.[nombre_paciente],CONVERT(nvarchar(2000),cd.nombre_paciente) COLLATE DATABASE_DEFAULT) AS [nombre_paciente],
       COALESCE(x.[medico_ponente],CONVERT(nvarchar(2000),cd.medico_ponente) COLLATE DATABASE_DEFAULT) AS [medico_ponente],
       COALESCE(x.[fecha_fijacion],CONVERT(nvarchar(2000),CONVERT(nvarchar(10),cd.fecha_fijacion,23)) COLLATE DATABASE_DEFAULT) AS [fecha_fijacion],
       COALESCE(x.[fecha_desfijacion],CONVERT(nvarchar(2000),CONVERT(nvarchar(10),cd.fecha_desfijacion,23)) COLLATE DATABASE_DEFAULT) AS [fecha_desfijacion],
       COALESCE(x.[documento_notificacion_paciente],CONVERT(nvarchar(2000),CASE WHEN (p0.mascara_no_aplica & 2)=2 THEN N'NO APLICA' ELSE n0.documento_notificacion END) COLLATE DATABASE_DEFAULT) AS [documento_notificacion_paciente],
       COALESCE(x.[estado_paciente],CONVERT(nvarchar(2000),CASE WHEN (p0.mascara_no_aplica & 4)=4 THEN N'NO APLICA' ELSE n0.estado_reportado END) COLLATE DATABASE_DEFAULT) AS [estado_paciente],
       COALESCE(x.[fecha_notificacion_paciente],CONVERT(nvarchar(2000),CASE WHEN (p0.mascara_no_aplica & 8)=8 THEN N'NO APLICA' ELSE CONVERT(nvarchar(10),n0.fecha_notificacion_reportada,23) END) COLLATE DATABASE_DEFAULT) AS [fecha_notificacion_paciente],
       COALESCE(x.[fecha_envio_paciente],CONVERT(nvarchar(2000),CASE WHEN (p0.mascara_no_aplica & 16)=16 THEN N'NO APLICA' ELSE CONVERT(nvarchar(10),n0.fecha_envio_reportada,23) END) COLLATE DATABASE_DEFAULT) AS [fecha_envio_paciente],
       COALESCE(x.[guia_paciente],CONVERT(nvarchar(2000),CASE WHEN (p0.mascara_no_aplica & 32)=32 THEN N'NO APLICA' ELSE n0.guia_reportada END) COLLATE DATABASE_DEFAULT) AS [guia_paciente],
       COALESCE(x.[fecha_recibido_paciente],CONVERT(nvarchar(2000),CASE WHEN (p0.mascara_no_aplica & 64)=64 THEN N'NO APLICA' ELSE CONVERT(nvarchar(10),n0.fecha_recibido_reportada,23) END) COLLATE DATABASE_DEFAULT) AS [fecha_recibido_paciente],
       COALESCE(x.[entidad_remitente],CONVERT(nvarchar(2000),rem.nombre) COLLATE DATABASE_DEFAULT) AS [entidad_remitente],
       COALESCE(x.[arl],CONVERT(nvarchar(2000),CASE WHEN (p1.mascara_no_aplica & 1)=1 THEN N'NO APLICA' ELSE e1.nombre END) COLLATE DATABASE_DEFAULT) AS [arl],
       COALESCE(x.[documento_notificacion_arl],CONVERT(nvarchar(2000),CASE WHEN (p1.mascara_no_aplica & 2)=2 THEN N'NO APLICA' ELSE n1.documento_notificacion END) COLLATE DATABASE_DEFAULT) AS [documento_notificacion_arl],
       COALESCE(x.[estado_arl],CONVERT(nvarchar(2000),CASE WHEN (p1.mascara_no_aplica & 4)=4 THEN N'NO APLICA' ELSE n1.estado_reportado END) COLLATE DATABASE_DEFAULT) AS [estado_arl],
       COALESCE(x.[fecha_notificacion_arl],CONVERT(nvarchar(2000),CASE WHEN (p1.mascara_no_aplica & 8)=8 THEN N'NO APLICA' ELSE CONVERT(nvarchar(10),n1.fecha_notificacion_reportada,23) END) COLLATE DATABASE_DEFAULT) AS [fecha_notificacion_arl],
       COALESCE(x.[fecha_envio_arl],CONVERT(nvarchar(2000),CASE WHEN (p1.mascara_no_aplica & 16)=16 THEN N'NO APLICA' ELSE CONVERT(nvarchar(10),n1.fecha_envio_reportada,23) END) COLLATE DATABASE_DEFAULT) AS [fecha_envio_arl],
       COALESCE(x.[guia_arl],CONVERT(nvarchar(2000),CASE WHEN (p1.mascara_no_aplica & 32)=32 THEN N'NO APLICA' ELSE n1.guia_reportada END) COLLATE DATABASE_DEFAULT) AS [guia_arl],
       COALESCE(x.[fecha_recibido_arl],CONVERT(nvarchar(2000),CASE WHEN (p1.mascara_no_aplica & 64)=64 THEN N'NO APLICA' ELSE CONVERT(nvarchar(10),n1.fecha_recibido_reportada,23) END) COLLATE DATABASE_DEFAULT) AS [fecha_recibido_arl],
       COALESCE(x.[afp],CONVERT(nvarchar(2000),CASE WHEN (p2.mascara_no_aplica & 1)=1 THEN N'NO APLICA' ELSE e2.nombre END) COLLATE DATABASE_DEFAULT) AS [afp],
       COALESCE(x.[documento_notificacion_afp],CONVERT(nvarchar(2000),CASE WHEN (p2.mascara_no_aplica & 2)=2 THEN N'NO APLICA' ELSE n2.documento_notificacion END) COLLATE DATABASE_DEFAULT) AS [documento_notificacion_afp],
       COALESCE(x.[estado_afp],CONVERT(nvarchar(2000),CASE WHEN (p2.mascara_no_aplica & 4)=4 THEN N'NO APLICA' ELSE n2.estado_reportado END) COLLATE DATABASE_DEFAULT) AS [estado_afp],
       COALESCE(x.[fecha_notificacion_afp],CONVERT(nvarchar(2000),CASE WHEN (p2.mascara_no_aplica & 8)=8 THEN N'NO APLICA' ELSE CONVERT(nvarchar(10),n2.fecha_notificacion_reportada,23) END) COLLATE DATABASE_DEFAULT) AS [fecha_notificacion_afp],
       COALESCE(x.[fecha_envio_afp],CONVERT(nvarchar(2000),CASE WHEN (p2.mascara_no_aplica & 16)=16 THEN N'NO APLICA' ELSE CONVERT(nvarchar(10),n2.fecha_envio_reportada,23) END) COLLATE DATABASE_DEFAULT) AS [fecha_envio_afp],
       COALESCE(x.[guia_afp],CONVERT(nvarchar(2000),CASE WHEN (p2.mascara_no_aplica & 32)=32 THEN N'NO APLICA' ELSE n2.guia_reportada END) COLLATE DATABASE_DEFAULT) AS [guia_afp],
       COALESCE(x.[fecha_recibido_afp],CONVERT(nvarchar(2000),CASE WHEN (p2.mascara_no_aplica & 64)=64 THEN N'NO APLICA' ELSE CONVERT(nvarchar(10),n2.fecha_recibido_reportada,23) END) COLLATE DATABASE_DEFAULT) AS [fecha_recibido_afp],
       COALESCE(x.[eps],CONVERT(nvarchar(2000),CASE WHEN (p3.mascara_no_aplica & 1)=1 THEN N'NO APLICA' ELSE e3.nombre END) COLLATE DATABASE_DEFAULT) AS [eps],
       COALESCE(x.[documento_notificacion_eps],CONVERT(nvarchar(2000),CASE WHEN (p3.mascara_no_aplica & 2)=2 THEN N'NO APLICA' ELSE n3.documento_notificacion END) COLLATE DATABASE_DEFAULT) AS [documento_notificacion_eps],
       COALESCE(x.[estado_eps],CONVERT(nvarchar(2000),CASE WHEN (p3.mascara_no_aplica & 4)=4 THEN N'NO APLICA' ELSE n3.estado_reportado END) COLLATE DATABASE_DEFAULT) AS [estado_eps],
       COALESCE(x.[fecha_notificacion_eps],CONVERT(nvarchar(2000),CASE WHEN (p3.mascara_no_aplica & 8)=8 THEN N'NO APLICA' ELSE CONVERT(nvarchar(10),n3.fecha_notificacion_reportada,23) END) COLLATE DATABASE_DEFAULT) AS [fecha_notificacion_eps],
       COALESCE(x.[fecha_envio_eps],CONVERT(nvarchar(2000),CASE WHEN (p3.mascara_no_aplica & 16)=16 THEN N'NO APLICA' ELSE CONVERT(nvarchar(10),n3.fecha_envio_reportada,23) END) COLLATE DATABASE_DEFAULT) AS [fecha_envio_eps],
       COALESCE(x.[guia_eps],CONVERT(nvarchar(2000),CASE WHEN (p3.mascara_no_aplica & 32)=32 THEN N'NO APLICA' ELSE n3.guia_reportada END) COLLATE DATABASE_DEFAULT) AS [guia_eps],
       COALESCE(x.[fecha_recibido_eps],CONVERT(nvarchar(2000),CASE WHEN (p3.mascara_no_aplica & 64)=64 THEN N'NO APLICA' ELSE CONVERT(nvarchar(10),n3.fecha_recibido_reportada,23) END) COLLATE DATABASE_DEFAULT) AS [fecha_recibido_eps],
       COALESCE(x.[aseguradora],CONVERT(nvarchar(2000),CASE WHEN (p4.mascara_no_aplica & 1)=1 THEN N'NO APLICA' ELSE e4.nombre END) COLLATE DATABASE_DEFAULT) AS [aseguradora],
       COALESCE(x.[documento_notificacion_aseguradora],CONVERT(nvarchar(2000),CASE WHEN (p4.mascara_no_aplica & 2)=2 THEN N'NO APLICA' ELSE n4.documento_notificacion END) COLLATE DATABASE_DEFAULT) AS [documento_notificacion_aseguradora],
       COALESCE(x.[estado_aseguradora],CONVERT(nvarchar(2000),CASE WHEN (p4.mascara_no_aplica & 4)=4 THEN N'NO APLICA' ELSE n4.estado_reportado END) COLLATE DATABASE_DEFAULT) AS [estado_aseguradora],
       COALESCE(x.[fecha_notificacion_aseguradora],CONVERT(nvarchar(2000),CASE WHEN (p4.mascara_no_aplica & 8)=8 THEN N'NO APLICA' ELSE CONVERT(nvarchar(10),n4.fecha_notificacion_reportada,23) END) COLLATE DATABASE_DEFAULT) AS [fecha_notificacion_aseguradora],
       COALESCE(x.[fecha_envio_aseguradora],CONVERT(nvarchar(2000),CASE WHEN (p4.mascara_no_aplica & 16)=16 THEN N'NO APLICA' ELSE CONVERT(nvarchar(10),n4.fecha_envio_reportada,23) END) COLLATE DATABASE_DEFAULT) AS [fecha_envio_aseguradora],
       COALESCE(x.[guia_aseguradora],CONVERT(nvarchar(2000),CASE WHEN (p4.mascara_no_aplica & 32)=32 THEN N'NO APLICA' ELSE n4.guia_reportada END) COLLATE DATABASE_DEFAULT) AS [guia_aseguradora],
       COALESCE(x.[fecha_recibido_aseguradora],CONVERT(nvarchar(2000),CASE WHEN (p4.mascara_no_aplica & 64)=64 THEN N'NO APLICA' ELSE CONVERT(nvarchar(10),n4.fecha_recibido_reportada,23) END) COLLATE DATABASE_DEFAULT) AS [fecha_recibido_aseguradora],
       COALESCE(x.[ultimo_empleador],CONVERT(nvarchar(2000),CASE WHEN (p5.mascara_no_aplica & 1)=1 THEN N'NO APLICA' ELSE e5.nombre END) COLLATE DATABASE_DEFAULT) AS [ultimo_empleador],
       COALESCE(x.[documento_notificacion_ultimoempleador],CONVERT(nvarchar(2000),CASE WHEN (p5.mascara_no_aplica & 2)=2 THEN N'NO APLICA' ELSE n5.documento_notificacion END) COLLATE DATABASE_DEFAULT) AS [documento_notificacion_ultimoempleador],
       COALESCE(x.[estado_ultimoempleador],CONVERT(nvarchar(2000),CASE WHEN (p5.mascara_no_aplica & 4)=4 THEN N'NO APLICA' ELSE n5.estado_reportado END) COLLATE DATABASE_DEFAULT) AS [estado_ultimoempleador],
       COALESCE(x.[fecha_notificacion_ultimoempleador],CONVERT(nvarchar(2000),CASE WHEN (p5.mascara_no_aplica & 8)=8 THEN N'NO APLICA' ELSE CONVERT(nvarchar(10),n5.fecha_notificacion_reportada,23) END) COLLATE DATABASE_DEFAULT) AS [fecha_notificacion_ultimoempleador],
       COALESCE(x.[fecha_envio_ultimoempleador],CONVERT(nvarchar(2000),CASE WHEN (p5.mascara_no_aplica & 16)=16 THEN N'NO APLICA' ELSE CONVERT(nvarchar(10),n5.fecha_envio_reportada,23) END) COLLATE DATABASE_DEFAULT) AS [fecha_envio_ultimoempleador],
       COALESCE(x.[guia_ultimoempleador],CONVERT(nvarchar(2000),CASE WHEN (p5.mascara_no_aplica & 32)=32 THEN N'NO APLICA' ELSE n5.guia_reportada END) COLLATE DATABASE_DEFAULT) AS [guia_ultimoempleador],
       COALESCE(x.[fecha_recibido_ultimoempleador],CONVERT(nvarchar(2000),CASE WHEN (p5.mascara_no_aplica & 64)=64 THEN N'NO APLICA' ELSE CONVERT(nvarchar(10),n5.fecha_recibido_reportada,23) END) COLLATE DATABASE_DEFAULT) AS [fecha_recibido_ultimoempleador],
       c.fecha_audiencia AS _fecha_audiencia,
       cd.fecha_fijacion AS _fecha_fijacion,
       cd.fecha_desfijacion AS _fecha_desfijacion,
       n0.fecha_envio_reportada AS _fecha_envio_paciente,
       n0.fecha_notificacion_reportada AS _fecha_notificacion_paciente,
       n1.fecha_envio_reportada AS _fecha_envio_arl,
       n1.fecha_notificacion_reportada AS _fecha_notificacion_arl,
       n2.fecha_envio_reportada AS _fecha_envio_afp,
       n2.fecha_notificacion_reportada AS _fecha_notificacion_afp,
       n3.fecha_envio_reportada AS _fecha_envio_eps,
       n3.fecha_notificacion_reportada AS _fecha_notificacion_eps,
       n4.fecha_envio_reportada AS _fecha_envio_aseguradora,
       n4.fecha_notificacion_reportada AS _fecha_notificacion_aseguradora,
       n5.fecha_envio_reportada AS _fecha_envio_ultimoempleador,
       n5.fecha_notificacion_reportada AS _fecha_notificacion_ultimoempleador
FROM ing.fila_origen f
JOIN ing.carga ca ON ca.id_carga=f.id_carga AND ca.estado='PUBLICADA'
JOIN ing.archivo a ON a.id_archivo=ca.id_archivo AND a.id_carga_vigente=ca.id_carga
JOIN cfg.perfil_entrada pe ON pe.id_perfil=f.id_perfil
JOIN core.caso c ON c.id_caso=f.id_caso
JOIN core.caso_dato cd ON cd.id_caso_dato=f.id_caso_dato
LEFT JOIN core.entidad rem ON rem.id_entidad=f.id_entidad_remitente
LEFT JOIN ing.parte_fila p0 ON p0.id_fila=f.id_fila AND p0.tipo_parte='paciente'
LEFT JOIN core.caso_parte cp0 ON cp0.id_caso_parte=p0.id_caso_parte
LEFT JOIN core.entidad e0 ON e0.id_entidad=cp0.id_entidad
LEFT JOIN core.notificacion_declarada n0 ON n0.id_notificacion=p0.id_notificacion
LEFT JOIN ing.parte_fila p1 ON p1.id_fila=f.id_fila AND p1.tipo_parte='arl'
LEFT JOIN core.caso_parte cp1 ON cp1.id_caso_parte=p1.id_caso_parte
LEFT JOIN core.entidad e1 ON e1.id_entidad=cp1.id_entidad
LEFT JOIN core.notificacion_declarada n1 ON n1.id_notificacion=p1.id_notificacion
LEFT JOIN ing.parte_fila p2 ON p2.id_fila=f.id_fila AND p2.tipo_parte='afp'
LEFT JOIN core.caso_parte cp2 ON cp2.id_caso_parte=p2.id_caso_parte
LEFT JOIN core.entidad e2 ON e2.id_entidad=cp2.id_entidad
LEFT JOIN core.notificacion_declarada n2 ON n2.id_notificacion=p2.id_notificacion
LEFT JOIN ing.parte_fila p3 ON p3.id_fila=f.id_fila AND p3.tipo_parte='eps'
LEFT JOIN core.caso_parte cp3 ON cp3.id_caso_parte=p3.id_caso_parte
LEFT JOIN core.entidad e3 ON e3.id_entidad=cp3.id_entidad
LEFT JOIN core.notificacion_declarada n3 ON n3.id_notificacion=p3.id_notificacion
LEFT JOIN ing.parte_fila p4 ON p4.id_fila=f.id_fila AND p4.tipo_parte='aseguradora'
LEFT JOIN core.caso_parte cp4 ON cp4.id_caso_parte=p4.id_caso_parte
LEFT JOIN core.entidad e4 ON e4.id_entidad=cp4.id_entidad
LEFT JOIN core.notificacion_declarada n4 ON n4.id_notificacion=p4.id_notificacion
LEFT JOIN ing.parte_fila p5 ON p5.id_fila=f.id_fila AND p5.tipo_parte='ultimoempleador'
LEFT JOIN core.caso_parte cp5 ON cp5.id_caso_parte=p5.id_caso_parte
LEFT JOIN core.entidad e5 ON e5.id_entidad=cp5.id_entidad
LEFT JOIN core.notificacion_declarada n5 ON n5.id_notificacion=p5.id_notificacion
OUTER APPLY (SELECT
 MAX(CASE WHEN v.campo='fecha_audiencia' THEN v.valor_original END) AS [fecha_audiencia],
 MAX(CASE WHEN v.campo='tipo_de_dictamen' THEN v.valor_original END) AS [tipo_de_dictamen],
 MAX(CASE WHEN v.campo='reparto_paciente' THEN v.valor_original END) AS [reparto_paciente],
 MAX(CASE WHEN v.campo='identificacion_paciente' THEN v.valor_original END) AS [identificacion_paciente],
 MAX(CASE WHEN v.campo='nombre_paciente' THEN v.valor_original END) AS [nombre_paciente],
 MAX(CASE WHEN v.campo='medico_ponente' THEN v.valor_original END) AS [medico_ponente],
 MAX(CASE WHEN v.campo='fecha_fijacion' THEN v.valor_original END) AS [fecha_fijacion],
 MAX(CASE WHEN v.campo='fecha_desfijacion' THEN v.valor_original END) AS [fecha_desfijacion],
 MAX(CASE WHEN v.campo='documento_notificacion_paciente' THEN v.valor_original END) AS [documento_notificacion_paciente],
 MAX(CASE WHEN v.campo='estado_paciente' THEN v.valor_original END) AS [estado_paciente],
 MAX(CASE WHEN v.campo='fecha_notificacion_paciente' THEN v.valor_original END) AS [fecha_notificacion_paciente],
 MAX(CASE WHEN v.campo='fecha_envio_paciente' THEN v.valor_original END) AS [fecha_envio_paciente],
 MAX(CASE WHEN v.campo='guia_paciente' THEN v.valor_original END) AS [guia_paciente],
 MAX(CASE WHEN v.campo='fecha_recibido_paciente' THEN v.valor_original END) AS [fecha_recibido_paciente],
 MAX(CASE WHEN v.campo='entidad_remitente' THEN v.valor_original END) AS [entidad_remitente],
 MAX(CASE WHEN v.campo='arl' THEN v.valor_original END) AS [arl],
 MAX(CASE WHEN v.campo='documento_notificacion_arl' THEN v.valor_original END) AS [documento_notificacion_arl],
 MAX(CASE WHEN v.campo='estado_arl' THEN v.valor_original END) AS [estado_arl],
 MAX(CASE WHEN v.campo='fecha_notificacion_arl' THEN v.valor_original END) AS [fecha_notificacion_arl],
 MAX(CASE WHEN v.campo='fecha_envio_arl' THEN v.valor_original END) AS [fecha_envio_arl],
 MAX(CASE WHEN v.campo='guia_arl' THEN v.valor_original END) AS [guia_arl],
 MAX(CASE WHEN v.campo='fecha_recibido_arl' THEN v.valor_original END) AS [fecha_recibido_arl],
 MAX(CASE WHEN v.campo='afp' THEN v.valor_original END) AS [afp],
 MAX(CASE WHEN v.campo='documento_notificacion_afp' THEN v.valor_original END) AS [documento_notificacion_afp],
 MAX(CASE WHEN v.campo='estado_afp' THEN v.valor_original END) AS [estado_afp],
 MAX(CASE WHEN v.campo='fecha_notificacion_afp' THEN v.valor_original END) AS [fecha_notificacion_afp],
 MAX(CASE WHEN v.campo='fecha_envio_afp' THEN v.valor_original END) AS [fecha_envio_afp],
 MAX(CASE WHEN v.campo='guia_afp' THEN v.valor_original END) AS [guia_afp],
 MAX(CASE WHEN v.campo='fecha_recibido_afp' THEN v.valor_original END) AS [fecha_recibido_afp],
 MAX(CASE WHEN v.campo='eps' THEN v.valor_original END) AS [eps],
 MAX(CASE WHEN v.campo='documento_notificacion_eps' THEN v.valor_original END) AS [documento_notificacion_eps],
 MAX(CASE WHEN v.campo='estado_eps' THEN v.valor_original END) AS [estado_eps],
 MAX(CASE WHEN v.campo='fecha_notificacion_eps' THEN v.valor_original END) AS [fecha_notificacion_eps],
 MAX(CASE WHEN v.campo='fecha_envio_eps' THEN v.valor_original END) AS [fecha_envio_eps],
 MAX(CASE WHEN v.campo='guia_eps' THEN v.valor_original END) AS [guia_eps],
 MAX(CASE WHEN v.campo='fecha_recibido_eps' THEN v.valor_original END) AS [fecha_recibido_eps],
 MAX(CASE WHEN v.campo='aseguradora' THEN v.valor_original END) AS [aseguradora],
 MAX(CASE WHEN v.campo='documento_notificacion_aseguradora' THEN v.valor_original END) AS [documento_notificacion_aseguradora],
 MAX(CASE WHEN v.campo='estado_aseguradora' THEN v.valor_original END) AS [estado_aseguradora],
 MAX(CASE WHEN v.campo='fecha_notificacion_aseguradora' THEN v.valor_original END) AS [fecha_notificacion_aseguradora],
 MAX(CASE WHEN v.campo='fecha_envio_aseguradora' THEN v.valor_original END) AS [fecha_envio_aseguradora],
 MAX(CASE WHEN v.campo='guia_aseguradora' THEN v.valor_original END) AS [guia_aseguradora],
 MAX(CASE WHEN v.campo='fecha_recibido_aseguradora' THEN v.valor_original END) AS [fecha_recibido_aseguradora],
 MAX(CASE WHEN v.campo='ultimo_empleador' THEN v.valor_original END) AS [ultimo_empleador],
 MAX(CASE WHEN v.campo='documento_notificacion_ultimoempleador' THEN v.valor_original END) AS [documento_notificacion_ultimoempleador],
 MAX(CASE WHEN v.campo='estado_ultimoempleador' THEN v.valor_original END) AS [estado_ultimoempleador],
 MAX(CASE WHEN v.campo='fecha_notificacion_ultimoempleador' THEN v.valor_original END) AS [fecha_notificacion_ultimoempleador],
 MAX(CASE WHEN v.campo='fecha_envio_ultimoempleador' THEN v.valor_original END) AS [fecha_envio_ultimoempleador],
 MAX(CASE WHEN v.campo='guia_ultimoempleador' THEN v.valor_original END) AS [guia_ultimoempleador],
 MAX(CASE WHEN v.campo='fecha_recibido_ultimoempleador' THEN v.valor_original END) AS [fecha_recibido_ultimoempleador]
FROM ing.valor_original v WHERE v.id_fila=f.id_fila AND v.mostrar_en_consolidado=1) x;
GO
CREATE OR ALTER VIEW rep.vw_consolidado_sabana
AS
SELECT
    b.[sala] AS [sala],
    b.[archivo_origen] AS [archivo_origen],
    b.[fila_origen] AS [fila_origen],
    b.[llave_caso] AS [llave_caso],
    b.[fecha_audiencia] AS [fecha_audiencia],
    b.[tipo_de_dictamen] AS [tipo_de_dictamen],
    b.[reparto_paciente] AS [reparto_paciente],
    b.[identificacion_paciente] AS [identificacion_paciente],
    b.[nombre_paciente] AS [nombre_paciente],
    b.[medico_ponente] AS [medico_ponente],
    b.[fecha_fijacion] AS [fecha_fijacion],
    b.[fecha_desfijacion] AS [fecha_desfijacion],
    b.[documento_notificacion_paciente] AS [documento_notificacion_paciente],
    b.[estado_paciente] AS [estado_paciente],
    b.[fecha_notificacion_paciente] AS [fecha_notificacion_paciente],
    b.[fecha_envio_paciente] AS [fecha_envio_paciente],
    b.[guia_paciente] AS [guia_paciente],
    b.[fecha_recibido_paciente] AS [fecha_recibido_paciente],
    b.[entidad_remitente] AS [entidad_remitente],
    b.[arl] AS [arl],
    b.[documento_notificacion_arl] AS [documento_notificacion_arl],
    b.[estado_arl] AS [estado_arl],
    b.[fecha_notificacion_arl] AS [fecha_notificacion_arl],
    b.[fecha_envio_arl] AS [fecha_envio_arl],
    b.[guia_arl] AS [guia_arl],
    b.[fecha_recibido_arl] AS [fecha_recibido_arl],
    b.[afp] AS [afp],
    b.[documento_notificacion_afp] AS [documento_notificacion_afp],
    b.[estado_afp] AS [estado_afp],
    b.[fecha_notificacion_afp] AS [fecha_notificacion_afp],
    b.[fecha_envio_afp] AS [fecha_envio_afp],
    b.[guia_afp] AS [guia_afp],
    b.[fecha_recibido_afp] AS [fecha_recibido_afp],
    b.[eps] AS [eps],
    b.[documento_notificacion_eps] AS [documento_notificacion_eps],
    b.[estado_eps] AS [estado_eps],
    b.[fecha_notificacion_eps] AS [fecha_notificacion_eps],
    b.[fecha_envio_eps] AS [fecha_envio_eps],
    b.[guia_eps] AS [guia_eps],
    b.[fecha_recibido_eps] AS [fecha_recibido_eps],
    b.[aseguradora] AS [aseguradora],
    b.[documento_notificacion_aseguradora] AS [documento_notificacion_aseguradora],
    b.[estado_aseguradora] AS [estado_aseguradora],
    b.[fecha_notificacion_aseguradora] AS [fecha_notificacion_aseguradora],
    b.[fecha_envio_aseguradora] AS [fecha_envio_aseguradora],
    b.[guia_aseguradora] AS [guia_aseguradora],
    b.[fecha_recibido_aseguradora] AS [fecha_recibido_aseguradora],
    b.[ultimo_empleador] AS [ultimo_empleador],
    b.[documento_notificacion_ultimoempleador] AS [documento_notificacion_ultimoempleador],
    b.[estado_ultimoempleador] AS [estado_ultimoempleador],
    b.[fecha_notificacion_ultimoempleador] AS [fecha_notificacion_ultimoempleador],
    b.[fecha_envio_ultimoempleador] AS [fecha_envio_ultimoempleador],
    b.[guia_ultimoempleador] AS [guia_ultimoempleador],
    b.[fecha_recibido_ultimoempleador] AS [fecha_recibido_ultimoempleador],
    DATEDIFF(day,b._fecha_audiencia,b._fecha_fijacion) AS [dias_cal_audiencia_a_fijacion],
    h0.dias_habiles AS [dias_hab_audiencia_a_fijacion],
    DATEDIFF(day,b._fecha_fijacion,b._fecha_desfijacion) AS [dias_cal_fijacion_a_desfijacion],
    h1.dias_habiles AS [dias_hab_fijacion_a_desfijacion],
    DATEDIFF(day,b._fecha_audiencia,b._fecha_envio_paciente) AS [dias_cal_audiencia_a_envio_paciente],
    h2.dias_habiles AS [dias_hab_audiencia_a_envio_paciente],
    DATEDIFF(day,b._fecha_audiencia,b._fecha_notificacion_paciente) AS [dias_cal_audiencia_a_notificacion_paciente],
    h3.dias_habiles AS [dias_hab_audiencia_a_notificacion_paciente],
    DATEDIFF(day,b._fecha_audiencia,b._fecha_envio_arl) AS [dias_cal_audiencia_a_envio_arl],
    h4.dias_habiles AS [dias_hab_audiencia_a_envio_arl],
    DATEDIFF(day,b._fecha_audiencia,b._fecha_notificacion_arl) AS [dias_cal_audiencia_a_notificacion_arl],
    h5.dias_habiles AS [dias_hab_audiencia_a_notificacion_arl],
    DATEDIFF(day,b._fecha_audiencia,b._fecha_envio_afp) AS [dias_cal_audiencia_a_envio_afp],
    h6.dias_habiles AS [dias_hab_audiencia_a_envio_afp],
    DATEDIFF(day,b._fecha_audiencia,b._fecha_notificacion_afp) AS [dias_cal_audiencia_a_notificacion_afp],
    h7.dias_habiles AS [dias_hab_audiencia_a_notificacion_afp],
    DATEDIFF(day,b._fecha_audiencia,b._fecha_envio_eps) AS [dias_cal_audiencia_a_envio_eps],
    h8.dias_habiles AS [dias_hab_audiencia_a_envio_eps],
    DATEDIFF(day,b._fecha_audiencia,b._fecha_notificacion_eps) AS [dias_cal_audiencia_a_notificacion_eps],
    h9.dias_habiles AS [dias_hab_audiencia_a_notificacion_eps],
    DATEDIFF(day,b._fecha_audiencia,b._fecha_envio_aseguradora) AS [dias_cal_audiencia_a_envio_aseguradora],
    h10.dias_habiles AS [dias_hab_audiencia_a_envio_aseguradora],
    DATEDIFF(day,b._fecha_audiencia,b._fecha_notificacion_aseguradora) AS [dias_cal_audiencia_a_notificacion_aseguradora],
    h11.dias_habiles AS [dias_hab_audiencia_a_notificacion_aseguradora],
    DATEDIFF(day,b._fecha_audiencia,b._fecha_envio_ultimoempleador) AS [dias_cal_audiencia_a_envio_ultimoempleador],
    h12.dias_habiles AS [dias_hab_audiencia_a_envio_ultimoempleador],
    DATEDIFF(day,b._fecha_audiencia,b._fecha_notificacion_ultimoempleador) AS [dias_cal_audiencia_a_notificacion_ultimoempleador],
    h13.dias_habiles AS [dias_hab_audiencia_a_notificacion_ultimoempleador],
    CONVERT(nvarchar(10),primera.fecha,23) AS [fecha_primer_envio_partes],
    DATEDIFF(day,b._fecha_audiencia,primera.fecha) AS [dias_cal_audiencia_a_primer_envio_partes],
    h14.dias_habiles AS [dias_hab_audiencia_a_primer_envio_partes]
FROM rep.vw_fila_base b
OUTER APPLY (SELECT MIN(v.fecha) AS fecha FROM (VALUES (b._fecha_envio_paciente),(b._fecha_envio_arl),(b._fecha_envio_afp),(b._fecha_envio_eps),(b._fecha_envio_aseguradora),(b._fecha_envio_ultimoempleador)) v(fecha)) primera
OUTER APPLY cfg.fn_dias_habiles(b.id_calendario,b._fecha_audiencia,b._fecha_fijacion) h0
OUTER APPLY cfg.fn_dias_habiles(b.id_calendario,b._fecha_fijacion,b._fecha_desfijacion) h1
OUTER APPLY cfg.fn_dias_habiles(b.id_calendario,b._fecha_audiencia,b._fecha_envio_paciente) h2
OUTER APPLY cfg.fn_dias_habiles(b.id_calendario,b._fecha_audiencia,b._fecha_notificacion_paciente) h3
OUTER APPLY cfg.fn_dias_habiles(b.id_calendario,b._fecha_audiencia,b._fecha_envio_arl) h4
OUTER APPLY cfg.fn_dias_habiles(b.id_calendario,b._fecha_audiencia,b._fecha_notificacion_arl) h5
OUTER APPLY cfg.fn_dias_habiles(b.id_calendario,b._fecha_audiencia,b._fecha_envio_afp) h6
OUTER APPLY cfg.fn_dias_habiles(b.id_calendario,b._fecha_audiencia,b._fecha_notificacion_afp) h7
OUTER APPLY cfg.fn_dias_habiles(b.id_calendario,b._fecha_audiencia,b._fecha_envio_eps) h8
OUTER APPLY cfg.fn_dias_habiles(b.id_calendario,b._fecha_audiencia,b._fecha_notificacion_eps) h9
OUTER APPLY cfg.fn_dias_habiles(b.id_calendario,b._fecha_audiencia,b._fecha_envio_aseguradora) h10
OUTER APPLY cfg.fn_dias_habiles(b.id_calendario,b._fecha_audiencia,b._fecha_notificacion_aseguradora) h11
OUTER APPLY cfg.fn_dias_habiles(b.id_calendario,b._fecha_audiencia,b._fecha_envio_ultimoempleador) h12
OUTER APPLY cfg.fn_dias_habiles(b.id_calendario,b._fecha_audiencia,b._fecha_notificacion_ultimoempleador) h13
OUTER APPLY cfg.fn_dias_habiles(b.id_calendario,b._fecha_audiencia,primera.fecha) h14;
GO
CREATE OR ALTER VIEW rep.vw_notificacion_declarada_vigente
AS
/* Una misma observacion tipada puede tener multiples filas de procedencia.
   No selecciona arbitrariamente una version si fuentes vigentes se contradicen. */
SELECT DISTINCT nd.id_notificacion,cp.id_caso,cp.id_caso_parte,cp.tipo_parte,cp.id_entidad,
 nd.documento_notificacion,nd.estado_reportado,nd.fecha_notificacion_reportada,
 nd.fecha_envio_reportada,nd.guia_reportada,nd.fecha_recibido_reportada
FROM ing.parte_fila pf
JOIN ing.fila_origen f ON f.id_fila=pf.id_fila
JOIN ing.carga ca ON ca.id_carga=f.id_carga AND ca.estado='PUBLICADA'
JOIN ing.archivo a ON a.id_archivo=ca.id_archivo AND a.id_carga_vigente=ca.id_carga
JOIN core.caso_parte cp ON cp.id_caso_parte=pf.id_caso_parte
JOIN core.notificacion_declarada nd ON nd.id_notificacion=pf.id_notificacion;
GO
