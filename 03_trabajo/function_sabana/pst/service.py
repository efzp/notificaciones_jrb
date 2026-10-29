from __future__ import annotations

import json
import os
import time

from .cloud import Graph, identity, open_pst, stage_file, staging
from .domain import PstFailure, PstRequest, integer_setting
from .extract import extract_message, folder_at, inventory
from .repository import repository


def request_from(value):
    return PstRequest.parse(value, os.environ.get("PST_DRIVE_ID", ""))


def prepare(document):
    request = request_from(document)
    with identity() as credential:
        graph = Graph(credential)
        try:
            metadata = graph.metadata(request)
            with repository() as repo:
                known = repo.known(request)
                if known and known["estado"] == "COMPLETA":
                    return {"id_carga": known["id_carga"], "completa": True}
            # No mantener la conexion SQL abierta durante la copia de varios GB.
            with staging(credential) as container:
                reference = stage_file(container, graph, request, metadata)
            with repository() as repo:
                load = repo.register(request, metadata, reference)
            return dict(reference, id_carga=load["id_carga"], completa=load["estado"] == "COMPLETA")
        finally:
            graph.close()


def build_inventory(reference):
    with repository() as repo:
        row = repo.one("SELECT inventario_completo FROM ing.pst_carga WHERE id_carga=?;", (reference["id_carga"],))
        if row["inventario_completo"]:
            return {"id_carga": reference["id_carga"]}
    with identity() as credential, staging(credential) as container, open_pst(container, reference) as file:
        folders = inventory(file)
    with repository() as repo:
        repo.inventory(reference["id_carga"], folders)
    return {"id_carga": reference["id_carga"]}


def process_batch(reference):
    with repository() as repo:
        repo.resume_failed(reference["id_carga"])
        folder = repo.next_folder(reference["id_carga"])
    if not folder:
        return {"terminado": True}
    start = folder["siguiente_indice"]
    count = integer_setting("PST_BATCH_MESSAGES", 50, 1, 500)
    deadline = time.monotonic()+integer_setting("PST_BATCH_SECONDS", 180, 10, 600)
    entries = []
    with identity() as credential, staging(credential) as container, open_pst(container, reference) as file:
        native_folder = folder_at(file, json.loads(folder["ruta_indices"]))
        for index in range(start, min(start+count, folder["elementos_reportados"])):
            if entries and time.monotonic() >= deadline:
                break
            identifier = None
            try:
                message = native_folder.get_sub_message(index)
                identifier = int(message.identifier)
                value = extract_message(message)
                entries.append({"mensaje": value, "identificador": identifier,
                                "estado": "EXTRAIDO" if value else "OMITIDO"})
            except PstFailure as error:
                if error.code == "PST_RANGO_NO_DISPONIBLE":
                    raise  # Fallo de infraestructura: no declarar corrupto el elemento.
                entries.append({"estado": "ERROR", "identificador": identifier, "codigo": error.code})
            except (OSError, ValueError, UnicodeError, AttributeError, OverflowError):
                entries.append({"estado": "ERROR", "identificador": identifier, "codigo": "PST_LECTURA_ELEMENTO"})
    if not entries:
        raise PstFailure("PST_LOTE_SIN_AVANCE")
    with repository() as repo:
        progress = repo.batch(reference["id_carga"], folder, start, entries)
    return dict(progress, terminado=False)


def finish(reference):
    with repository() as repo:
        return repo.finish(reference["id_carga"])


def fail(reference):
    with repository() as repo:
        repo.fail(reference["id_carga"], "PST_ACTIVIDAD_FALLIDA")
    return {"id_carga": reference["id_carga"], "estado": "ERROR"}
