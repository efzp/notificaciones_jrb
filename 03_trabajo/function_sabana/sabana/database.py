from __future__ import annotations

import hashlib
import json
import os
import re
import struct
import time
from contextlib import contextmanager

from .domain import (AppError, FieldSpec, Issue, MODEL_VERSION, NOTIFICATION_FIELDS,
                     PART_NAMES, PARTS, PARSER_VERSION, Payload, Profile, fact_hash)

SESSION_OPTIONS = """
SET NOCOUNT ON;
SET XACT_ABORT ON;
SET ANSI_NULLS ON;
SET ANSI_PADDING ON;
SET ANSI_WARNINGS ON;
SET ARITHABORT ON;
SET CONCAT_NULL_YIELDS_NULL ON;
SET QUOTED_IDENTIFIER ON;
SET NUMERIC_ROUNDABORT OFF;
SET LOCK_TIMEOUT 10000;
"""
SQL_IDS = {
    "ing.archivo": "id_archivo", "ing.carga": "id_carga", "core.caso": "id_caso",
    "core.entidad": "id_entidad", "core.caso_dato": "id_caso_dato",
    "core.caso_parte": "id_caso_parte", "core.notificacion_declarada": "id_notificacion",
}


def sql_number(error) -> int | None:
    # No devolver ni registrar el texto del driver: puede contener valores del origen.
    matches = re.findall(r"\((\d{3,6})\)", " ".join(str(a) for a in getattr(error, "args", ())))
    numbers = [int(n) for n in matches if int(n) != 0]
    priority = next((n for n in numbers if 51100 <= n <= 51499), None)
    if priority is not None:
        return priority
    return next((n for n in numbers if n in {1205, 1222, 2601, 2627, 40197, 40501, 40613, 10928, 10929, 49918, 49919, 49920}),
                numbers[0] if numbers else None)


def retryable_sql(error) -> bool:
    if sql_number(error) in {1205, 1222, 2601, 2627, 40197, 40501, 40613, 10928, 10929, 49918, 49919, 49920}:
        return True
    return bool(getattr(error, "args", ())) and error.args[0] in {"08S01", "08001", "HYT00", "HYT01"}


def connect_sql():
    import pyodbc
    from azure.identity import DefaultAzureCredential, ManagedIdentityCredential

    server, database = os.environ.get("SQL_SERVER", ""), os.environ.get("SQL_DATABASE", "")
    if not re.fullmatch(r"[a-z0-9-]+\.database\.windows\.net", server, re.I):
        raise AppError("CONFIGURACION_SQL", "Configure SQL_SERVER con el FQDN del servidor.", 503)
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", database) or database.lower() in {
        "master", "tempdb", "model", "msdb", "sqldb-jnc-notif-dev"
    }:
        raise AppError("CONFIGURACION_SQL", "Configure la base exclusiva del nuevo proyecto.", 503)
    driver = os.environ.get("ODBC_DRIVER", "ODBC Driver 18 for SQL Server")
    if driver not in pyodbc.drivers():
        raise AppError("ODBC_NO_DISPONIBLE", "Instale Microsoft ODBC Driver 18 o configure ODBC_DRIVER.", 503)
    # Cerrar la conexion fisica permite que SQL serverless llegue a pausarse.
    pyodbc.pooling = False
    if os.environ.get("WEBSITE_HOSTNAME") or os.environ.get("WEBSITE_INSTANCE_ID"):
        credential = ManagedIdentityCredential(client_id=os.environ.get("AZURE_CLIENT_ID") or None)
    else:
        credential = DefaultAzureCredential(exclude_interactive_browser_credential=True)
    try:
        token = credential.get_token("https://database.windows.net/.default").token.encode("utf-16le")
    finally:
        credential.close()
    packed = struct.pack(f"<I{len(token)}s", len(token), token)
    connection = pyodbc.connect(
        f"Driver={{{driver}}};Server=tcp:{server},1433;Database={database};"
        "Encrypt=yes;TrustServerCertificate=no;",
        attrs_before={1256: packed}, autocommit=True, timeout=15,
    )
    connection.timeout = 20
    try:
        cursor = connection.cursor()
        cursor.execute(SESSION_OPTIONS)
        actual = cursor.execute("SELECT DB_NAME()").fetchone()[0]
        if actual != database:
            raise AppError("BASE_INCORRECTA", "La conexion apunta a otra base.", 503)
        cursor.close()
        return connection
    except Exception:
        connection.close()
        raise


