/* Catalogo inicial. Calendario de COMPATIBILIDAD con el script recibido:
   fines de semana y SOLO festivos 2026 de su configuracion original.
   Los dias 2025/2027 no incorporan otros festivos: no es calendario legal multianual.
   Para cambiar calendario, parsers o mapping, crear un perfil nuevo; no editar el publicado. */
SET NOCOUNT ON;
SET XACT_ABORT ON;
BEGIN TRY
BEGIN TRANSACTION;
IF EXISTS(SELECT 1 FROM cfg.perfil_entrada) OR EXISTS(SELECT 1 FROM cfg.calendario)
    THROW 51002,N'Catalogos iniciales ya cargados; use una migracion versionada.',1;
INSERT cfg.calendario(id_calendario,nombre,version,fecha_desde,fecha_hasta,descripcion,publicado)
VALUES(1,N'COMPAT_SCRIPT_SABANA_2026',1,'20250101','20271231',
 N'Reproduce los festivos 2026 del script original. Fuera de 2026 solo excluye sabados y domingos. No certifica terminos legales.',0);
DECLARE @festivos TABLE(fecha date PRIMARY KEY,nombre nvarchar(120));
INSERT @festivos VALUES
('20260101',N'Ano Nuevo'),('20260112',N'Reyes Magos'),('20260323',N'San Jose'),
('20260402',N'Jueves Santo'),('20260403',N'Viernes Santo'),('20260501',N'Dia del Trabajo'),
('20260518',N'Ascension'),('20260608',N'Corpus Christi'),('20260615',N'Sagrado Corazon'),
('20260629',N'San Pedro y San Pablo'),('20260720',N'Independencia'),('20260807',N'Batalla de Boyaca'),
('20260817',N'Asuncion'),('20261012',N'Diversidad Etnica y Cultural'),('20261102',N'Todos los Santos'),
('20261116',N'Independencia de Cartagena'),('20261208',N'Inmaculada Concepcion'),('20261225',N'Navidad');
;WITH fechas AS (
 SELECT CONVERT(date,'20250101',112) AS fecha
 UNION ALL SELECT DATEADD(day,1,fecha) FROM fechas WHERE fecha<CONVERT(date,'20271231',112)
), clasificado AS (
 SELECT f.fecha,CONVERT(bit,CASE WHEN DATEDIFF(day,CONVERT(date,'19000101',112),f.fecha)%7<5
     AND fest.fecha IS NULL THEN 1 ELSE 0 END) AS es_habil,fest.nombre
 FROM fechas f LEFT JOIN @festivos fest ON fest.fecha=f.fecha
)
INSERT cfg.dia_calendario(id_calendario,fecha,es_habil,acumulado_habil,nombre_festivo)
SELECT 1,fecha,es_habil,SUM(CONVERT(int,es_habil)) OVER(ORDER BY fecha ROWS UNBOUNDED PRECEDING),nombre
FROM clasificado OPTION(MAXRECURSION 0);
UPDATE cfg.calendario SET publicado=1 WHERE id_calendario=1;
INSERT cfg.perfil_entrada(id_perfil,nombre,version,version_parser,version_modelo,id_calendario,fecha_minima,fecha_maxima,publicado)
VALUES(1,'SABANA_CONTROL',1,'sabana-fechas/1.0','1.0.0',1,'20250101','20271231',0);
INSERT cfg.campo_entrada(id_perfil,campo,orden,tipo_dato,obligatorio,longitud_maxima,tipo_parte,bit_no_aplica)
VALUES
(1,'fecha_audiencia',1,'FECHA',1,NULL,NULL,NULL),
(1,'tipo_de_dictamen',2,'TEXTO',0,100,NULL,NULL),
(1,'reparto_paciente',3,'IDENTIFICADOR',1,100,NULL,NULL),
(1,'identificacion_paciente',4,'IDENTIFICADOR',1,50,NULL,NULL),
(1,'nombre_paciente',5,'TEXTO',0,500,NULL,NULL),
(1,'medico_ponente',6,'TEXTO',0,500,NULL,NULL),
(1,'fecha_fijacion',7,'FECHA',0,NULL,NULL,NULL),
(1,'fecha_desfijacion',8,'FECHA',0,NULL,NULL,NULL),
(1,'documento_notificacion_paciente',9,'TEXTO',0,100,N'paciente',2),
(1,'estado_paciente',10,'TEXTO',0,100,N'paciente',4),
(1,'fecha_notificacion_paciente',11,'FECHA',0,NULL,N'paciente',8),
(1,'fecha_envio_paciente',12,'FECHA',0,NULL,N'paciente',16),
(1,'guia_paciente',13,'TEXTO',0,150,N'paciente',32),
(1,'fecha_recibido_paciente',14,'FECHA',0,NULL,N'paciente',64),
(1,'entidad_remitente',15,'TEXTO',0,500,NULL,NULL),
(1,'arl',16,'TEXTO',0,500,N'arl',1),
(1,'documento_notificacion_arl',17,'TEXTO',0,100,N'arl',2),
(1,'estado_arl',18,'TEXTO',0,100,N'arl',4),
(1,'fecha_notificacion_arl',19,'FECHA',0,NULL,N'arl',8),
(1,'fecha_envio_arl',20,'FECHA',0,NULL,N'arl',16),
(1,'guia_arl',21,'TEXTO',0,150,N'arl',32),
(1,'fecha_recibido_arl',22,'FECHA',0,NULL,N'arl',64),
(1,'afp',23,'TEXTO',0,500,N'afp',1),
(1,'documento_notificacion_afp',24,'TEXTO',0,100,N'afp',2),
(1,'estado_afp',25,'TEXTO',0,100,N'afp',4),
(1,'fecha_notificacion_afp',26,'FECHA',0,NULL,N'afp',8),
(1,'fecha_envio_afp',27,'FECHA',0,NULL,N'afp',16),
(1,'guia_afp',28,'TEXTO',0,150,N'afp',32),
(1,'fecha_recibido_afp',29,'FECHA',0,NULL,N'afp',64),
(1,'eps',30,'TEXTO',0,500,N'eps',1),
(1,'documento_notificacion_eps',31,'TEXTO',0,100,N'eps',2),
(1,'estado_eps',32,'TEXTO',0,100,N'eps',4),
(1,'fecha_notificacion_eps',33,'FECHA',0,NULL,N'eps',8),
(1,'fecha_envio_eps',34,'FECHA',0,NULL,N'eps',16),
(1,'guia_eps',35,'TEXTO',0,150,N'eps',32),
(1,'fecha_recibido_eps',36,'FECHA',0,NULL,N'eps',64),
(1,'aseguradora',37,'TEXTO',0,500,N'aseguradora',1),
(1,'documento_notificacion_aseguradora',38,'TEXTO',0,100,N'aseguradora',2),
(1,'estado_aseguradora',39,'TEXTO',0,100,N'aseguradora',4),
(1,'fecha_notificacion_aseguradora',40,'FECHA',0,NULL,N'aseguradora',8),
(1,'fecha_envio_aseguradora',41,'FECHA',0,NULL,N'aseguradora',16),
(1,'guia_aseguradora',42,'TEXTO',0,150,N'aseguradora',32),
(1,'fecha_recibido_aseguradora',43,'FECHA',0,NULL,N'aseguradora',64),
(1,'ultimo_empleador',44,'TEXTO',0,500,N'ultimoempleador',1),
(1,'documento_notificacion_ultimoempleador',45,'TEXTO',0,100,N'ultimoempleador',2),
(1,'estado_ultimoempleador',46,'TEXTO',0,100,N'ultimoempleador',4),
(1,'fecha_notificacion_ultimoempleador',47,'FECHA',0,NULL,N'ultimoempleador',8),
(1,'fecha_envio_ultimoempleador',48,'FECHA',0,NULL,N'ultimoempleador',16),
(1,'guia_ultimoempleador',49,'TEXTO',0,150,N'ultimoempleador',32),
(1,'fecha_recibido_ultimoempleador',50,'FECHA',0,NULL,N'ultimoempleador',64);
INSERT cfg.alias_columna(id_perfil,alias_normalizado,campo) SELECT id_perfil,CONVERT(nvarchar(255),campo),campo FROM cfg.campo_entrada;
UPDATE cfg.perfil_entrada SET publicado=1 WHERE id_perfil=1;
COMMIT TRANSACTION;
END TRY
BEGIN CATCH
IF XACT_STATE()<>0 ROLLBACK TRANSACTION;
THROW;
END CATCH;
