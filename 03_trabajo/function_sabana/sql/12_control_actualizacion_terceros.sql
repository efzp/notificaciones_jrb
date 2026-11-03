/* Cola de control del maestro de terceros. Ejecutar en sqldb-jrb-sabana-dev.
   Una fila por revision publicada de una carga de origen.
   No ejecuta cargas ni modifica el maestro vigente. */
SET NOCOUNT ON;
SET XACT_ABORT ON;

IF DB_NAME() <> N'sqldb-jrb-sabana-dev'
    THROW 51840, N'Base incorrecta: use sqldb-jrb-sabana-dev.', 1;

IF OBJECT_ID(N'ing.terceros_carga', N'U') IS NULL
    THROW 51841, N'Falta el modelo del maestro de terceros.', 1;

BEGIN TRY
    BEGIN TRANSACTION;

    DECLARE @bloqueo int;
    EXEC @bloqueo = sys.sp_getapplock
        @Resource = N'terceros:migracion',
        @LockMode = 'Exclusive',
        @LockOwner = 'Transaction',
        @LockTimeout = 10000;

    IF @bloqueo < 0
        THROW 51842, N'Otra migracion esta en ejecucion.', 1;

    IF OBJECT_ID(N'ing.terceros_actualizacion', N'U') IS NULL
    BEGIN
        CREATE TABLE ing.terceros_actualizacion (
            id_actualizacion bigint IDENTITY(1,1) NOT NULL,
            origen varchar(3) COLLATE Latin1_General_100_BIN2 NOT NULL,
            tipo_fuente varchar(12) COLLATE Latin1_General_100_BIN2 NOT NULL,
            id_carga_origen nvarchar(128) COLLATE Latin1_General_100_BIN2 NOT NULL,
            hash_revision binary(32) NOT NULL,

            estado varchar(12) NOT NULL
                CONSTRAINT DF_terceros_actualizacion_estado DEFAULT ('PENDIENTE'),
            intentos smallint NOT NULL
                CONSTRAINT DF_terceros_actualizacion_intentos DEFAULT (0),
            max_intentos smallint NOT NULL
                CONSTRAINT DF_terceros_actualizacion_max_intentos DEFAULT (5),

            creado_utc datetime2(3) NOT NULL
                CONSTRAINT DF_terceros_actualizacion_creado DEFAULT (SYSUTCDATETIME()),
            actualizado_utc datetime2(3) NOT NULL
                CONSTRAINT DF_terceros_actualizacion_actualizado DEFAULT (SYSUTCDATETIME()),
            disponible_utc datetime2(3) NOT NULL
                CONSTRAINT DF_terceros_actualizacion_disponible
                DEFAULT (DATEADD(MINUTE, 10, SYSUTCDATETIME())),

            id_ejecucion uniqueidentifier NULL,
            inicio_intento_utc datetime2(3) NULL,
            reserva_hasta_utc datetime2(3) NULL,
            finalizado_utc datetime2(3) NULL,
            id_carga_terceros int NULL,
            codigo_ultimo_error varchar(64) NULL,
            version_fila rowversion NOT NULL,

            CONSTRAINT PK_terceros_actualizacion
                PRIMARY KEY CLUSTERED (id_actualizacion),
            CONSTRAINT UQ_terceros_actualizacion_revision
                UNIQUE (origen, tipo_fuente, id_carga_origen, hash_revision),
            CONSTRAINT FK_terceros_actualizacion_carga
                FOREIGN KEY (id_carga_terceros)
                REFERENCES ing.terceros_carga (id_carga_terceros),

            CONSTRAINT CK_terceros_actualizacion_fuente CHECK (
                (origen = 'JNC' AND tipo_fuente = 'LOTE') OR
                (origen = 'JRB' AND tipo_fuente IN ('EXCEL', 'PST'))
            ),
            CONSTRAINT CK_terceros_actualizacion_clave CHECK (
                LEN(id_carga_origen) > 0 AND
                DATALENGTH(id_carga_origen) =
                DATALENGTH(LTRIM(RTRIM(id_carga_origen)))
            ),
            CONSTRAINT CK_terceros_actualizacion_estado CHECK (
                estado IN ('PENDIENTE', 'EN_PROCESO', 'REINTENTO', 'COMPLETADA', 'AGOTADA')
            ),
            CONSTRAINT CK_terceros_actualizacion_intentos CHECK (
                max_intentos BETWEEN 1 AND 20 AND
                intentos BETWEEN 0 AND max_intentos AND
                ((estado = 'PENDIENTE' AND intentos = 0) OR
                 (estado = 'REINTENTO' AND intentos > 0 AND intentos < max_intentos) OR
                 (estado = 'AGOTADA' AND intentos = max_intentos) OR
                 (estado IN ('EN_PROCESO', 'COMPLETADA') AND intentos > 0))
            ),
            CONSTRAINT CK_terceros_actualizacion_ejecucion CHECK (
                (estado = 'PENDIENTE' AND id_ejecucion IS NULL AND inicio_intento_utc IS NULL) OR
                (estado <> 'PENDIENTE' AND id_ejecucion IS NOT NULL AND inicio_intento_utc IS NOT NULL)
            ),
            CONSTRAINT CK_terceros_actualizacion_reserva CHECK (
                (estado = 'EN_PROCESO' AND reserva_hasta_utc IS NOT NULL
                    AND reserva_hasta_utc > inicio_intento_utc) OR
                (estado <> 'EN_PROCESO' AND reserva_hasta_utc IS NULL)
            ),
            CONSTRAINT CK_terceros_actualizacion_final CHECK (
                (estado IN ('COMPLETADA', 'AGOTADA') AND finalizado_utc IS NOT NULL
                    AND finalizado_utc >= inicio_intento_utc) OR
                (estado NOT IN ('COMPLETADA', 'AGOTADA') AND finalizado_utc IS NULL)
            ),
            CONSTRAINT CK_terceros_actualizacion_resultado CHECK (
                (estado = 'COMPLETADA' AND id_carga_terceros IS NOT NULL
                    AND codigo_ultimo_error IS NULL) OR
                (estado <> 'COMPLETADA' AND id_carga_terceros IS NULL)
            ),
            CONSTRAINT CK_terceros_actualizacion_error CHECK (
                (estado IN ('REINTENTO', 'AGOTADA') AND codigo_ultimo_error IS NOT NULL
                    AND LEN(LTRIM(RTRIM(codigo_ultimo_error))) > 0) OR
                (estado = 'PENDIENTE' AND codigo_ultimo_error IS NULL) OR
                estado IN ('EN_PROCESO', 'COMPLETADA')
            ),
            CONSTRAINT CK_terceros_actualizacion_fechas CHECK (
                actualizado_utc >= creado_utc AND disponible_utc >= creado_utc AND
                (inicio_intento_utc IS NULL OR
                    (inicio_intento_utc >= creado_utc AND actualizado_utc >= inicio_intento_utc)) AND
                (finalizado_utc IS NULL OR actualizado_utc >= finalizado_utc)
            )
        );

        CREATE INDEX IX_terceros_actualizacion_trabajo
            ON ing.terceros_actualizacion (estado, disponible_utc, id_actualizacion)
            INCLUDE (id_ejecucion, reserva_hasta_utc, intentos, max_intentos);
    END;

    COMMIT TRANSACTION;
END TRY
BEGIN CATCH
    IF XACT_STATE() <> 0
        ROLLBACK TRANSACTION;
    THROW;
END CATCH;
