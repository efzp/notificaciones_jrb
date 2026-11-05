/* Contactos por cedula. Ejecutar en sqldb-jrb-sabana-dev.
   Prioridad: CONSOLIDADO > CANOPUS (paciente/empleador) > MAESTRO_TERCEROS.
   Fecha de audiencia y reparto son procedencia, no claves de vinculacion.
   No carga archivos ni modifica el procesador actualmente desplegado. */
SET NOCOUNT ON;
SET XACT_ABORT ON;
SET ANSI_NULLS ON;
SET ANSI_PADDING ON;
SET ANSI_WARNINGS ON;
SET ARITHABORT ON;
SET CONCAT_NULL_YIELDS_NULL ON;
SET QUOTED_IDENTIFIER ON;
SET NUMERIC_ROUNDABORT OFF;

IF DB_NAME()<>N'sqldb-jrb-sabana-dev'
    THROW 51860,N'Ejecutar en sqldb-jrb-sabana-dev.',1;
IF OBJECT_ID(N'ref.entidad_equivalencia',N'U') IS NULL
    OR OBJECT_ID(N'mail.direccion',N'U') IS NULL
    THROW 51861,N'Falta el modelo de terceros o de direcciones.',1;

BEGIN TRY
    BEGIN TRANSACTION;
    DECLARE @bloqueo int;
    EXEC @bloqueo=sys.sp_getapplock
        @Resource=N'terceros:migracion',@LockMode='Exclusive',
        @LockOwner='Transaction',@LockTimeout=10000;
    IF @bloqueo<0 THROW 51862,N'Otra migracion esta en ejecucion.',1;

    IF OBJECT_ID(N'ing.canopus_carga',N'U') IS NULL
    BEGIN
        CREATE TABLE ing.canopus_carga (
            id_carga_canopus int IDENTITY(1,1) NOT NULL
                CONSTRAINT PK_canopus_carga PRIMARY KEY,
            drive_id nvarchar(128) COLLATE Latin1_General_100_BIN2 NOT NULL,
            item_id nvarchar(128) COLLATE Latin1_General_100_BIN2 NOT NULL,
            nombre_archivo nvarchar(255) NOT NULL,
            version_origen nvarchar(128) NOT NULL,
            hash_contenido binary(32) NOT NULL,
            version_parser varchar(32) NOT NULL,
            modificado_origen_utc datetime2(3) NULL,
            estado varchar(12) NOT NULL
                CONSTRAINT DF_canopus_carga_estado DEFAULT('RECIBIDA'),
            vigente bit NOT NULL CONSTRAINT DF_canopus_carga_vigente DEFAULT(0),
            creado_utc datetime2(3) NOT NULL
                CONSTRAINT DF_canopus_carga_creado DEFAULT(SYSUTCDATETIME()),
            publicado_utc datetime2(3) NULL,
            filas_procesadas int NULL,
            CONSTRAINT UQ_canopus_carga_contenido
                UNIQUE(drive_id,item_id,hash_contenido,version_parser),
            CONSTRAINT CK_canopus_carga_textos CHECK(
                LEN(LTRIM(RTRIM(drive_id)))>0 AND LEN(LTRIM(RTRIM(item_id)))>0
                AND LEN(LTRIM(RTRIM(nombre_archivo)))>0
                AND LEN(LTRIM(RTRIM(version_origen)))>0
                AND LEN(LTRIM(RTRIM(version_parser)))>0),
            CONSTRAINT CK_canopus_carga_estado CHECK(
                estado IN('RECIBIDA','PROCESANDO','PUBLICADA','ERROR')),
            CONSTRAINT CK_canopus_carga_publicada CHECK(
                (vigente=0 OR estado='PUBLICADA')
                AND (estado<>'PUBLICADA' OR
                    (publicado_utc IS NOT NULL AND filas_procesadas IS NOT NULL))
                AND (publicado_utc IS NULL OR publicado_utc>=creado_utc)
                AND (filas_procesadas IS NULL OR filas_procesadas>=0))
        );
        CREATE UNIQUE INDEX UX_canopus_carga_vigente
            ON ing.canopus_carga(drive_id,item_id) WHERE vigente=1;
    END;

    IF OBJECT_ID(N'ing.canopus_fila',N'U') IS NULL
    BEGIN
        CREATE TABLE ing.canopus_fila (
            id_fila_canopus bigint IDENTITY(1,1) NOT NULL
                CONSTRAINT PK_canopus_fila PRIMARY KEY,
            id_carga_canopus int NOT NULL,
            hoja nvarchar(128) COLLATE Latin1_General_100_BIN2 NOT NULL,
            numero_fila int NOT NULL,
            cedula nvarchar(50) COLLATE Latin1_General_100_BIN2 NOT NULL,
            fecha_audiencia_reportada date NULL,
            reparto_reportado nvarchar(100) NULL,
            empleador_reportado nvarchar(500) NULL,
            id_entidad_empleador int NULL,
            estado_empleador varchar(12) NOT NULL
                CONSTRAINT DF_canopus_fila_empleador DEFAULT('PENDIENTE'),
            CONSTRAINT FK_canopus_fila_carga FOREIGN KEY(id_carga_canopus)
                REFERENCES ing.canopus_carga(id_carga_canopus),
            CONSTRAINT FK_canopus_fila_entidad FOREIGN KEY(id_entidad_empleador)
                REFERENCES core.entidad(id_entidad),
            CONSTRAINT UQ_canopus_fila_posicion UNIQUE(id_carga_canopus,hoja,numero_fila),
            CONSTRAINT CK_canopus_fila_identidad CHECK(
                numero_fila>=2 AND LEN(LTRIM(RTRIM(hoja)))>0 AND LEN(cedula)>0
                AND DATALENGTH(cedula)=DATALENGTH(LTRIM(RTRIM(cedula)))),
            CONSTRAINT CK_canopus_fila_empleador CHECK(
                (estado_empleador='ASOCIADO' AND id_entidad_empleador IS NOT NULL) OR
                (estado_empleador IN('PENDIENTE','SIN_ENTIDAD','AMBIGUO','DISCREPANTE','NO_APLICA')
                    AND id_entidad_empleador IS NULL))
        );
        CREATE INDEX IX_canopus_fila_cedula ON ing.canopus_fila(cedula)
            INCLUDE(id_carga_canopus,id_entidad_empleador);
    END;

    -- Comun a Canopus y consolidado; las direcciones se reutilizan en mail.direccion.
    IF OBJECT_ID(N'ref.contacto_reportado',N'U') IS NULL
    BEGIN
        CREATE TABLE ref.contacto_reportado (
            id_contacto_reportado bigint IDENTITY(1,1) NOT NULL
                CONSTRAINT PK_contacto_reportado PRIMARY KEY,
            origen varchar(12) NOT NULL,
            id_fila_canopus bigint NULL,
            id_fila_consolidado bigint NULL,
            tipo_parte varchar(20) NOT NULL,
            campo_origen nvarchar(128) NOT NULL,
            id_direccion int NULL,
            estado varchar(12) NOT NULL
                CONSTRAINT DF_contacto_reportado_estado DEFAULT('REVISION'),
            valor_no_utilizable nvarchar(1024) NULL,
            codigo_revision varchar(64) NULL,
            creado_utc datetime2(3) NOT NULL
                CONSTRAINT DF_contacto_reportado_creado DEFAULT(SYSUTCDATETIME()),
            CONSTRAINT FK_contacto_reportado_canopus FOREIGN KEY(id_fila_canopus)
                REFERENCES ing.canopus_fila(id_fila_canopus),
            CONSTRAINT FK_contacto_reportado_consolidado
                FOREIGN KEY(id_fila_consolidado,tipo_parte)
                REFERENCES ing.parte_fila(id_fila,tipo_parte),
            CONSTRAINT FK_contacto_reportado_direccion FOREIGN KEY(id_direccion)
                REFERENCES mail.direccion(id_direccion),
            CONSTRAINT CK_contacto_reportado_origen CHECK(
                (origen='CANOPUS' AND id_fila_canopus IS NOT NULL
                    AND id_fila_consolidado IS NULL
                    AND tipo_parte IN('paciente','ultimoempleador','por_definir')) OR
                (origen='CONSOLIDADO' AND id_fila_consolidado IS NOT NULL
                    AND id_fila_canopus IS NULL
                    AND tipo_parte IN('paciente','arl','afp','eps','aseguradora','ultimoempleador'))),
            CONSTRAINT CK_contacto_reportado_estado CHECK(
                estado IN('UTILIZABLE','REVISION','DESCARTADO')
                AND (tipo_parte<>'por_definir' OR estado<>'UTILIZABLE')),
            CONSTRAINT CK_contacto_reportado_campo CHECK(LEN(LTRIM(RTRIM(campo_origen)))>0),
            CONSTRAINT CK_contacto_reportado_valor CHECK(
                (id_direccion IS NOT NULL AND valor_no_utilizable IS NULL) OR
                (id_direccion IS NULL AND valor_no_utilizable IS NOT NULL
                    AND LEN(LTRIM(RTRIM(valor_no_utilizable)))>0
                    AND estado<>'UTILIZABLE' AND codigo_revision IS NOT NULL))
        );
        CREATE UNIQUE INDEX UX_contacto_reportado_canopus
            ON ref.contacto_reportado(id_fila_canopus,tipo_parte,id_direccion,campo_origen)
            WHERE id_fila_canopus IS NOT NULL AND id_direccion IS NOT NULL;
        CREATE UNIQUE INDEX UX_contacto_reportado_consolidado
            ON ref.contacto_reportado(id_fila_consolidado,tipo_parte,id_direccion,campo_origen)
            WHERE id_fila_consolidado IS NOT NULL AND id_direccion IS NOT NULL;
    END;

    -- Acelera el enlace del ID original del tercero con su representante.
    IF NOT EXISTS(SELECT 1 FROM sys.indexes
        WHERE object_id=OBJECT_ID(N'ref.entidad_equivalencia')
          AND name=N'IX_equivalencia_contacto_origen')
        CREATE INDEX IX_equivalencia_contacto_origen
            ON ref.entidad_equivalencia(id_carga_terceros,origen,tipo_parte,id_entidad_origen)
            INCLUDE(id_entidad_representante) WHERE id_entidad_origen IS NOT NULL;

    EXEC(N'CREATE OR ALTER VIEW rep.vw_contactos_caso AS
    WITH corte AS (
        SELECT id_carga_terceros FROM ing.terceros_publicacion WHERE id=1
    ), partes_base AS (
        SELECT DISTINCT cp.id_caso_parte,cp.id_caso,cp.tipo_parte,
            cp.id_entidad,c.identificacion_paciente AS cedula
        FROM ing.archivo a
        JOIN ing.carga ca ON ca.id_carga=a.id_carga_vigente AND ca.estado=''PUBLICADA''
        JOIN ing.fila_origen f ON f.id_carga=ca.id_carga
        JOIN ing.parte_fila pf ON pf.id_fila=f.id_fila
        JOIN core.caso_parte cp ON cp.id_caso_parte=pf.id_caso_parte
        JOIN core.caso c ON c.id_caso=cp.id_caso
    ), partes AS (
        SELECT p.*,COALESCE(e.id_entidad_representante,p.id_entidad) AS id_entidad_referencia,
            co.id_carga_terceros
        FROM partes_base p LEFT JOIN corte co ON 1=1
        LEFT JOIN ref.entidad_equivalencia e
            ON e.id_carga_terceros=co.id_carga_terceros AND e.origen=''JRB''
            AND e.tipo_parte=p.tipo_parte AND e.id_entidad_origen=p.id_entidad
    ), declarados AS (
        SELECT r.id_contacto_reportado,r.origen,r.tipo_parte,r.id_direccion,d.direccion AS correo,
            COALESCE(cf.cedula,c.identificacion_paciente) AS cedula,
            COALESCE(eq.id_entidad_representante,
                CASE WHEN r.origen=''CANOPUS'' THEN cf.id_entidad_empleador ELSE cp.id_entidad END)
                AS id_entidad_referencia,
            CASE WHEN r.origen=''CONSOLIDADO'' THEN 10 ELSE 20 END AS prioridad
        FROM ref.contacto_reportado r
        JOIN mail.direccion d ON d.id_direccion=r.id_direccion AND d.tipo=''SMTP''
        LEFT JOIN ing.canopus_fila cf ON cf.id_fila_canopus=r.id_fila_canopus
        LEFT JOIN ing.canopus_carga cc ON cc.id_carga_canopus=cf.id_carga_canopus
        LEFT JOIN ing.fila_origen f ON f.id_fila=r.id_fila_consolidado
        LEFT JOIN ing.carga ca ON ca.id_carga=f.id_carga
        LEFT JOIN ing.archivo a ON a.id_archivo=ca.id_archivo
        LEFT JOIN core.caso c ON c.id_caso=f.id_caso
        LEFT JOIN ing.parte_fila pf ON pf.id_fila=f.id_fila AND pf.tipo_parte=r.tipo_parte
        LEFT JOIN core.caso_parte cp ON cp.id_caso_parte=pf.id_caso_parte
        LEFT JOIN corte co ON 1=1
        LEFT JOIN ref.entidad_equivalencia eq
            ON eq.id_carga_terceros=co.id_carga_terceros AND eq.origen=''JRB''
            AND eq.tipo_parte=r.tipo_parte
            AND eq.id_entidad_origen=
                CASE WHEN r.origen=''CANOPUS'' THEN cf.id_entidad_empleador ELSE cp.id_entidad END
        WHERE r.estado=''UTILIZABLE'' AND r.tipo_parte<>''por_definir'' AND (
            (r.origen=''CANOPUS'' AND cc.estado=''PUBLICADA'' AND cc.vigente=1) OR
            (r.origen=''CONSOLIDADO'' AND ca.estado=''PUBLICADA'' AND a.id_carga_vigente=ca.id_carga))
    ), candidatos AS (
        SELECT p.id_caso_parte,p.id_caso,p.cedula,p.tipo_parte,p.id_entidad,
            p.id_entidad_referencia,d.id_direccion,d.correo,CAST(d.origen AS varchar(20)) AS origen,
            d.prioridad,d.id_contacto_reportado,CAST(NULL AS int) AS id_entidad_correo,
            p.id_carga_terceros
        FROM partes p JOIN declarados d ON d.cedula=p.cedula AND d.tipo_parte=p.tipo_parte
        WHERE p.tipo_parte=''paciente'' OR
            (p.id_entidad_referencia IS NOT NULL AND p.id_entidad_referencia=d.id_entidad_referencia)
        UNION ALL
        SELECT DISTINCT p.id_caso_parte,p.id_caso,p.cedula,p.tipo_parte,p.id_entidad,
            p.id_entidad_referencia,m.id_direccion,d.direccion,CAST(''MAESTRO_TERCEROS'' AS varchar(20)),
            30,CAST(NULL AS bigint),m.id_entidad_correo,p.id_carga_terceros
        FROM partes p
        JOIN ref.entidad_correo m ON m.id_entidad=p.id_entidad_referencia AND m.tipo_parte=p.tipo_parte
        JOIN ref.entidad_correo_fuente mf ON mf.id_carga_terceros=p.id_carga_terceros
            AND mf.id_entidad_correo=m.id_entidad_correo
        JOIN mail.direccion d ON d.id_direccion=m.id_direccion AND d.tipo=''SMTP''
    ), priorizados AS (
        SELECT *,MIN(prioridad) OVER(PARTITION BY id_caso,id_caso_parte,tipo_parte) AS prioridad_preferida
        FROM candidatos
    )
    SELECT id_caso_parte,id_caso,cedula,tipo_parte,id_entidad,id_entidad_referencia,
        id_direccion,correo,origen,prioridad,id_contacto_reportado,id_entidad_correo,id_carga_terceros
    FROM priorizados WHERE prioridad=prioridad_preferida;');

    IF DATABASE_PRINCIPAL_ID(N'rol_carga_terceros') IS NOT NULL
    BEGIN
        GRANT SELECT,INSERT,UPDATE ON OBJECT::ing.canopus_carga TO rol_carga_terceros;
        GRANT SELECT,INSERT,UPDATE ON OBJECT::ing.canopus_fila TO rol_carga_terceros;
        GRANT SELECT,INSERT,UPDATE ON OBJECT::ref.contacto_reportado TO rol_carga_terceros;
        GRANT SELECT ON OBJECT::rep.vw_contactos_caso TO rol_carga_terceros;
    END;
    COMMIT TRANSACTION;
END TRY
BEGIN CATCH
    IF XACT_STATE()<>0 ROLLBACK TRANSACTION;
    THROW;
END CATCH;
