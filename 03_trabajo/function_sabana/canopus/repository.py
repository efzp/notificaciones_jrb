"""Publicacion atomica; reutiliza filas por contenido y direcciones SMTP existentes."""
from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from datetime import datetime

from sabana.database import SESSION_OPTIONS
from terceros.database import connect
from terceros.matching import EntityMatcher
from .domain import CanopusError


@contextmanager
def transaction(connection):
    cursor = connection.cursor()
    nested = cursor.execute("SELECT @@TRANCOUNT;").fetchone()[0] > 0
    cursor.execute("SAVE TRANSACTION canopus_tx;" if nested else "BEGIN TRANSACTION;")
    try:
        yield cursor
        if not nested:
            cursor.execute("COMMIT TRANSACTION;")
    except Exception:
        state = cursor.execute("SELECT XACT_STATE();").fetchone()[0]
        if state == 1:
            cursor.execute("ROLLBACK TRANSACTION canopus_tx;" if nested else "ROLLBACK TRANSACTION;")
        elif state == -1:
            cursor.execute("ROLLBACK TRANSACTION;")
        raise
    finally:
        cursor.close()


def lock(cursor, resource):
    result = cursor.execute("DECLARE @r int; EXEC @r=sys.sp_getapplock @Resource=?,"
                            "@LockMode='Exclusive',@LockOwner='Transaction',@LockTimeout=10000; SELECT @r;",
                            resource).fetchone()[0]
    if result < 0:
        raise RuntimeError("CANOPUS_BLOQUEO_NO_DISPONIBLE")


def file_lock(info):
    return "canopus:" + hashlib.sha256((info["drive_id"] + ":" + info["item_id"]).encode()).hexdigest()


def modified(info):
    return datetime.fromisoformat(info["fecha_modificacion_origen"]).replace(tzinfo=None)


def bulk(cursor, sql, rows, sizes):
    if not rows:
        return
    cursor.fast_executemany = True
    cursor.setinputsizes(sizes)
    try:
        for start in range(0, len(rows), 500):
            cursor.executemany(sql, rows[start:start + 500])
    finally:
        cursor.fast_executemany = False
        cursor.setinputsizes([])


