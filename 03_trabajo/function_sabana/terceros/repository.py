from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from pathlib import Path

from sabana.database import SESSION_OPTIONS
from .database import connect
from .domain import validate_snapshot, digest

MIGRATION = Path(__file__).resolve().parents[1]/"sql"/"09_catalogo_terceros.sql"
MIGRATIONS = [MIGRATION,MIGRATION.with_name("10_equivalencias_terceros.sql")]


def migrate():
    connection = connect("JRB", write=True)
    try:
        cursor = connection.cursor()
        cursor.execute(SESSION_OPTIONS)
        results=[]
        for version,path in enumerate(MIGRATIONS,1):
            script=path.read_text(encoding="utf-8-sig")
            fingerprint=hashlib.sha256(script.encode("utf-8")).digest()
            cursor.execute("EXEC sys.sp_set_session_context @key=N'terceros_migration_hash',@value=?;", fingerprint)
            cursor.execute(script)
            while cursor.nextset():pass
            row=cursor.execute("SELECT version,CONVERT(varchar(64),sha256,2) FROM cfg.migracion_terceros WHERE version=?;",version).fetchone()
            results.append({"version":row[0],"sha256":row[1].lower()})
        return {"migraciones":results,"aplicada":True}
    finally:
        connection.close()


def summary(document):
    payload = validate_snapshot(document)
    associations = {(r["id_entidad"],r["tipo_parte"],r["correo"]) for r in payload["asociaciones"]}
    by_origin = {}
    for origin in ("JNC", "JRB_PST"):
        by_origin[origin] = sum(r["origen"] == origin for r in payload["asociaciones"])
    return {"sha256":document["sha256"], "asociaciones_distintas":len(associations),
            "respaldos_por_origen":by_origin, "pendientes":len(payload["pendientes"]),
            "conteos":payload["conteos"], "version":payload["version"]}


