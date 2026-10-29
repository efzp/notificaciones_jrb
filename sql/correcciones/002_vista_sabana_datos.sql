/* Separacion de consultas sin duplicar datos almacenados.
   Aplicar sobre el modelo existente, despues de sql/03_vistas.sql.
   No modifica tablas, cargas ni la Function.
   rep.vw_sabana_datos: 50 campos Excel + 4 campos de origen/caso.
   rep.vw_consolidado_sabana conserva sus 85 columnas actuales.
*/
SET ANSI_NULLS ON;
SET QUOTED_IDENTIFIER ON;
GO
CREATE OR ALTER VIEW rep.vw_sabana_datos
AS
SELECT
    b.[sala],
    b.[archivo_origen],
    b.[fila_origen],
    b.[llave_caso],
    b.[fecha_audiencia],
    b.[tipo_de_dictamen],
    b.[reparto_paciente],
    b.[identificacion_paciente],
    b.[nombre_paciente],
    b.[medico_ponente],
    b.[fecha_fijacion],
    b.[fecha_desfijacion],
    b.[documento_notificacion_paciente],
    b.[estado_paciente],
    b.[fecha_notificacion_paciente],
    b.[fecha_envio_paciente],
    b.[guia_paciente],
    b.[fecha_recibido_paciente],
    b.[entidad_remitente],
    b.[arl],
    b.[documento_notificacion_arl],
    b.[estado_arl],
    b.[fecha_notificacion_arl],
    b.[fecha_envio_arl],
    b.[guia_arl],
    b.[fecha_recibido_arl],
    b.[afp],
    b.[documento_notificacion_afp],
    b.[estado_afp],
    b.[fecha_notificacion_afp],
    b.[fecha_envio_afp],
    b.[guia_afp],
    b.[fecha_recibido_afp],
    b.[eps],
    b.[documento_notificacion_eps],
    b.[estado_eps],
    b.[fecha_notificacion_eps],
    b.[fecha_envio_eps],
    b.[guia_eps],
    b.[fecha_recibido_eps],
    b.[aseguradora],
    b.[documento_notificacion_aseguradora],
    b.[estado_aseguradora],
    b.[fecha_notificacion_aseguradora],
    b.[fecha_envio_aseguradora],
    b.[guia_aseguradora],
    b.[fecha_recibido_aseguradora],
    b.[ultimo_empleador],
    b.[documento_notificacion_ultimoempleador],
    b.[estado_ultimoempleador],
    b.[fecha_notificacion_ultimoempleador],
    b.[fecha_envio_ultimoempleador],
    b.[guia_ultimoempleador],
    b.[fecha_recibido_ultimoempleador]
FROM rep.vw_fila_base b;
GO
