/* Azure SQL Database. Instalacion inicial en una base NUEVA para este proceso.
   No crea bases, recursos Azure, usuarios ni permisos. Ejecutar antes de 02..04.
   Las versiones de hechos son inmutables por contrato de carga. */
SET NOCOUNT ON;
SET XACT_ABORT ON;
SET ANSI_NULLS ON;
SET ANSI_PADDING ON;
SET ANSI_WARNINGS ON;
SET ARITHABORT ON;
SET CONCAT_NULL_YIELDS_NULL ON;
SET QUOTED_IDENTIFIER ON;
SET NUMERIC_ROUNDABORT OFF;
IF DB_NAME() IN (N'master', N'tempdb', N'model', N'msdb', N'sqldb-jnc-notif-dev')
    THROW 51000, N'Utilice una base nueva y exclusiva para este proceso.', 1;
IF EXISTS (SELECT 1 FROM sys.tables t JOIN sys.schemas s ON s.schema_id=t.schema_id
           WHERE s.name IN (N'cfg',N'ing',N'core',N'rep'))
    THROW 51001, N'Instalacion inicial: los esquemas de destino ya contienen tablas.', 1;
BEGIN TRY
    BEGIN TRANSACTION;
    IF SCHEMA_ID(N'cfg') IS NULL EXEC(N'CREATE SCHEMA cfg AUTHORIZATION dbo;');
    IF SCHEMA_ID(N'ing') IS NULL EXEC(N'CREATE SCHEMA ing AUTHORIZATION dbo;');
    IF SCHEMA_ID(N'core') IS NULL EXEC(N'CREATE SCHEMA core AUTHORIZATION dbo;');
    IF SCHEMA_ID(N'rep') IS NULL EXEC(N'CREATE SCHEMA rep AUTHORIZATION dbo;');

    CREATE TABLE cfg.calendario (
        id_calendario smallint NOT NULL CONSTRAINT PK_calendario PRIMARY KEY,
        nombre nvarchar(120) NOT NULL,
        version smallint NOT NULL,
        fecha_desde date NOT NULL,
        fecha_hasta date NOT NULL,
        descripcion nvarchar(1000) NOT NULL,
        publicado bit NOT NULL CONSTRAINT DF_calendario_publicado DEFAULT (0),
        CONSTRAINT UQ_calendario_nombre_version UNIQUE(nombre,version),
        CONSTRAINT CK_calendario_rango CHECK(fecha_desde<=fecha_hasta AND version>0)
    );
    CREATE TABLE cfg.dia_calendario (
        id_calendario smallint NOT NULL,
        fecha date NOT NULL,
        es_habil bit NOT NULL,
        acumulado_habil int NOT NULL,
        nombre_festivo nvarchar(120) NULL,
        CONSTRAINT PK_dia_calendario PRIMARY KEY(id_calendario,fecha),
        CONSTRAINT FK_dia_calendario FOREIGN KEY(id_calendario) REFERENCES cfg.calendario(id_calendario),
        CONSTRAINT CK_dia_acumulado CHECK(acumulado_habil>=0)
    );
    CREATE TABLE cfg.perfil_entrada (
        id_perfil smallint NOT NULL CONSTRAINT PK_perfil_entrada PRIMARY KEY,
        nombre varchar(50) NOT NULL,
        version smallint NOT NULL,
        version_parser varchar(32) NOT NULL,
        version_modelo varchar(32) NOT NULL,
        id_calendario smallint NOT NULL,
        fecha_minima date NOT NULL,
        fecha_maxima date NOT NULL,
        publicado bit NOT NULL CONSTRAINT DF_perfil_publicado DEFAULT (0),
        CONSTRAINT UQ_perfil_nombre_version UNIQUE(nombre,version),
        CONSTRAINT FK_perfil_calendario FOREIGN KEY(id_calendario) REFERENCES cfg.calendario(id_calendario),
        CONSTRAINT CK_perfil_rango CHECK(fecha_minima<=fecha_maxima AND version>0)
    );
    CREATE TABLE cfg.campo_entrada (
        id_perfil smallint NOT NULL,
        campo varchar(80) COLLATE Latin1_General_100_BIN2 NOT NULL,
        orden smallint NOT NULL,
        tipo_dato varchar(16) NOT NULL,
        obligatorio bit NOT NULL,
        longitud_maxima smallint NULL,
        tipo_parte varchar(20) NULL,
        bit_no_aplica tinyint NULL,
        CONSTRAINT PK_campo_entrada PRIMARY KEY(id_perfil,campo),
        CONSTRAINT UQ_campo_orden UNIQUE(id_perfil,orden),
        CONSTRAINT FK_campo_perfil FOREIGN KEY(id_perfil) REFERENCES cfg.perfil_entrada(id_perfil),
        CONSTRAINT CK_campo_tipo CHECK(tipo_dato IN ('TEXTO','FECHA','IDENTIFICADOR')),
        CONSTRAINT CK_campo_orden CHECK(orden BETWEEN 1 AND 50),
        CONSTRAINT CK_campo_bit CHECK(bit_no_aplica IS NULL OR bit_no_aplica IN (1,2,4,8,16,32,64)),
        CONSTRAINT CK_campo_longitud CHECK(longitud_maxima IS NULL OR longitud_maxima>0)
    );
    CREATE TABLE cfg.alias_columna (
        id_perfil smallint NOT NULL,
        alias_normalizado nvarchar(255) COLLATE Latin1_General_100_BIN2 NOT NULL,
        campo varchar(80) COLLATE Latin1_General_100_BIN2 NOT NULL,
        CONSTRAINT PK_alias_columna PRIMARY KEY(id_perfil,alias_normalizado),
        CONSTRAINT FK_alias_campo FOREIGN KEY(id_perfil,campo) REFERENCES cfg.campo_entrada(id_perfil,campo),
        CONSTRAINT CK_alias_no_vacio CHECK(LEN(LTRIM(RTRIM(alias_normalizado)))>0)
    );

    CREATE TABLE ing.archivo (
        id_archivo bigint IDENTITY(1,1) NOT NULL CONSTRAINT PK_archivo PRIMARY KEY,
        drive_id nvarchar(128) COLLATE Latin1_General_100_BIN2 NOT NULL,
        item_id nvarchar(128) COLLATE Latin1_General_100_BIN2 NOT NULL,
        sitio_id nvarchar(256) NULL,
        nombre_actual nvarchar(255) NOT NULL,
        ruta_actual nvarchar(2048) NOT NULL,
        version_origen_actual nvarchar(128) NULL,
        fecha_modificacion_origen datetime2(7) NULL,
        id_carga_vigente bigint NULL,
        fecha_creacion_utc datetime2(3) NOT NULL CONSTRAINT DF_archivo_creacion DEFAULT SYSUTCDATETIME(),
        CONSTRAINT UQ_archivo_sharepoint UNIQUE(drive_id,item_id),
        CONSTRAINT CK_archivo_ids CHECK(LEN(drive_id)>0 AND LEN(item_id)>0)
    );
    CREATE TABLE ing.carga (
        id_carga bigint IDENTITY(1,1) NOT NULL CONSTRAINT PK_carga PRIMARY KEY,
        id_archivo bigint NOT NULL,
        id_perfil smallint NOT NULL,
        hash_contenido binary(32) NOT NULL,
        tamano_bytes bigint NOT NULL,
        version_origen_inicial nvarchar(128) NOT NULL,
        nombre_origen_inicial nvarchar(255) NOT NULL,
        ruta_origen_inicial nvarchar(2048) NOT NULL,
        estado varchar(15) NOT NULL CONSTRAINT DF_carga_estado DEFAULT ('RECIBIDA'),
        filas_esperadas int NULL,
        fecha_recepcion_utc datetime2(3) NOT NULL CONSTRAINT DF_carga_recepcion DEFAULT SYSUTCDATETIME(),
        fecha_validacion_utc datetime2(3) NULL,
        fecha_publicacion_utc datetime2(3) NULL,
        estructura_origen_json nvarchar(max) NULL,
        CONSTRAINT FK_carga_archivo FOREIGN KEY(id_archivo) REFERENCES ing.archivo(id_archivo),
        CONSTRAINT FK_carga_perfil FOREIGN KEY(id_perfil) REFERENCES cfg.perfil_entrada(id_perfil),
        CONSTRAINT UQ_carga_contenido UNIQUE(id_archivo,hash_contenido,id_perfil),
        CONSTRAINT UQ_carga_archivo_id UNIQUE(id_archivo,id_carga),
        CONSTRAINT UQ_carga_perfil_id UNIQUE(id_carga,id_perfil),
        CONSTRAINT CK_carga_tamano CHECK(tamano_bytes>0),
        CONSTRAINT CK_carga_filas CHECK(filas_esperadas IS NULL OR filas_esperadas>=0),
        CONSTRAINT CK_carga_estado CHECK(estado IN ('RECIBIDA','PROCESANDO','VALIDADA','PUBLICADA','ERROR')),
        CONSTRAINT CK_carga_estructura_json CHECK(estructura_origen_json IS NULL OR ISJSON(estructura_origen_json)=1)
    );
    ALTER TABLE ing.archivo ADD CONSTRAINT FK_archivo_carga_vigente
        FOREIGN KEY(id_archivo,id_carga_vigente) REFERENCES ing.carga(id_archivo,id_carga);

    CREATE TABLE core.entidad (
        id_entidad int IDENTITY(1,1) NOT NULL CONSTRAINT PK_entidad PRIMARY KEY,
        nombre nvarchar(500) COLLATE Latin1_General_100_BIN2 NOT NULL,
        hash_nombre AS CONVERT(binary(32),HASHBYTES('SHA2_256',CONVERT(varbinary(1000),nombre))) PERSISTED,
        CONSTRAINT UQ_entidad_hash UNIQUE(hash_nombre),
        CONSTRAINT CK_entidad_nombre CHECK(LEN(LTRIM(RTRIM(nombre)))>0)
    );
    CREATE TABLE core.caso (
        id_caso bigint IDENTITY(1,1) NOT NULL CONSTRAINT PK_caso PRIMARY KEY,
        sala tinyint NOT NULL,
        fecha_audiencia date NOT NULL,
        reparto_paciente nvarchar(100) COLLATE Latin1_General_100_BIN2 NOT NULL,
        identificacion_paciente nvarchar(50) COLLATE Latin1_General_100_BIN2 NOT NULL,
        fecha_creacion_utc datetime2(3) NOT NULL CONSTRAINT DF_caso_creacion DEFAULT SYSUTCDATETIME(),
        CONSTRAINT UQ_caso_negocio UNIQUE(sala,fecha_audiencia,reparto_paciente,identificacion_paciente),
        CONSTRAINT CK_caso_sala CHECK(sala BETWEEN 1 AND 99),
        CONSTRAINT CK_caso_identificadores CHECK(
            LEN(LTRIM(RTRIM(reparto_paciente)))>0 AND LEN(LTRIM(RTRIM(identificacion_paciente)))>0
            AND CHARINDEX('|',reparto_paciente)=0 AND CHARINDEX('|',identificacion_paciente)=0
            AND DATALENGTH(reparto_paciente)=DATALENGTH(LTRIM(RTRIM(reparto_paciente)))
            AND DATALENGTH(identificacion_paciente)=DATALENGTH(LTRIM(RTRIM(identificacion_paciente))))
    );
    CREATE TABLE core.caso_dato (
        id_caso_dato bigint IDENTITY(1,1) NOT NULL CONSTRAINT PK_caso_dato PRIMARY KEY,
        id_caso bigint NOT NULL,
        tipo_de_dictamen nvarchar(100) NULL,
        nombre_paciente nvarchar(500) NULL,
        medico_ponente nvarchar(500) NULL,
        fecha_fijacion date NULL,
        fecha_desfijacion date NULL,
        hash_datos binary(32) NOT NULL,
        CONSTRAINT FK_caso_dato_caso FOREIGN KEY(id_caso) REFERENCES core.caso(id_caso),
        CONSTRAINT UQ_caso_dato_hash UNIQUE(id_caso,hash_datos),
        CONSTRAINT UQ_caso_dato_padre UNIQUE(id_caso,id_caso_dato)
    );
    CREATE TABLE core.caso_parte (
        id_caso_parte bigint IDENTITY(1,1) NOT NULL CONSTRAINT PK_caso_parte PRIMARY KEY,
        id_caso bigint NOT NULL,
        tipo_parte varchar(20) NOT NULL,
        id_entidad int NULL,
        referencia_anonima varchar(100) COLLATE Latin1_General_100_BIN2 NOT NULL CONSTRAINT DF_parte_referencia DEFAULT (''),
        CONSTRAINT FK_parte_caso FOREIGN KEY(id_caso) REFERENCES core.caso(id_caso),
        CONSTRAINT FK_parte_entidad FOREIGN KEY(id_entidad) REFERENCES core.entidad(id_entidad),
        CONSTRAINT UQ_parte_identidad UNIQUE(id_caso,tipo_parte,id_entidad,referencia_anonima),
        CONSTRAINT UQ_parte_padre_tipo UNIQUE(id_caso,id_caso_parte,tipo_parte),
        CONSTRAINT CK_parte_tipo CHECK(tipo_parte IN ('paciente','arl','afp','eps','aseguradora','ultimoempleador')),
        CONSTRAINT CK_parte_identidad CHECK(
            (tipo_parte='paciente' AND id_entidad IS NULL AND referencia_anonima='') OR
            (tipo_parte<>'paciente' AND ((id_entidad IS NOT NULL AND referencia_anonima='') OR
              (id_entidad IS NULL AND LEN(LTRIM(RTRIM(referencia_anonima)))>0))))
    );
    CREATE TABLE core.notificacion_declarada (
        id_notificacion bigint IDENTITY(1,1) NOT NULL CONSTRAINT PK_notificacion_declarada PRIMARY KEY,
        id_caso_parte bigint NOT NULL,
        documento_notificacion nvarchar(100) NULL,
        estado_reportado nvarchar(100) NULL,
        fecha_notificacion_reportada date NULL,
        fecha_envio_reportada date NULL,
        guia_reportada nvarchar(150) NULL,
        fecha_recibido_reportada date NULL,
        hash_datos binary(32) NOT NULL,
        CONSTRAINT FK_notificacion_parte FOREIGN KEY(id_caso_parte) REFERENCES core.caso_parte(id_caso_parte),
        CONSTRAINT UQ_notificacion_datos UNIQUE(id_caso_parte,hash_datos),
        CONSTRAINT UQ_notificacion_padre UNIQUE(id_caso_parte,id_notificacion),
        CONSTRAINT CK_notificacion_no_vacia CHECK(documento_notificacion IS NOT NULL OR estado_reportado IS NOT NULL
            OR fecha_notificacion_reportada IS NOT NULL OR fecha_envio_reportada IS NOT NULL
            OR guia_reportada IS NOT NULL OR fecha_recibido_reportada IS NOT NULL)
    );

    CREATE TABLE ing.fila_origen (
        id_fila bigint IDENTITY(1,1) NOT NULL CONSTRAINT PK_fila_origen PRIMARY KEY,
        id_carga bigint NOT NULL,
        id_perfil smallint NOT NULL,
        hoja nvarchar(128) COLLATE Latin1_General_100_BIN2 NOT NULL,
        numero_fila int NOT NULL,
        id_caso bigint NOT NULL,
        id_caso_dato bigint NOT NULL,
        id_entidad_remitente int NULL,
        CONSTRAINT UQ_fila_posicion UNIQUE(id_carga,hoja,numero_fila),
        CONSTRAINT UQ_fila_caso UNIQUE(id_fila,id_caso),
        CONSTRAINT UQ_fila_carga UNIQUE(id_carga,id_fila),
        CONSTRAINT UQ_fila_perfil UNIQUE(id_fila,id_perfil),
        CONSTRAINT FK_fila_carga_perfil FOREIGN KEY(id_carga,id_perfil) REFERENCES ing.carga(id_carga,id_perfil),
        CONSTRAINT FK_fila_caso_dato FOREIGN KEY(id_caso,id_caso_dato) REFERENCES core.caso_dato(id_caso,id_caso_dato),
        CONSTRAINT FK_fila_remitente FOREIGN KEY(id_entidad_remitente) REFERENCES core.entidad(id_entidad),
        CONSTRAINT CK_fila_numero CHECK(numero_fila>=2),
        CONSTRAINT CK_fila_hoja CHECK(LEN(hoja)>0)
    );
    CREATE TABLE ing.parte_fila (
        id_fila bigint NOT NULL,
        tipo_parte varchar(20) NOT NULL,
        id_caso bigint NOT NULL,
        id_caso_parte bigint NULL,
        id_notificacion bigint NULL,
        mascara_no_aplica tinyint NOT NULL CONSTRAINT DF_parte_fila_mascara DEFAULT (0),
        CONSTRAINT PK_parte_fila PRIMARY KEY(id_fila,tipo_parte),
        CONSTRAINT FK_parte_fila_caso FOREIGN KEY(id_fila,id_caso) REFERENCES ing.fila_origen(id_fila,id_caso),
        CONSTRAINT FK_parte_fila_parte FOREIGN KEY(id_caso,id_caso_parte,tipo_parte)
            REFERENCES core.caso_parte(id_caso,id_caso_parte,tipo_parte),
        CONSTRAINT FK_parte_fila_notificacion FOREIGN KEY(id_caso_parte,id_notificacion)
            REFERENCES core.notificacion_declarada(id_caso_parte,id_notificacion),
        CONSTRAINT CK_parte_fila_tipo CHECK(tipo_parte IN ('paciente','arl','afp','eps','aseguradora','ultimoempleador')),
        CONSTRAINT CK_parte_fila_mascara CHECK(mascara_no_aplica<=127),
        CONSTRAINT CK_parte_fila_notificacion CHECK(id_notificacion IS NULL OR id_caso_parte IS NOT NULL),
        CONSTRAINT CK_parte_fila_contenido CHECK(id_caso_parte IS NOT NULL OR mascara_no_aplica>0)
    );
    CREATE TABLE ing.valor_original (
        id_fila bigint NOT NULL,
        id_perfil smallint NOT NULL,
        campo varchar(80) COLLATE Latin1_General_100_BIN2 NOT NULL,
        valor_original nvarchar(2000) NOT NULL,
        motivo varchar(30) NOT NULL,
        parser_aplicado varchar(40) NULL,
        mostrar_en_consolidado bit NOT NULL,
        CONSTRAINT PK_valor_original PRIMARY KEY(id_fila,campo),
        CONSTRAINT FK_valor_fila_perfil FOREIGN KEY(id_fila,id_perfil) REFERENCES ing.fila_origen(id_fila,id_perfil),
        CONSTRAINT FK_valor_campo FOREIGN KEY(id_perfil,campo) REFERENCES cfg.campo_entrada(id_perfil,campo),
        CONSTRAINT CK_valor_motivo CHECK(motivo IN ('FECHA_INVALIDA','FECHA_AMBIGUA','FECHA_FUERA_RANGO',
            'FECHA_RECUPERADA','VARIANTE_NO_APLICA','NORMALIZACION','OTRO'))
    );
    CREATE TABLE ing.incidencia (
        id_incidencia bigint IDENTITY(1,1) NOT NULL CONSTRAINT PK_incidencia PRIMARY KEY,
        id_carga bigint NOT NULL,
        id_fila bigint NULL,
        codigo varchar(60) NOT NULL,
        severidad char(1) NOT NULL,
        columna_origen nvarchar(255) NULL,
        detalle nvarchar(1000) NOT NULL,
        resuelta bit NOT NULL CONSTRAINT DF_incidencia_resuelta DEFAULT (0),
        fecha_utc datetime2(3) NOT NULL CONSTRAINT DF_incidencia_fecha DEFAULT SYSUTCDATETIME(),
        CONSTRAINT FK_incidencia_carga FOREIGN KEY(id_carga) REFERENCES ing.carga(id_carga),
        CONSTRAINT FK_incidencia_fila FOREIGN KEY(id_carga,id_fila) REFERENCES ing.fila_origen(id_carga,id_fila),
        CONSTRAINT CK_incidencia_severidad CHECK(severidad IN ('I','W','E'))
    );
    CREATE INDEX IX_fila_caso ON ing.fila_origen(id_caso) INCLUDE(id_carga,id_caso_dato);
    CREATE INDEX IX_parte_fila_notificacion ON ing.parte_fila(id_notificacion) WHERE id_notificacion IS NOT NULL;
    CREATE INDEX IX_incidencia_carga ON ing.incidencia(id_carga,severidad,resuelta);
    COMMIT TRANSACTION;
END TRY
BEGIN CATCH
    IF XACT_STATE()<>0 ROLLBACK TRANSACTION;
    THROW;
END CATCH;