class Repository:
    def __init__(self):
        self.connection = connect("JRB", write=True)
        try:
            self.connection.cursor().execute(SESSION_OPTIONS)
        except Exception:
            self.connection.close()
            raise

    def close(self):
        self.connection.close()

    def register(self, info):
        with transaction(self.connection) as cursor:
            lock(cursor, file_lock(info))
            row = cursor.execute("""SELECT id_carga_canopus FROM ing.canopus_carga
                WHERE drive_id=? AND item_id=? AND hash_contenido=? AND version_parser=?;""",
                info["drive_id"], info["item_id"], bytes.fromhex(info["hash_contenido"]), info["version_parser"]).fetchone()
            if row:
                return row[0]
            return cursor.execute("""INSERT ing.canopus_carga(drive_id,item_id,nombre_archivo,version_origen,
                hash_contenido,version_parser,modificado_origen_utc,estado)
                OUTPUT inserted.id_carga_canopus VALUES(?,?,?,?,?,?,?,'PROCESANDO');""",
                info["drive_id"], info["item_id"], info["nombre_archivo"], info["version_origen"],
                bytes.fromhex(info["hash_contenido"]), info["version_parser"], modified(info)).fetchone()[0]

    def matcher(self):
        cursor = self.connection.cursor()
        try:
            entities = [dict(id_entidad=r[0], nombre=r[1]) for r in cursor.execute("SELECT id_entidad,nombre FROM core.entidad;")]
            roles = [dict(id_entidad=r[0], tipo_parte="ultimoempleador") for r in cursor.execute("""
                SELECT DISTINCT cp.id_entidad FROM ing.archivo a
                JOIN ing.carga c ON c.id_carga=a.id_carga_vigente AND c.estado='PUBLICADA'
                JOIN ing.fila_origen f ON f.id_carga=c.id_carga
                JOIN ing.parte_fila p ON p.id_fila=f.id_fila AND p.tipo_parte='ultimoempleador'
                JOIN core.caso_parte cp ON cp.id_caso_parte=p.id_caso_parte WHERE cp.id_entidad IS NOT NULL;""")]
            return EntityMatcher(entities, roles, threshold=95, margin=5)
        finally:
            cursor.close()

    def mark_error(self, load):
        # Nunca invalidar una publicacion confirmada aunque se haya perdido su respuesta.
        self.connection.cursor().execute("UPDATE ing.canopus_carga SET estado='ERROR' WHERE id_carga_canopus=? AND estado<>'PUBLICADA';", load)

    def publish(self, load, info, rows, counts):
        import pyodbc
        nv = lambda size: (pyodbc.SQL_WVARCHAR, size, 0)
        integer, big = (pyodbc.SQL_INTEGER, 0, 0), (pyodbc.SQL_BIGINT, 0, 0)
        with transaction(self.connection) as cursor:
            # Mismo bloqueo que la publicacion del maestro. Orden fijo antes del archivo.
            lock(cursor, "terceros:carga")
            lock(cursor, file_lock(info))
            target = cursor.execute("SELECT estado,modificado_origen_utc FROM ing.canopus_carga WITH(UPDLOCK,HOLDLOCK) WHERE id_carga_canopus=?;", load).fetchone()
            current = cursor.execute("""SELECT id_carga_canopus,modificado_origen_utc,hash_contenido
                FROM ing.canopus_carga WITH(UPDLOCK,HOLDLOCK) WHERE drive_id=? AND item_id=? AND vigente=1;""",
                info["drive_id"], info["item_id"]).fetchone()
            when = modified(info)
            if current:
                if current[1] is None or when < current[1]:
                    raise CanopusError("CANOPUS_VERSION_ANTIGUA", 409)
                if when == current[1] and bytes(current[2]) != bytes.fromhex(info["hash_contenido"]):
                    raise CanopusError("CANOPUS_VERSION_CONFLICTIVA", 409)
            reused = target[0] == "PUBLICADA"
            if not reused:
                cursor.execute("UPDATE ing.canopus_carga SET estado='PROCESANDO' WHERE id_carga_canopus=?;", load)
                bulk(cursor, """INSERT ing.canopus_fila(id_carga_canopus,hoja,numero_fila,cedula,
                    fecha_audiencia_reportada,reparto_reportado,empleador_reportado,id_entidad_empleador,estado_empleador)
                    VALUES(?,?,?,?,?,?,?,?,?);""",
                    [(load, r["hoja"], r["numero"], r["cedula"], r["fecha"], r["reparto"], r["empleador"], r["entidad"], r["estado_empleador"]) for r in rows],
                    [integer, nv(128), integer, nv(50), (pyodbc.SQL_TYPE_DATE, 0, 0), nv(100), nv(500), integer, (pyodbc.SQL_VARCHAR, 12, 0)])
                positions = {(r[0], r[1]): r[2] for r in cursor.execute("SELECT hoja,numero_fila,id_fila_canopus FROM ing.canopus_fila WHERE id_carga_canopus=?;", load)}
                addresses = sorted({c["correo"] for r in rows for c in r["contactos"] if c["correo"]})
                mapping = {}
                # OPENJSON parametrizado evita tablas temporales incompatibles con ciertos
                # modos de preparacion del controlador ODBC y reduce viajes por direccion.
                for start in range(0, len(addresses), 500):
                    document = json.dumps(addresses[start:start + 500], ensure_ascii=True)
                    cursor.execute("""INSERT mail.direccion(tipo,direccion) SELECT 'SMTP',s.direccion
                        FROM OPENJSON(CAST(? AS nvarchar(max))) WITH(direccion nvarchar(1024) '$') s
                        WHERE NOT EXISTS(SELECT 1 FROM mail.direccion d WITH(UPDLOCK,HOLDLOCK)
                        WHERE d.tipo='SMTP' AND d.hash_direccion=CONVERT(binary(32),HASHBYTES('SHA2_256',CONVERT(varbinary(2048),s.direccion))));""", document)
                    mapping.update((r[0], r[1]) for r in cursor.execute("""SELECT d.direccion,d.id_direccion FROM mail.direccion d
                        JOIN OPENJSON(CAST(? AS nvarchar(max))) WITH(direccion nvarchar(1024) '$') s ON d.tipo='SMTP'
                        AND d.hash_direccion=CONVERT(binary(32),HASHBYTES('SHA2_256',CONVERT(varbinary(2048),s.direccion)));""", document))
                if set(mapping) != set(addresses):
                    raise RuntimeError("CANOPUS_COLISION_DIRECCION")
                contact_rows = [(positions[(r["hoja"], r["numero"])], c["rol"], c["campo"], mapping.get(c["correo"]),
                                 c["estado"], c["valor"], c["codigo"]) for r in rows for c in r["contactos"]]
                bulk(cursor, """INSERT ref.contacto_reportado(origen,id_fila_canopus,tipo_parte,campo_origen,
                    id_direccion,estado,valor_no_utilizable,codigo_revision) VALUES('CANOPUS',?,?,?,?,?,?,?);""", contact_rows,
                    [big, (pyodbc.SQL_VARCHAR, 20, 0), nv(128), integer, (pyodbc.SQL_VARCHAR, 12, 0), nv(1024), (pyodbc.SQL_VARCHAR, 64, 0)])
            else:
                # El mapping puede haber cambiado desde la primera publicacion: informar lo almacenado.
                counts = {k: v for k, v in counts.items() if not k.startswith('empleadores_')}
                for state, count in cursor.execute("SELECT estado_empleador,COUNT(*) FROM ing.canopus_fila WHERE id_carga_canopus=? GROUP BY estado_empleador;", load):
                    counts['empleadores_' + state.lower()] = count
                actual = cursor.execute("""SELECT COUNT(*),COALESCE(SUM(CASE WHEN c.estado='REVISION' THEN 1 ELSE 0 END),0)
                    FROM ref.contacto_reportado c JOIN ing.canopus_fila f ON f.id_fila_canopus=c.id_fila_canopus
                    WHERE f.id_carga_canopus=?;""", load).fetchone()
                counts.update(contactos=actual[0], contactos_revision=actual[1])
            cursor.execute("UPDATE ing.canopus_carga SET vigente=0 WHERE drive_id=? AND item_id=? AND vigente=1 AND id_carga_canopus<>?;",
                           info["drive_id"], info["item_id"], load)
            cursor.execute("""UPDATE ing.canopus_carga SET estado='PUBLICADA',vigente=1,
                publicado_utc=COALESCE(publicado_utc,SYSUTCDATETIME()),filas_procesadas=?,
                nombre_archivo=?,version_origen=?,modificado_origen_utc=? WHERE id_carga_canopus=?;""",
                len(rows), info["nombre_archivo"], info["version_origen"], when, load)
        return dict(estado="PUBLICADA", id_carga_canopus=load, contenido_reutilizado=reused, **counts)
