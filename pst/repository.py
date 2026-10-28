from __future__ import annotations

import hashlib
import json
import time
from contextlib import contextmanager
from datetime import datetime

from sabana.database import SqlRepository, connect_sql
from .domain import EXTRACTOR_VERSION, MODEL_VERSION, PstFailure

IDS = {"ing.pst_contenido": "id_contenido", "ing.pst_archivo": "id_pst", "ing.pst_carga": "id_carga",
       "mail.pst_carpeta": "id_carpeta", "mail.direccion": "id_direccion", "mail.mensaje": "id_mensaje",
       "mail.mensaje_origen": "id_origen", "mail.destinatario": "id_destinatario", "mail.adjunto": "id_adjunto",
       "mail.reporte_destinatario": "id_reporte"}


def sql_date(value):
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return value.replace(tzinfo=None) if value else None


@contextmanager
def repository(seconds=600):
    repo = PstRepository(connect_sql(), time.monotonic()+seconds)
    try:
        yield repo
    finally:
        repo.close()


class PstRepository(SqlRepository):
    def lock(self, key):
        row = self.one("DECLARE @r int; EXEC @r=sys.sp_getapplock @Resource=?,@LockMode='Exclusive',"
                       "@LockOwner='Transaction',@LockTimeout=10000; SELECT @r AS r;", ("pst:"+str(key),))
        if row["r"] < 0:
            raise PstFailure("PST_CARGA_OCUPADA")

    def insert(self, table, values):
        identifier = IDS[table]  # Tabla e identificadores solo de constantes internas.
        fields = ",".join("["+key+"]" for key in values)
        return self.one(f"INSERT {table}({fields}) OUTPUT inserted.[{identifier}] VALUES({','.join('?' for _ in values)});",
                        tuple(values.values()))[identifier]

    def known(self, request):
        return self.one("SELECT c.id_carga,c.estado FROM ing.pst_archivo a JOIN ing.pst_carga c "
                        "ON c.id_contenido=a.id_contenido WHERE a.drive_id=? AND a.item_id=? AND a.version_origen=? "
                        "AND c.version_extractor=? AND c.version_modelo=?;",
                        (request.drive_id, request.item_id, request.version_origen, EXTRACTOR_VERSION, MODEL_VERSION))

    def register(self, request, metadata, staged):
        with self.transaction():
            self.lock(staged["sha256"])
            content = self.one("SELECT id_contenido,tamano_bytes FROM ing.pst_contenido WITH(UPDLOCK,HOLDLOCK) WHERE sha256=?;",
                               (bytes.fromhex(staged["sha256"]),))
            if content and content["tamano_bytes"] != staged["bytes"]:
                raise PstFailure("PST_HUELLA_CONFLICTIVA")
            content_id = content["id_contenido"] if content else self.insert("ing.pst_contenido", {
                "sha256": bytes.fromhex(staged["sha256"]), "tamano_bytes": staged["bytes"]})
            source = self.one("SELECT id_pst,id_contenido FROM ing.pst_archivo WITH(UPDLOCK,HOLDLOCK) "
                              "WHERE drive_id=? AND item_id=? AND version_origen=?;",
                              (request.drive_id, request.item_id, request.version_origen))
            if source and source["id_contenido"] != content_id:
                raise PstFailure("PST_VERSION_CONTENIDO_CONFLICTIVO")
            if not source:
                path = metadata["parentReference"].get("path", "")+"/"+metadata["name"]
                self.insert("ing.pst_archivo", {"id_contenido": content_id, "drive_id": request.drive_id,
                    "item_id": request.item_id, "version_origen": request.version_origen, "nombre": metadata["name"],
                    "ruta": path, "modificado_origen_utc": sql_date(metadata.get("lastModifiedDateTime"))})
            load = self.one("SELECT id_carga,estado FROM ing.pst_carga WITH(UPDLOCK,HOLDLOCK) WHERE "
                            "id_contenido=? AND version_extractor=? AND version_modelo=?;",
                            (content_id, EXTRACTOR_VERSION, MODEL_VERSION))
            if not load:
                load = {"id_carga": self.insert("ing.pst_carga", {"id_contenido": content_id,
                         "version_extractor": EXTRACTOR_VERSION, "version_modelo": MODEL_VERSION}), "estado": "RECIBIDA"}
            if load["estado"] != "COMPLETA":
                self.execute("UPDATE ing.pst_carga SET estado='PROCESANDO',intentos=intentos+1,"
                             "inicio_utc=COALESCE(inicio_utc,SYSUTCDATETIME()),fin_utc=NULL,latido_utc=SYSUTCDATETIME() "
                             "WHERE id_carga=?;", (load["id_carga"],))
            return load

    def inventory(self, load_id, folders):
        with self.transaction():
            self.lock(load_id)
            load = self.one("SELECT inventario_completo FROM ing.pst_carga WHERE id_carga=?;", (load_id,))
            if load["inventario_completo"]:
                return
            parents = {}
            for folder in folders:
                values = {"id_carga": load_id, "identificador_pst": folder["identificador"],
                          "id_carpeta_padre": parents.get(folder["padre"]), "nombre": folder["nombre"],
                          "elementos_reportados": folder["elementos"], "ruta_indices": json.dumps(folder["ruta"])}
                row = self.one("SELECT id_carpeta FROM mail.pst_carpeta WHERE id_carga=? AND identificador_pst=?;",
                               (load_id, folder["identificador"]))
                parents[folder["identificador"]] = row["id_carpeta"] if row else self.insert("mail.pst_carpeta", values)
            self.execute("UPDATE ing.pst_carga SET inventario_completo=1,elementos_reportados=?,"
                         "latido_utc=SYSUTCDATETIME() WHERE id_carga=?;", (sum(f["elementos"] for f in folders), load_id))

    def next_folder(self, load_id):
        return self.one("SELECT TOP(1) id_carpeta,ruta_indices,siguiente_indice,elementos_reportados "
                        "FROM mail.pst_carpeta WHERE id_carga=? AND siguiente_indice<elementos_reportados "
                        "ORDER BY id_carpeta;", (load_id,))

    def resume_failed(self, load_id):
        # UPDATE atomico: dos actividades reanudadas no incrementan dos veces.
        self.execute("UPDATE ing.pst_carga SET estado='PROCESANDO',intentos=intentos+1,fin_utc=NULL,"
                     "latido_utc=SYSUTCDATETIME() WHERE id_carga=? AND estado='ERROR';", (load_id,))

    def direction(self, value):
        if not value:
            return None
        key = (value["tipo"], value["direccion"])
        if key in self.cache:
            return self.cache[key]
        digest = hashlib.sha256(value["direccion"].encode("utf-16le")).digest()
        row = self.one("SELECT id_direccion,direccion FROM mail.direccion WITH(UPDLOCK,HOLDLOCK) "
                       "WHERE tipo=? AND hash_direccion=?;", (key[0], digest))
        if row and row["direccion"] != key[1]:
            raise PstFailure("PST_DIRECCION_HUELLA_CONFLICTIVA")
        identifier = row["id_direccion"] if row else self.insert("mail.direccion", value)
        self.cache[key] = identifier
        return identifier

    def message(self, load_id, folder_id, index, value):
        previous = self.one("SELECT id_origen,id_carpeta FROM mail.mensaje_origen WHERE id_carga=? AND identificador_pst=?;",
                            (load_id, value["identificador"]))
        if previous:
            if previous["id_carpeta"] != folder_id:
                raise PstFailure("PST_ELEMENTO_REPETIDO")
            return previous["id_origen"]
        # No deduplicar por Message-ID ni por metadatos sin verificar el contenido.
        message_id = self.insert("mail.mensaje", {"message_id": value["message_id"], "clase_mapi": value["clase"],
            "asunto": value["asunto"], "id_remitente": self.direction(value["remitente"]),
            "id_representado": self.direction(value["representado"]), "nombre_remitente": value["nombre_remitente"],
            "envio_utc": sql_date(value["envio"])})
        for recipient in value["destinatarios"]:
            self.insert("mail.destinatario", {"id_mensaje": message_id, "tipo": recipient["tipo"],
                "fuente": recipient["fuente"], "ordinal": recipient["ordinal"],
                "id_direccion": self.direction(recipient["direccion"]), "id_smtp_resuelto": self.direction(recipient["smtp"]),
                "nombre_mostrado": recipient["nombre"]})
        for attachment in value["adjuntos"]:
            self.insert("mail.adjunto", {"id_mensaje": message_id, "ordinal": attachment["ordinal"],
                "nombre": attachment["nombre"], "tamano_bytes": attachment["tamano"], "tipo_mime": attachment["mime"],
                "metodo_mapi": attachment["metodo"], "content_id": attachment["content_id"], "oculto": attachment["oculto"]})
        for ref in value["referencias"]:
            self.execute("INSERT mail.referencia_mensaje(id_mensaje,tipo,ordinal,valor_texto,valor_binario) VALUES(?,?,?,?,?);",
                         (message_id, ref["tipo"], ref["ordinal"], ref["texto"], ref["binario"]))
        ordinals = {}
        for report in value["reportes"]:
            kind = report["tipo"]
            ordinal = ordinals.get(kind, 0)
            ordinals[kind] = ordinal+1
            self.insert("mail.reporte_destinatario", dict(id_mensaje=message_id, ordinal=ordinal, **report))
        return self.insert("mail.mensaje_origen", {"id_carga": load_id, "id_carpeta": folder_id,
            "identificador_pst": value["identificador"], "id_mensaje": message_id, "indice_en_carpeta": index,
            "recepcion_buzon_utc": sql_date(value["recepcion"]), "flags_mapi": value["flags"],
            "tamano_mensaje_bytes": value["tamano"], "destinatarios_reportados": None,
            "adjuntos_reportados": value["adjuntos_reportados"],
            "destinatarios_completos": value["destinatarios_completos"], "adjuntos_completos": True})

    def batch(self, load_id, folder, start, entries):
        with self.transaction():
            self.lock(load_id)
            current = self.one("SELECT siguiente_indice FROM mail.pst_carpeta WITH(UPDLOCK,HOLDLOCK) WHERE id_carga=? AND id_carpeta=?;",
                               (load_id, folder["id_carpeta"]))
            # Actividades Durable pueden ejecutarse otra vez despues del COMMIT.
            if current["siguiente_indice"] != start:
                return self.progress(load_id)
            for offset, entry in enumerate(entries):
                value, origin = entry.get("mensaje"), None
                if value:
                    origin = self.message(load_id, folder["id_carpeta"], start+offset, value)
                self.execute("INSERT ing.pst_elemento(id_carga,id_carpeta,indice,identificador_pst,id_origen,estado,codigo) "
                             "VALUES(?,?,?,?,?,?,?);", (load_id, folder["id_carpeta"], start+offset,
                             value["identificador"] if value else entry.get("identificador"), origin, entry["estado"], entry.get("codigo")))
                codes = value["incidencias"] if value else [entry["codigo"]] if entry.get("codigo") else []
                for code in codes:
                    self.execute("INSERT ing.pst_incidencia(id_carga,id_origen,identificador_pst,codigo,severidad,detalle) "
                                 "VALUES(?,?,?,?,?,?);", (load_id, origin, value["identificador"] if value else entry.get("identificador"),
                                 code, "E" if entry["estado"] == "ERROR" else "W", "Revise el elemento en su PST de origen."))
            self.execute("UPDATE mail.pst_carpeta SET siguiente_indice=? WHERE id_carga=? AND id_carpeta=?;",
                         (start+len(entries), load_id, folder["id_carpeta"]))
            self.execute("UPDATE c SET elementos_examinados=x.total,mensajes_extraidos=x.extraidos,"
                         "elementos_omitidos=x.omitidos,elementos_fallidos=x.fallidos,latido_utc=SYSUTCDATETIME() "
                         "FROM ing.pst_carga c CROSS APPLY(SELECT COUNT_BIG(*) total,"
                         "COALESCE(SUM(CONVERT(bigint,CASE WHEN estado='EXTRAIDO' THEN 1 ELSE 0 END)),0) extraidos,"
                         "COALESCE(SUM(CONVERT(bigint,CASE WHEN estado='OMITIDO' THEN 1 ELSE 0 END)),0) omitidos,"
                         "COALESCE(SUM(CONVERT(bigint,CASE WHEN estado='ERROR' THEN 1 ELSE 0 END)),0) fallidos "
                         "FROM ing.pst_elemento WHERE id_carga=c.id_carga)x WHERE c.id_carga=?;", (load_id,))
            return self.progress(load_id)

    def progress(self, load_id):
        return self.one("SELECT id_carga,estado,elementos_reportados,elementos_examinados,mensajes_extraidos,"
                        "elementos_omitidos,elementos_fallidos FROM ing.pst_carga WHERE id_carga=?;", (load_id,))

    def finish(self, load_id):
        with self.transaction():
            self.lock(load_id)
            load = self.one("SELECT * FROM ing.pst_carga WITH(UPDLOCK,HOLDLOCK) WHERE id_carga=?;", (load_id,))
            if load["estado"] == "COMPLETA":
                return self.progress(load_id)
            if not load["inventario_completo"] or self.next_folder(load_id) or load["elementos_examinados"] != load["elementos_reportados"]:
                raise PstFailure("PST_CARGA_INCOMPLETA")
            check = self.one("SELECT COUNT_BIG(*) AS total FROM mail.mensaje_origen WHERE id_carga=?;", (load_id,))
            if check["total"] != load["mensajes_extraidos"]:
                raise PstFailure("PST_CONTEO_NO_COINCIDE")
            check = self.one("SELECT COUNT_BIG(*) AS errores FROM mail.mensaje_origen o WHERE o.id_carga=? "
                             "AND (o.adjuntos_completos=0 OR o.adjuntos_reportados IS NULL OR o.adjuntos_reportados<>"
                             "(SELECT COUNT_BIG(*) FROM mail.adjunto a WHERE a.id_mensaje=o.id_mensaje));", (load_id,))
            if check["errores"]:
                raise PstFailure("PST_ADJUNTOS_NO_COINCIDEN")
            state = "CON_ERRORES" if load["elementos_fallidos"] else "COMPLETA"
            self.execute("UPDATE ing.pst_carga SET estado=?,fin_utc=SYSUTCDATETIME(),latido_utc=SYSUTCDATETIME() WHERE id_carga=?;", (state, load_id))
            if state == "COMPLETA":
                self.execute("UPDATE ing.pst_incidencia SET resuelta=1 WHERE id_carga=? "
                             "AND codigo='PST_ACTIVIDAD_FALLIDA' AND id_origen IS NULL;", (load_id,))
                # No desplazar una carga registrada posteriormente y ya publicada.
                self.execute("UPDATE ing.pst_contenido SET id_carga_vigente=? WHERE id_contenido=? "
                             "AND (id_carga_vigente IS NULL OR id_carga_vigente<=?);", (load_id, load["id_contenido"], load_id))
            return self.progress(load_id)

    def fail(self, load_id, code):
        with self.transaction():
            self.lock(load_id)
            current = self.one("SELECT estado FROM ing.pst_carga WITH(UPDLOCK,HOLDLOCK) WHERE id_carga=?;", (load_id,))
            if current and current["estado"] == "COMPLETA":
                return
            self.execute("UPDATE ing.pst_carga SET estado='ERROR',fin_utc=SYSUTCDATETIME(),latido_utc=SYSUTCDATETIME() "
                         "WHERE id_carga=? AND estado<>'COMPLETA';", (load_id,))
            self.execute("IF NOT EXISTS(SELECT 1 FROM ing.pst_incidencia WHERE id_carga=? AND codigo=? AND id_origen IS NULL) "
                         "INSERT ing.pst_incidencia(id_carga,codigo,severidad,detalle) VALUES(?,?,'E',N'Fallo de actividad; reanude con el identificador de seguimiento.');",
                         (load_id, code, load_id, code))
