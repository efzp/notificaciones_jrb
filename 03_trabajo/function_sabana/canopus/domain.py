from __future__ import annotations

import base64
import hashlib
import os
import re
from datetime import datetime, timezone

from terceros.domain import digest

VERSION = "canopus-v1"
DRIVE = "b!BVdcW1M1CUGvabWi-TiRu48Z8tWTR9xJhVeDlno8JlrPYQEct01cSIycOPVx2lWL"
JOB = re.compile(r"canopus-[a-f0-9]{64}")
FIELDS = {"drive_id", "item_id", "nombre_archivo", "version_origen", "fecha_modificacion_origen"}


class CanopusError(ValueError):
    def __init__(self, code, status=400):
        self.code, self.status = code, status
        super().__init__(code)


def enabled():
    return os.getenv("CANOPUS_INGEST_ENABLED", "0") == "1"


def limit(name, default, maximum):
    value = int(os.getenv(name, str(default)))
    if not 1 <= value <= maximum:
        raise CanopusError("CANOPUS_CONFIGURACION_INVALIDA", 503)
    return value


def max_bytes():
    return limit("CANOPUS_MAX_BYTES", 10 * 1024 * 1024, 20 * 1024 * 1024)


def metadata(document):
    if not isinstance(document, dict) or set(document) != FIELDS:
        raise CanopusError("CANOPUS_METADATOS_INVALIDOS")
    for field, size in (("drive_id", 128), ("item_id", 128), ("nombre_archivo", 255), ("version_origen", 128)):
        value = document[field]
        if (not isinstance(value, str) or not value or value != value.strip() or len(value) > size
                or any(ord(c) < 32 for c in value)):
            raise CanopusError("CANOPUS_METADATOS_INVALIDOS")
    if document["drive_id"] != os.getenv("CANOPUS_DRIVE_ID", DRIVE):
        raise CanopusError("CANOPUS_DRIVE_NO_PERMITIDO")
    if not re.fullmatch(r"[A-Za-z0-9!_-]+", document["item_id"]):
        raise CanopusError("CANOPUS_ITEM_INVALIDO")
    name = document["nombre_archivo"]
    if not name.lower().endswith(".xlsx") or any(c in name for c in '/\\:*?"<>|'):
        raise CanopusError("CANOPUS_ARCHIVO_NO_PERMITIDO")
    try:
        modified = datetime.fromisoformat(document["fecha_modificacion_origen"].replace("Z", "+00:00"))
        if modified.tzinfo is None:
            raise ValueError()
        modified = modified.astimezone(timezone.utc)
        # SQL datetime2(3): normalizar antes de comparar y calcular el identificador.
        modified = modified.replace(microsecond=(modified.microsecond // 1000) * 1000)
    except (ValueError, TypeError, AttributeError, OverflowError):
        raise CanopusError("CANOPUS_FECHA_ORIGEN_INVALIDA") from None
    return dict(document, fecha_modificacion_origen=modified.isoformat(timespec="milliseconds"))


def intake(document):
    if not isinstance(document, dict) or set(document) != FIELDS | {"contenido_base64"}:
        raise CanopusError("CANOPUS_SOLICITUD_INVALIDA")
    info = metadata({k: document[k] for k in FIELDS})
    encoded = document["contenido_base64"]
    maximum = max_bytes()
    if not isinstance(encoded, str) or len(encoded) > 4 * ((maximum + 2) // 3):
        raise CanopusError("CANOPUS_ARCHIVO_GRANDE", 413)
    try:
        content = base64.b64decode(encoded, validate=True)
    except (ValueError, UnicodeError):
        raise CanopusError("CANOPUS_BASE64_INVALIDO") from None
    if not content or len(content) > maximum:
        raise CanopusError("CANOPUS_TAMANO_INVALIDO", 413)
    info.update(hash_contenido=hashlib.sha256(content).hexdigest(), version_parser=VERSION, bytes=len(content))
    info["id_seguimiento"] = "canopus-" + digest(info)
    return info, content
