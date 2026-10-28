/* Azure SQL: datos previos al cruce PST / Sabana.
   Instalacion inicial. Ejecutar UNA vez en sqldb-jrb-sabana-dev.
   No modifica las tablas Excel ni carga archivos; no concede permisos.
   PST, cuerpos, cabeceras completas y binarios adjuntos quedan fuera de SQL.
   Fechas datetime2 expresadas en UTC. Hashes: bytes SHA-256, no texto hexadecimal.
   Los codigos tinyint se documentan junto a su columna.
*/
SET NOCOUNT ON;
SET XACT_ABORT ON;
SET ANSI_NULLS ON;
SET QUOTED_IDENTIFIER ON;
SET ANSI_PADDING ON;
SET ANSI_WARNINGS ON;
SET ARITHABORT ON;
SET CONCAT_NULL_YIELDS_NULL ON;
SET NUMERIC_ROUNDABORT OFF;

IF DB_NAME() <> N'sqldb-jrb-sabana-dev'
    THROW 51600, N'Ejecute en sqldb-jrb-sabana-dev.', 1;
IF EXISTS (
    SELECT 1 FROM sys.objects o JOIN sys.schemas s ON s.schema_id=o.schema_id
    WHERE (s.name=N'ing' AND o.name IN
        (N'pst_contenido',N'pst_archivo',N'pst_carga',N'pst_incidencia'))
       OR (s.name=N'mail' AND o.name IN
        (N'pst_carpeta',N'direccion',N'mensaje',N'mensaje_origen',N'destinatario',
         N'adjunto',N'referencia_mensaje',N'reporte_destinatario'))
)
    THROW 51601, N'Hay objetos PST existentes. No reejecute la instalacion inicial; use una migracion.', 1;