class SqlRepository:
    def __init__(self, connection, deadline: float):
        self.connection = connection
        self.cursor = connection.cursor()
        self.deadline = deadline
        self.cache = {}

    def close(self):
        try:
            self.cursor.close()
        finally:
            self.connection.close()

    def execute(self, sql: str, parameters=()):
        if time.monotonic() >= self.deadline:
            raise AppError("TIEMPO_AGOTADO", "Se agoto el tiempo de la solicitud; la carga puede reintentarse.", 503)
        return self.cursor.execute(sql, tuple(parameters))

    def one(self, sql: str, parameters=()):
        cursor = self.execute(sql, parameters)
        row = cursor.fetchone()
        return dict(zip((d[0] for d in cursor.description), row)) if row is not None else None

    def all(self, sql: str, parameters=()):
        cursor = self.execute(sql, parameters)
        names = [d[0] for d in cursor.description]
        return [dict(zip(names, row)) for row in cursor.fetchall()]

    def procedure(self, sql, parameters=()):
        cursor = self.execute(sql, parameters)
        first = None
        while True:
            if cursor.description:
                names = [d[0] for d in cursor.description]
                rows = cursor.fetchall()
                if first is None and rows:
                    first = dict(zip(names, rows[0]))
            if not cursor.nextset():
                break
        return first

    @contextmanager
    def transaction(self):
        self.cache = {}
        try:
            # BEGIN explicito: sp_getapplock exige una transaccion activa.
            self.execute("BEGIN TRANSACTION;")
            yield
            self.execute("COMMIT TRANSACTION;")
        except Exception:
            try:
                # No aplicar el deadline al rollback de una escritura incompleta.
                self.cursor.execute("IF XACT_STATE()<>0 ROLLBACK TRANSACTION;")
            except Exception:
                pass  # El cierre de la sesion libera una conexion rota.
            raise
        finally:
            self.cache = {}

    def lock_file(self, payload: Payload):
        resource = "sabana:" + fact_hash({"drive_id": payload.drive_id, "item_id": payload.item_id}).hex()
        result = self.one(
            "DECLARE @resultado int; EXEC @resultado=sys.sp_getapplock "
            "@Resource=?,@LockMode='Exclusive',@LockOwner='Transaction',@LockTimeout=0; "
            "SELECT @resultado AS resultado;", (resource,)
        )
        if result["resultado"] < 0:
            raise AppError("ARCHIVO_OCUPADO", "El archivo ya se esta procesando; reintente.", 503)

    def profile(self, profile_id: int) -> Profile:
        with self.transaction():
            header = self.one(
                "SELECT p.id_perfil,p.fecha_minima,p.fecha_maxima,p.version_parser,p.version_modelo "
                "FROM cfg.perfil_entrada p JOIN cfg.calendario c ON c.id_calendario=p.id_calendario "
                "WHERE p.id_perfil=? AND p.publicado=1 AND c.publicado=1 "
                "AND c.fecha_desde<=p.fecha_minima AND c.fecha_hasta>=p.fecha_maxima "
                "AND (SELECT COUNT_BIG(*) FROM cfg.dia_calendario d "
                "WHERE d.id_calendario=c.id_calendario AND d.fecha BETWEEN p.fecha_minima AND p.fecha_maxima) "
                "=DATEDIFF(day,p.fecha_minima,p.fecha_maxima)+1;", (profile_id,)
            )
            if not header:
                raise AppError("PERFIL_NO_DISPONIBLE", "El perfil no existe, no esta publicado o falta calendario.", 422)
            specs = self.all(
                "SELECT campo,orden,tipo_dato,obligatorio,longitud_maxima,tipo_parte,bit_no_aplica "
                "FROM cfg.campo_entrada WHERE id_perfil=? ORDER BY orden;", (profile_id,)
            )
            aliases = self.all("SELECT alias_normalizado,campo FROM cfg.alias_columna WHERE id_perfil=?;", (profile_id,))
            profile = Profile(**header, fields=tuple(FieldSpec(**s) for s in specs),
                              aliases={r["alias_normalizado"]: r["campo"] for r in aliases})
            profile.validate()
            return profile

    def ensure(self, table: str, key: dict, attributes: dict | None = None) -> int:
        """Buscar/insertar con bloqueo de rango y comprobar el contenido del hash."""
        id_column = SQL_IDS[table]  # Identificadores SQL solo de constantes del programa.
        attributes = attributes or {}
        expected = dict(key, **attributes)
        cache_key = (table, tuple(key.items()))
        if cache_key in self.cache:
            result, previous = self.cache[cache_key]
            if previous != expected:
                raise AppError("COLISION_DATOS", "Un hash o identidad corresponde a datos diferentes.", 409)
            return result
        conditions, parameters = [], []
        for name, value in key.items():
            conditions.append(f"[{name}] IS NULL" if value is None else f"[{name}]=?")
            if value is not None:
                parameters.append(value)
        columns = ",".join(f"[{name}]" for name in expected)
        row = self.one(f"SELECT [{id_column}],{columns} FROM {table} WITH(UPDLOCK,HOLDLOCK) "
                       f"WHERE {' AND '.join(conditions)};", parameters)
        if row:
            if any(row[name] != value for name, value in expected.items()):
                raise AppError("COLISION_DATOS", "Un hash o identidad corresponde a datos diferentes.", 409)
            result = row[id_column]
        else:
            inserted = {k: v for k, v in expected.items() if not (table == "core.entidad" and k == "hash_nombre")}
            insert_columns = ",".join(f"[{name}]" for name in inserted)
            row = self.one(f"INSERT {table}({insert_columns}) OUTPUT inserted.[{id_column}] "
                           f"VALUES({','.join('?' for _ in inserted)});", tuple(inserted.values()))
            result = row[id_column]
        self.cache[cache_key] = (result, expected)
        return result

    def register(self, payload: Payload, parsed):
        with self.transaction():
            self.lock_file(payload)
            existing = self.one("SELECT id_archivo FROM ing.archivo WITH(UPDLOCK,HOLDLOCK) WHERE drive_id=? AND item_id=?;",
                                (payload.drive_id, payload.item_id))
            if existing:
                archive_id = existing["id_archivo"]
            else:
                archive_id = self.one(
                    "INSERT ing.archivo(drive_id,item_id,nombre_actual,ruta_actual) "
                    "OUTPUT inserted.id_archivo VALUES(?,?,?,?);",
                    (payload.drive_id, payload.item_id, payload.nombre_archivo, payload.ruta_sharepoint)
                )["id_archivo"]
            load = self.one(
                "SELECT id_carga,estado,tamano_bytes,estructura_origen_json FROM ing.carga WITH(UPDLOCK,HOLDLOCK) "
                "WHERE id_archivo=? AND hash_contenido=? AND id_perfil=?;",
                (archive_id, payload.content_hash, payload.id_perfil)
            )
            if load:
                structure = json.loads(load["estructura_origen_json"] or "{}")
                if load["tamano_bytes"] != len(payload.content) or structure.get("sala", payload.sala) != payload.sala:
                    raise AppError("CONTEXTO_CONFLICTIVO", "El mismo contenido se registro con otro contexto de sala o tamano.", 409)
                return archive_id, load["id_carga"], False
            structure = dict(parsed.structure, sala=payload.sala, version_parser=PARSER_VERSION, version_modelo=MODEL_VERSION)
            load_id = self.one(
                "INSERT ing.carga(id_archivo,id_perfil,hash_contenido,tamano_bytes,version_origen_inicial,"
                "nombre_origen_inicial,ruta_origen_inicial,estructura_origen_json) "
                "OUTPUT inserted.id_carga VALUES(?,?,?,?,?,?,?,?);",
                (archive_id, payload.id_perfil, payload.content_hash, len(payload.content), payload.version_origen,
                 payload.nombre_archivo, payload.ruta_sharepoint, json.dumps(structure, ensure_ascii=False))
            )["id_carga"]
            return archive_id, load_id, True

    def issue(self, load_id: int, issue: Issue, row_id: int | None = None):
        detail = (f"Fila {issue.fila}. " if issue.fila is not None else "") + issue.detalle
        self.execute(
            "INSERT ing.incidencia(id_carga,id_fila,codigo,severidad,columna_origen,detalle) VALUES(?,?,?,?,?,?);",
            (load_id, row_id, issue.codigo, issue.severidad, issue.campo, detail)
        )

    def record_failure(self, payload, load_id, issues=()):
        with self.transaction():
            self.lock_file(payload)
            load = self.one("SELECT estado FROM ing.carga WITH(UPDLOCK,HOLDLOCK) WHERE id_carga=?;", (load_id,))
            if not load or load["estado"] in {"VALIDADA", "PUBLICADA"}:
                return
            # Reenviar el mismo archivo rechazado no repite todas sus incidencias.
            if load["estado"] == "ERROR":
                return
            for issue in issues:
                self.issue(load_id, issue)
            self.procedure("EXEC ing.cambiar_estado_carga @id_carga=?,@accion='ERROR',@detalle=?;",
                           (load_id, "Archivo no publicado. Revise las incidencias de la carga."))

    def _entity(self, name):
        if name is None:
            return None
        return self.ensure("core.entidad", {"hash_nombre": hashlib.sha256(name.encode("utf-16le")).digest()}, {"nombre": name})

    def _insert_row(self, payload, load_id, sheet, row):
        values = row.values
        case_id = self.ensure("core.caso", {
            "sala": payload.sala, "fecha_audiencia": values["fecha_audiencia"],
            "reparto_paciente": values["reparto_paciente"], "identificacion_paciente": values["identificacion_paciente"],
        })
        case_data = {name: values[name] for name in ("tipo_de_dictamen", "nombre_paciente", "medico_ponente", "fecha_fijacion", "fecha_desfijacion")}
        case_data_id = self.ensure("core.caso_dato", {"id_caso": case_id, "hash_datos": fact_hash(case_data)}, case_data)
        sender_id = self._entity(values["entidad_remitente"])
        row_id = self.one(
            # SQL exige OUTPUT INTO cuando la tabla tiene un trigger INSERT.
            "DECLARE @filas_insertadas TABLE (id_fila bigint NOT NULL); "
            "INSERT ing.fila_origen(id_carga,id_perfil,hoja,numero_fila,id_caso,id_caso_dato,id_entidad_remitente) "
            "OUTPUT inserted.id_fila INTO @filas_insertadas(id_fila) VALUES(?,?,?,?,?,?,?); "
            "SELECT id_fila FROM @filas_insertadas;",
            (load_id, payload.id_perfil, sheet, row.number, case_id, case_data_id, sender_id)
        )["id_fila"]
        for part in PARTS:
            name = values[PART_NAMES[part]] if part != "paciente" else None
            data = {target: values[f"{source}_{part}"] for source, target in NOTIFICATION_FIELDS.items()}
            has_data = any(value is not None for value in data.values())
            mask = row.masks.get(part, 0)
            if part != "paciente" and name is None and not has_data and not mask:
                continue
            party_id = notification_id = None
            if part == "paciente" or name is not None or has_data:
                anonymous = ""
                if part != "paciente" and name is None:
                    anonymous = "origen:" + fact_hash({"drive": payload.drive_id, "item": payload.item_id,
                                                        "hoja": sheet, "fila": row.number, "parte": part}).hex()
                party_id = self.ensure("core.caso_parte", {"id_caso": case_id, "tipo_parte": part,
                                       "id_entidad": self._entity(name), "referencia_anonima": anonymous})
                if has_data:
                    notification_id = self.ensure("core.notificacion_declarada",
                                                  {"id_caso_parte": party_id, "hash_datos": fact_hash(data)}, data)
            self.execute(
                "INSERT ing.parte_fila(id_fila,tipo_parte,id_caso,id_caso_parte,id_notificacion,mascara_no_aplica) VALUES(?,?,?,?,?,?);",
                (row_id, part, case_id, party_id, notification_id, mask)
            )
        for original in row.originals:
            self.execute(
                "INSERT ing.valor_original(id_fila,id_perfil,campo,valor_original,motivo,parser_aplicado,mostrar_en_consolidado) "
                "VALUES(?,?,?,?,?,?,?);",
                (row_id, payload.id_perfil, original.campo, original.valor_original, original.motivo,
                 original.parser_aplicado, original.mostrar_en_consolidado)
            )
        return row_id

    def ingest(self, payload, load_id, parsed):
        with self.transaction():
            self.lock_file(payload)
            load = self.one("SELECT estado FROM ing.carga WITH(UPDLOCK,HOLDLOCK) WHERE id_carga=?;", (load_id,))
            reused = load["estado"] in {"VALIDADA", "PUBLICADA"}
            if not reused:
                unresolved = self.one("SELECT COUNT(*) AS cantidad FROM ing.incidencia WHERE id_carga=? "
                                      "AND severidad='E' AND resuelta=0 AND codigo<>'ERROR_PROCESAMIENTO';", (load_id,))
                if unresolved["cantidad"]:
                    raise AppError("CARGA_RECHAZADA", "El contenido tiene errores pendientes; corrija el archivo o use un perfil nuevo.", 422)
                self.procedure("EXEC ing.cambiar_estado_carga @id_carga=?,@accion='INICIAR';", (load_id,))
                row_ids = {row.number: self._insert_row(payload, load_id, parsed.sheet, row) for row in parsed.rows}
                for issue in parsed.issues:
                    self.issue(load_id, issue, row_ids.get(issue.fila))
                self.procedure("EXEC ing.cambiar_estado_carga @id_carga=?,@accion='VALIDAR',@filas_esperadas=?;",
                               (load_id, parsed.total_rows))
            publication = self.procedure(
                "EXEC ing.publicar_carga @id_carga=?,@version_origen=?,@fecha_modificacion_origen=?,"
                "@nombre_archivo=?,@ruta_sharepoint=?;",
                (load_id, payload.version_origen, payload.fecha_modificacion_origen, payload.nombre_archivo, payload.ruta_sharepoint)
            )
            counts = self.one("SELECT COUNT(*) AS advertencias FROM ing.incidencia WHERE id_carga=? AND severidad='W' AND resuelta=0;", (load_id,))
            return {"reutilizada": reused, "cambio_carga_vigente": bool(publication["cambio_carga_vigente"]),
                    "advertencias": counts["advertencias"]}