def apply(document, reservation=None):
    payload = validate_snapshot(document)
    report = summary(document)
    connection = connect("JRB", write=True)
    cursor = connection.cursor()
    try:
        cursor.execute(SESSION_OPTIONS)
        cursor.execute("BEGIN TRANSACTION;")
        lock = cursor.execute("DECLARE @r int; EXEC @r=sys.sp_getapplock @Resource=N'terceros:carga',"
                              "@LockMode='Exclusive',@LockOwner='Transaction',@LockTimeout=10000; SELECT @r;").fetchone()[0]
        if lock < 0:
            raise ValueError("CARGA_TERCEROS_OCUPADA")
        if reservation is not None:
            from . import control
            control.assert_reserved(cursor, reservation)
        required=1 if payload["version"]=="terceros-v1" else 2
        expected_hash = hashlib.sha256(MIGRATIONS[required-1].read_text(encoding="utf-8-sig").encode("utf-8")).digest()
        version = cursor.execute("SELECT sha256 FROM cfg.migracion_terceros WHERE version=?;",required).fetchone()
        if not version or bytes(version[0]) != expected_hash:
            raise ValueError("MIGRACION_NO_COINCIDE")
        known = cursor.execute("SELECT id_carga_terceros FROM ing.terceros_carga WHERE sha256=?;",
                               bytes.fromhex(document["sha256"])).fetchone()
        if known:
            if reservation is not None:
                current = cursor.execute("SELECT c.id_carga_terceros,c.capturado_utc FROM ing.terceros_publicacion p "
                                         "JOIN ing.terceros_carga c ON c.id_carga_terceros=p.id_carga_terceros;").fetchone()
                captured = datetime.fromisoformat(document["capturado_utc"]).replace(tzinfo=None)
                if current and current[0] != known[0] and captured < current[1]:
                    raise ValueError("INSTANTANEA_ANTERIOR_A_PUBLICACION")
                if not current or current[0] != known[0]:
                    publish(cursor, known[0])
                control.complete(cursor, reservation, known[0])
            cursor.execute("COMMIT TRANSACTION;")
            return dict(report, id_carga_terceros=known[0], reutilizada=True)
        captured = datetime.fromisoformat(document["capturado_utc"]).replace(tzinfo=None)
        current = cursor.execute("SELECT c.capturado_utc FROM ing.terceros_publicacion p JOIN ing.terceros_carga c "
                                 "ON c.id_carga_terceros=p.id_carga_terceros;").fetchone()
        if current and captured < current[0]:
            raise ValueError("INSTANTANEA_ANTERIOR_A_PUBLICACION")
        expected_names = {e["id_entidad"]:e["nombre"] for e in payload["entidades"]}
        actual_names = {r[0]:r[1] for r in cursor.execute("SELECT id_entidad,nombre FROM core.entidad;").fetchall()}
        used_ids={r["id_entidad"] for r in payload["asociaciones"]}
        for row in payload.get("normalizacion",{}).get("equivalencias",[]):
            used_ids.update(row[k] for k in ("id_entidad_origen","id_entidad_representante","id_entidad_candidata") if row[k] is not None)
        for eid in used_ids:
            if actual_names.get(eid) != expected_names.get(eid):
                raise ValueError("ENTIDAD_CAMBIO_DESDE_CAPTURA")
        load = cursor.execute("INSERT ing.terceros_carga(sha256,version_regla,capturado_utc,asociaciones,pendientes) "
                              "OUTPUT inserted.id_carga_terceros VALUES(?,?,?,?,?);",
                              bytes.fromhex(document["sha256"]),payload["version"],captured,
                              report["asociaciones_distintas"],report["pendientes"]).fetchone()[0]
        import pyodbc
        def batch(sql, values, sizes):
            cursor.fast_executemany = True
            cursor.setinputsizes(sizes)
            try:
                for offset in range(0,len(values),500):
                    cursor.executemany(sql,values[offset:offset+500])
            finally:
                cursor.fast_executemany = False
                cursor.setinputsizes([])
        integer = (pyodbc.SQL_INTEGER,0,0)
        text = lambda length:(pyodbc.SQL_WVARCHAR,length,0)
        binary = (pyodbc.SQL_VARBINARY,32,0)
        day = (pyodbc.SQL_TYPE_DATE,0,0)
        existing = cursor.execute("SELECT id_direccion,direccion,hash_direccion FROM mail.direccion WITH(UPDLOCK,HOLDLOCK) WHERE tipo='SMTP';").fetchall()
        directions = {r[1]:r[0] for r in existing}
        hashes = {bytes(r[2]):r[1] for r in existing}
        addresses = sorted({r["correo"] for r in payload["asociaciones"]})
        for email in addresses:
            h = hashlib.sha256(email.encode("utf-16le")).digest()
            if h in hashes and hashes[h] != email:
                raise ValueError("DIRECCION_HUELLA_CONFLICTIVA")
        batch("INSERT mail.direccion(tipo,direccion) VALUES('SMTP',?);",
              [(e,) for e in addresses if e not in directions],[text(1024)])
        directions = {r[1]:r[0] for r in cursor.execute("SELECT id_direccion,direccion FROM mail.direccion WHERE tipo='SMTP';").fetchall()}
        associations = {(r[1],r[2],r[3]):r[0] for r in cursor.execute("SELECT id_entidad_correo,id_entidad,tipo_parte,id_direccion FROM ref.entidad_correo;").fetchall()}
        pairs = sorted({(r["id_entidad"],r["tipo_parte"],directions[r["correo"]]) for r in payload["asociaciones"]})
        batch("INSERT ref.entidad_correo(id_entidad,tipo_parte,id_direccion) VALUES(?,?,?);",
              [p for p in pairs if p not in associations],[integer,text(20),integer])
        associations = {(r[1],r[2],r[3]):r[0] for r in cursor.execute("SELECT id_entidad_correo,id_entidad,tipo_parte,id_direccion FROM ref.entidad_correo;").fetchall()}
        facts = []
        for row in payload["asociaciones"]:
            pair = (row["id_entidad"],row["tipo_parte"],directions[row["correo"]])
            facts.append((load,associations[pair],row["origen"],row["casos_distintos"],
                          row["casos_entidad_con_correo"],row["observaciones"],row["casos_certificado_exacto"],
                          date.fromisoformat(row["primera_observacion"]) if row["primera_observacion"] else None,
                          date.fromisoformat(row["ultima_observacion"]) if row["ultima_observacion"] else None,
                          bytes.fromhex(row["hash_soporte"]),row["referencia_muestra"],json.dumps(row["fuentes"],sort_keys=True)))
        batch("INSERT ref.entidad_correo_fuente(id_carga_terceros,id_entidad_correo,origen,casos_distintos,"
                           "casos_entidad_con_correo,observaciones,casos_certificado_exacto,primera_observacion,ultima_observacion,hash_soporte,referencia_muestra,detalle_fuentes) "
              "VALUES(?,?,?,?,?,?,?,?,?,?,?,?);",facts,
              [integer,integer,text(10),integer,integer,integer,integer,day,day,binary,text(200),text(1000)])
        pending = [(load,r["origen"],r["tipo_parte"],r["nombre_origen"],r["correo_origen"],
                    r["codigo"],r["ocurrencias"],r["referencia"],bytes.fromhex(r["hash_soporte"])) for r in payload["pendientes"]]
        batch("INSERT ing.terceros_pendiente(id_carga_terceros,origen,tipo_parte,nombre_origen,correo_origen,"
              "codigo,ocurrencias,referencia,hash_soporte) VALUES(?,?,?,?,?,?,?,?,?);",pending,
              [integer,text(10),text(20),text(500),text(1024),text(50),integer,text(200),binary])
        normal=payload.get("normalizacion")
        if normal:
            from .matching import plain
            from decimal import Decimal
            equivalences=[]
            for r in normal["equivalencias"]:
                h=digest([r["origen"],r["tipo_parte"],r["id_entidad_origen"] or 0,plain(r["nombre_origen"])])
                equivalences.append((load,bytes.fromhex(h),r["origen"],r["tipo_parte"],r["nombre_origen"],
                     r["id_entidad_origen"],r["id_entidad_representante"],r["id_entidad_candidata"],r["metodo"],
                     Decimal(str(r["puntaje"])),Decimal(str(r["segundo_puntaje"])),Decimal(str(normal["umbral"])),
                     Decimal(str(normal["margen"])),normal["version"]))
            numeric=(pyodbc.SQL_DECIMAL,5,2)
            batch("INSERT ref.entidad_equivalencia(id_carga_terceros,hash_clave,origen,tipo_parte,nombre_origen,"
                  "id_entidad_origen,id_entidad_representante,id_entidad_candidata,metodo,puntaje,segundo_puntaje,umbral,margen,version_normalizacion) "
                  "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?);",equivalences,
                  [integer,binary,text(3),text(20),text(500),integer,integer,integer,text(20),numeric,numeric,numeric,numeric,text(50)])
        # Una sola publicacion visible al finalizar. No se modifican revisiones.
        publish(cursor, load)
        if reservation is not None:
            control.complete(cursor, reservation, load)
        cursor.execute("COMMIT TRANSACTION;")
        return dict(report, id_carga_terceros=load, reutilizada=False)
    except Exception:
        try:
            cursor.execute("IF @@TRANCOUNT>0 ROLLBACK TRANSACTION;")
        except Exception:
            pass
        raise
    finally:
        connection.close()


def publish(cursor, load_id):
    if cursor.execute("SELECT id FROM ing.terceros_publicacion WHERE id=1;").fetchone():
        cursor.execute("UPDATE ing.terceros_publicacion SET id_carga_terceros=? WHERE id=1;", load_id)
    else:
        cursor.execute("INSERT ing.terceros_publicacion(id,id_carga_terceros) VALUES(1,?);", load_id)