BEGIN TRY
    BEGIN TRANSACTION;
    IF SCHEMA_ID(N'ing') IS NULL EXEC(N'CREATE SCHEMA ing AUTHORIZATION dbo;');
    IF SCHEMA_ID(N'mail') IS NULL EXEC(N'CREATE SCHEMA mail AUTHORIZATION dbo;');

    -- Un mismo contenido puede tener varios archivos de procedencia.
    CREATE TABLE ing.pst_contenido (
        id_contenido int IDENTITY(1,1) NOT NULL CONSTRAINT PK_pst_contenido PRIMARY KEY,
        sha256 binary(32) NOT NULL,
        tamano_bytes bigint NOT NULL,
        id_carga_vigente int NULL,
        registrado_utc datetime2(7) NOT NULL CONSTRAINT DF_pst_contenido_fecha DEFAULT SYSUTCDATETIME(),
        CONSTRAINT UQ_pst_contenido_sha UNIQUE(sha256),
        CONSTRAINT CK_pst_contenido_tamano CHECK(tamano_bytes>0)
    );
    -- Una fila por archivo/version de SharePoint; referencias estables, sin SAS.
    CREATE TABLE ing.pst_archivo (
        id_pst int IDENTITY(1,1) NOT NULL CONSTRAINT PK_pst_archivo PRIMARY KEY,
        id_contenido int NOT NULL,
        drive_id nvarchar(128) COLLATE Latin1_General_100_BIN2 NOT NULL,
        item_id nvarchar(128) COLLATE Latin1_General_100_BIN2 NOT NULL,
        version_origen nvarchar(128) COLLATE Latin1_General_100_BIN2 NOT NULL,
        nombre nvarchar(255) NOT NULL,
        ruta nvarchar(2048) NOT NULL,
        modificado_origen_utc datetime2(7) NULL,
        registrado_utc datetime2(7) NOT NULL CONSTRAINT DF_pst_archivo_fecha DEFAULT SYSUTCDATETIME(),
        CONSTRAINT FK_pst_archivo_contenido FOREIGN KEY(id_contenido) REFERENCES ing.pst_contenido(id_contenido),
        CONSTRAINT UQ_pst_archivo_version UNIQUE(drive_id,item_id,version_origen),
        CONSTRAINT CK_pst_archivo_referencia CHECK(LEN(drive_id)>0 AND LEN(item_id)>0 AND LEN(version_origen)>0 AND LEN(nombre)>0 AND LEN(ruta)>0)
    );
    -- Carga logica reanudable: un reintento no crea otra copia de las filas.
    CREATE TABLE ing.pst_carga (
        id_carga int IDENTITY(1,1) NOT NULL CONSTRAINT PK_pst_carga PRIMARY KEY,
        id_contenido int NOT NULL,
        version_extractor varchar(64) NOT NULL,
        version_modelo varchar(32) NOT NULL,
        estado varchar(12) NOT NULL CONSTRAINT DF_pst_carga_estado DEFAULT 'RECIBIDA',
        intentos int NOT NULL CONSTRAINT DF_pst_carga_intentos DEFAULT 0,
        inicio_utc datetime2(7) NULL,
        fin_utc datetime2(7) NULL,
        elementos_reportados bigint NULL,
        elementos_examinados bigint NOT NULL CONSTRAINT DF_pst_carga_examinados DEFAULT 0,
        mensajes_extraidos bigint NOT NULL CONSTRAINT DF_pst_carga_extraidos DEFAULT 0,
        elementos_omitidos bigint NOT NULL CONSTRAINT DF_pst_carga_omitidos DEFAULT 0,
        elementos_fallidos bigint NOT NULL CONSTRAINT DF_pst_carga_fallidos DEFAULT 0,
        CONSTRAINT FK_pst_carga_contenido FOREIGN KEY(id_contenido) REFERENCES ing.pst_contenido(id_contenido),
        CONSTRAINT UQ_pst_carga_version UNIQUE(id_contenido,version_extractor,version_modelo),
        CONSTRAINT UQ_pst_carga_padre UNIQUE(id_contenido,id_carga),
        CONSTRAINT CK_pst_carga_estado CHECK(estado IN ('RECIBIDA','PROCESANDO','COMPLETA','CON_ERRORES','ERROR')),
        CONSTRAINT CK_pst_carga_version CHECK(LEN(version_extractor)>0 AND LEN(version_modelo)>0),
        CONSTRAINT CK_pst_carga_conteos CHECK(intentos>=0 AND (elementos_reportados IS NULL OR elementos_reportados>=0)
            AND elementos_examinados>=0 AND mensajes_extraidos>=0 AND elementos_omitidos>=0 AND elementos_fallidos>=0),
        CONSTRAINT CK_pst_carga_completa CHECK(estado<>'COMPLETA' OR
            (inicio_utc IS NOT NULL AND fin_utc IS NOT NULL AND elementos_fallidos=0
             AND elementos_examinados=mensajes_extraidos+elementos_omitidos)),
        CONSTRAINT CK_pst_carga_fechas CHECK(fin_utc IS NULL OR (inicio_utc IS NOT NULL AND fin_utc>=inicio_utc))
    );
    ALTER TABLE ing.pst_contenido ADD CONSTRAINT FK_pst_contenido_carga
        FOREIGN KEY(id_contenido,id_carga_vigente) REFERENCES ing.pst_carga(id_contenido,id_carga);

    CREATE TABLE mail.pst_carpeta (
        id_carpeta int IDENTITY(1,1) NOT NULL CONSTRAINT PK_mail_pst_carpeta PRIMARY KEY,
        id_carga int NOT NULL,
        identificador_pst bigint NOT NULL,
        id_carpeta_padre int NULL,
        nombre nvarchar(512) NOT NULL,
        elementos_reportados bigint NULL,
        CONSTRAINT FK_mail_carpeta_carga FOREIGN KEY(id_carga) REFERENCES ing.pst_carga(id_carga),
        CONSTRAINT UQ_mail_carpeta_local UNIQUE(id_carga,identificador_pst),
        CONSTRAINT UQ_mail_carpeta_carga UNIQUE(id_carga,id_carpeta),
        CONSTRAINT FK_mail_carpeta_padre FOREIGN KEY(id_carga,id_carpeta_padre) REFERENCES mail.pst_carpeta(id_carga,id_carpeta),
        CONSTRAINT CK_mail_carpeta_valores CHECK(identificador_pst>=0 AND (elementos_reportados IS NULL OR elementos_reportados>=0)),
        CONSTRAINT CK_mail_carpeta_no_autopadre CHECK(id_carpeta_padre IS NULL OR id_carpeta_padre<>id_carpeta)
    );
    -- La direccion se conserva exacta; tipo EX no equivale automaticamente a SMTP.
    CREATE TABLE mail.direccion (
        id_direccion int IDENTITY(1,1) NOT NULL CONSTRAINT PK_mail_direccion PRIMARY KEY,
        tipo varchar(16) COLLATE Latin1_General_100_BIN2 NOT NULL,
        direccion nvarchar(1024) COLLATE Latin1_General_100_BIN2 NOT NULL,
        hash_direccion AS CONVERT(binary(32),HASHBYTES('SHA2_256',CONVERT(varbinary(2048),direccion))),
        CONSTRAINT CK_mail_direccion_valor CHECK(LEN(tipo)>0 AND LEN(direccion)>0)
    );
    CREATE UNIQUE INDEX UX_mail_direccion ON mail.direccion(tipo,hash_direccion);

    CREATE TABLE mail.mensaje (
        id_mensaje int IDENTITY(1,1) NOT NULL CONSTRAINT PK_mail_mensaje PRIMARY KEY,
        -- Nula hasta disponer de una politica de deduplicacion verificable.
        huella_deduplicacion binary(32) NULL,
        version_huella smallint NULL,
        message_id nvarchar(2048) COLLATE Latin1_General_100_BIN2 NULL,
        hash_message_id AS CONVERT(binary(32),HASHBYTES('SHA2_256',CONVERT(varbinary(4096),message_id))),
        clase_mapi varchar(255) NULL,
        asunto nvarchar(2048) NULL,
        id_remitente int NULL,
        id_representado int NULL,
        nombre_remitente nvarchar(512) NULL,
        envio_utc datetime2(7) NULL,
        hash_cuerpo binary(32) NULL,
        ubicacion_detalle nvarchar(2048) NULL,
        -- No contiene texto de cuerpo, HTML, MIME completo ni adjuntos binarios.
        CONSTRAINT FK_mail_mensaje_remitente FOREIGN KEY(id_remitente) REFERENCES mail.direccion(id_direccion),
        CONSTRAINT FK_mail_mensaje_representado FOREIGN KEY(id_representado) REFERENCES mail.direccion(id_direccion),
        CONSTRAINT CK_mail_mensaje_huella CHECK((huella_deduplicacion IS NULL AND version_huella IS NULL)
            OR (huella_deduplicacion IS NOT NULL AND version_huella IS NOT NULL AND version_huella>0))
    );
    CREATE UNIQUE INDEX UX_mail_mensaje_huella ON mail.mensaje(version_huella,huella_deduplicacion)
        WHERE huella_deduplicacion IS NOT NULL;
    -- Message-ID no es una clave unica global.
    CREATE INDEX IX_mail_mensaje_message_id ON mail.mensaje(hash_message_id) WHERE message_id IS NOT NULL;
    CREATE INDEX IX_mail_mensaje_envio ON mail.mensaje(envio_utc);

    CREATE TABLE mail.mensaje_origen (
        id_origen int IDENTITY(1,1) NOT NULL CONSTRAINT PK_mail_mensaje_origen PRIMARY KEY,
        id_carga int NOT NULL,
        id_carpeta int NOT NULL,
        identificador_pst bigint NOT NULL,
        id_mensaje int NOT NULL,
        indice_en_carpeta int NULL,
        recepcion_buzon_utc datetime2(7) NULL,
        flags_mapi int NULL,
        tamano_mensaje_bytes bigint NULL,
        destinatarios_reportados int NULL,
        adjuntos_reportados int NULL,
        destinatarios_completos bit NOT NULL CONSTRAINT DF_mail_origen_destinatarios DEFAULT 0,
        adjuntos_completos bit NOT NULL CONSTRAINT DF_mail_origen_adjuntos DEFAULT 0,
        extraido_utc datetime2(7) NOT NULL CONSTRAINT DF_mail_origen_fecha DEFAULT SYSUTCDATETIME(),
        CONSTRAINT FK_mail_origen_carpeta FOREIGN KEY(id_carga,id_carpeta) REFERENCES mail.pst_carpeta(id_carga,id_carpeta),
        CONSTRAINT FK_mail_origen_mensaje FOREIGN KEY(id_mensaje) REFERENCES mail.mensaje(id_mensaje),
        CONSTRAINT UQ_mail_origen_local UNIQUE(id_carga,identificador_pst),
        CONSTRAINT UQ_mail_origen_carga UNIQUE(id_carga,id_origen),
        CONSTRAINT CK_mail_origen_valores CHECK(identificador_pst>=0 AND (indice_en_carpeta IS NULL OR indice_en_carpeta>=0)
            AND (tamano_mensaje_bytes IS NULL OR tamano_mensaje_bytes>=0)
            AND (destinatarios_reportados IS NULL OR destinatarios_reportados>=0)
            AND (adjuntos_reportados IS NULL OR adjuntos_reportados>=0))
    );
    CREATE INDEX IX_mail_origen_mensaje ON mail.mensaje_origen(id_mensaje);

    CREATE TABLE mail.destinatario (
        id_destinatario int IDENTITY(1,1) NOT NULL CONSTRAINT PK_mail_destinatario PRIMARY KEY,
        id_mensaje int NOT NULL,
        tipo tinyint NOT NULL, -- 1=Para, 2=CC, 3=CCO, 4=Reply-To.
        fuente tinyint NOT NULL, -- 1=tabla MAPI, 2=cabecera de transporte, 3=solo nombre mostrado.
        ordinal int NOT NULL,
        id_direccion int NULL,
        id_smtp_resuelto int NULL,
        nombre_mostrado nvarchar(512) NULL,
        CONSTRAINT FK_mail_destinatario_mensaje FOREIGN KEY(id_mensaje) REFERENCES mail.mensaje(id_mensaje),
        CONSTRAINT FK_mail_destinatario_direccion FOREIGN KEY(id_direccion) REFERENCES mail.direccion(id_direccion),
        CONSTRAINT FK_mail_destinatario_smtp FOREIGN KEY(id_smtp_resuelto) REFERENCES mail.direccion(id_direccion),
        CONSTRAINT UQ_mail_destinatario_posicion UNIQUE(id_mensaje,fuente,tipo,ordinal),
        CONSTRAINT CK_mail_destinatario_tipo CHECK(tipo BETWEEN 1 AND 4 AND fuente BETWEEN 1 AND 3 AND ordinal>=0),
        CONSTRAINT CK_mail_destinatario_valor CHECK(id_direccion IS NOT NULL OR id_smtp_resuelto IS NOT NULL OR COALESCE(LEN(nombre_mostrado),0)>0)
    );
    CREATE INDEX IX_mail_destinatario_direccion ON mail.destinatario(id_direccion,id_mensaje) WHERE id_direccion IS NOT NULL;
    CREATE INDEX IX_mail_destinatario_smtp ON mail.destinatario(id_smtp_resuelto,id_mensaje) WHERE id_smtp_resuelto IS NOT NULL;

    CREATE TABLE mail.adjunto (
        id_adjunto int IDENTITY(1,1) NOT NULL CONSTRAINT PK_mail_adjunto PRIMARY KEY,
        id_mensaje int NOT NULL,
        ordinal int NOT NULL,
        nombre nvarchar(1024) NULL,
        tamano_bytes bigint NULL,
        tipo_mime varchar(255) NULL,
        metodo_mapi int NULL,
        content_id nvarchar(1024) NULL,
        oculto bit NULL,
        sha256 binary(32) NULL, -- Solo cuando se haya leido el binario completo.
        ubicacion_objeto nvarchar(2048) NULL, -- Solo si se extrae a almacenamiento externo.
        CONSTRAINT FK_mail_adjunto_mensaje FOREIGN KEY(id_mensaje) REFERENCES mail.mensaje(id_mensaje),
        CONSTRAINT UQ_mail_adjunto_posicion UNIQUE(id_mensaje,ordinal),
        CONSTRAINT CK_mail_adjunto_valores CHECK(ordinal>=0 AND (tamano_bytes IS NULL OR tamano_bytes>=0))
    );

    -- Referencias OBSERVADAS; todavia no enlazan con otro mensaje ni con un caso.
    CREATE TABLE mail.referencia_mensaje (
        id_mensaje int NOT NULL,
        tipo tinyint NOT NULL, -- 1=In-Reply-To, 2=Original-Message-ID, 3=References, 4=ReportTag.
        ordinal int NOT NULL,
        valor_texto nvarchar(2048) COLLATE Latin1_General_100_BIN2 NULL,
        valor_binario varbinary(4096) NULL,
        hash_valor AS CONVERT(binary(32),HASHBYTES('SHA2_256',
            COALESCE(CONVERT(varbinary(4096),valor_texto),valor_binario))),
        CONSTRAINT PK_mail_referencia PRIMARY KEY(id_mensaje,tipo,ordinal),
        CONSTRAINT FK_mail_referencia_mensaje FOREIGN KEY(id_mensaje) REFERENCES mail.mensaje(id_mensaje),
        CONSTRAINT CK_mail_referencia_tipo CHECK(tipo BETWEEN 1 AND 4 AND ordinal>=0),
        CONSTRAINT CK_mail_referencia_valor CHECK(
            (tipo BETWEEN 1 AND 3 AND valor_texto IS NOT NULL AND LEN(valor_texto)>0 AND valor_binario IS NULL)
            OR (tipo=4 AND valor_texto IS NULL AND valor_binario IS NOT NULL AND DATALENGTH(valor_binario)>0))
    );
    CREATE INDEX IX_mail_referencia_busqueda ON mail.referencia_mensaje(tipo,hash_valor);

    -- Campos estructurados DSN/MDN, cuando existan. Son observaciones, no conclusiones.
    CREATE TABLE mail.reporte_destinatario (
        id_reporte int IDENTITY(1,1) NOT NULL CONSTRAINT PK_mail_reporte PRIMARY KEY,
        id_mensaje int NOT NULL,
        ordinal int NOT NULL,
        tipo tinyint NOT NULL, -- 1=DSN, 2=MDN, 3=reporte MAPI.
        destinatario_original nvarchar(1024) NULL,
        destinatario_final nvarchar(1024) NULL,
        accion varchar(64) NULL,
        estado varchar(64) NULL,
        disposicion nvarchar(512) NULL,
        diagnostico nvarchar(2048) NULL,
        fecha_reporte_utc datetime2(7) NULL,
        CONSTRAINT FK_mail_reporte_mensaje FOREIGN KEY(id_mensaje) REFERENCES mail.mensaje(id_mensaje),
        CONSTRAINT UQ_mail_reporte_posicion UNIQUE(id_mensaje,tipo,ordinal),
        CONSTRAINT CK_mail_reporte_tipo CHECK(tipo BETWEEN 1 AND 3 AND ordinal>=0)
    );

    CREATE TABLE ing.pst_incidencia (
        id_incidencia int IDENTITY(1,1) NOT NULL CONSTRAINT PK_pst_incidencia PRIMARY KEY,
        id_carga int NOT NULL,
        id_origen int NULL,
        identificador_pst bigint NULL, -- Permite registrar elementos que no se pudieron extraer.
        codigo varchar(64) NOT NULL,
        severidad char(1) NOT NULL,
        detalle nvarchar(1000) NOT NULL,
        resuelta bit NOT NULL CONSTRAINT DF_pst_incidencia_resuelta DEFAULT 0,
        registrada_utc datetime2(7) NOT NULL CONSTRAINT DF_pst_incidencia_fecha DEFAULT SYSUTCDATETIME(),
        CONSTRAINT FK_pst_incidencia_carga FOREIGN KEY(id_carga) REFERENCES ing.pst_carga(id_carga),
        CONSTRAINT FK_pst_incidencia_origen FOREIGN KEY(id_carga,id_origen) REFERENCES mail.mensaje_origen(id_carga,id_origen),
        CONSTRAINT CK_pst_incidencia_severidad CHECK(severidad IN ('I','W','E')),
        CONSTRAINT CK_pst_incidencia_codigo CHECK(LEN(codigo)>0 AND (identificador_pst IS NULL OR identificador_pst>=0))
    );
    CREATE INDEX IX_pst_incidencia_carga ON ing.pst_incidencia(id_carga,severidad,resuelta);
    CREATE INDEX IX_pst_archivo_contenido ON ing.pst_archivo(id_contenido);

    COMMIT TRANSACTION;
    SELECT N'OK: tablas PST creadas. No se han cargado archivos ni realizado cruces.' AS resultado;
END TRY
BEGIN CATCH
    IF XACT_STATE()<>0 ROLLBACK TRANSACTION;
    THROW;
END CATCH;
