from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import asdict, dataclass

from sabana.domain import AppError

EXTRACTOR_VERSION = "pypff-20260926-v1"
MODEL_VERSION = "pst-async-v1"
JOB_PATTERN = re.compile(r"pst-[0-9a-f]{64}\Z")


@dataclass(frozen=True)
class PstRequest:
    drive_id: str
    item_id: str
    version_origen: str

    @classmethod
    def parse(cls, value: dict, allowed_drive: str) -> "PstRequest":
        if not allowed_drive:
            raise AppError("PST_NO_CONFIGURADO", "Configure PST_DRIVE_ID.", 503)
        if not isinstance(value, dict) or set(value) != {"drive_id", "item_id", "version_origen"}:
            raise AppError("PST_SOLICITUD_INVALIDA", "Envie solo drive_id, item_id y version_origen.")
        for name, content in value.items():
            if not isinstance(content, str) or not content or len(content) > 128 or any(ord(c) < 32 for c in content):
                raise AppError("PST_SOLICITUD_INVALIDA", f"Campo invalido: {name}.")
        if value["drive_id"] != allowed_drive:
            raise AppError("PST_ORIGEN_NO_PERMITIDO", "Biblioteca no autorizada.", 403)
        if any(c in value["item_id"] for c in "/\\?#"):
            raise AppError("PST_SOLICITUD_INVALIDA", "item_id invalido.")
        return cls(**value)

    def document(self) -> dict:
        return asdict(self)

    @property
    def job_id(self) -> str:
        value = dict(self.document(), extractor=EXTRACTOR_VERSION, modelo=MODEL_VERSION)
        return "pst-" + hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def integer_setting(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except ValueError:
        raise AppError("PST_CONFIGURACION", f"Valor invalido: {name}.", 503) from None
    if not minimum <= value <= maximum:
        raise AppError("PST_CONFIGURACION", f"Valor fuera de rango: {name}.", 503)
    return value


class PstFailure(Exception):
    """Solo codigos seguros: nunca texto del lector, SQL, URLs o contenido de correos."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)
