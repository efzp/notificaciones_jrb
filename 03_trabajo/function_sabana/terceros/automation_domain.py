"""Contrato de avisos; solo metadatos en el historial Durable."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass

from .domain import digest

JOB_PATTERN = re.compile(r"terceros-[a-f0-9]{64}\Z")


class AutomationError(ValueError):
    def __init__(self, code, status=400):
        super().__init__(code)
        self.code = code
        self.status = status


def setting(name, default, minimum, maximum):
    try:
        result = int(os.environ.get(name, str(default)))
    except (ValueError, TypeError):
        raise AutomationError("TERCEROS_CONFIGURACION_INVALIDA", 503) from None
    if not minimum <= result <= maximum:
        raise AutomationError("TERCEROS_CONFIGURACION_INVALIDA", 503)
    return result


def enabled():
    return os.environ.get("TERCEROS_AUTOMATION_ENABLED", "0") == "1"


@dataclass(frozen=True)
class Notice:
    origen: str
    tipo_fuente: str
    id_carga_origen: str
    hash_revision: str

    @classmethod
    def parse(cls, value):
        fields = {"origen", "tipo_fuente", "id_carga_origen", "hash_revision"}
        if not isinstance(value, dict) or set(value) != fields:
            raise AutomationError("TERCEROS_AVISO_INVALIDO")
        if any(not isinstance(value[k], str) for k in fields):
            raise AutomationError("TERCEROS_AVISO_INVALIDO")
        origin, kind = value["origen"].upper(), value["tipo_fuente"].upper()
        if (origin, kind) not in {("JNC", "LOTE"), ("JRB", "EXCEL"), ("JRB", "PST")}:
            raise AutomationError("TERCEROS_ORIGEN_INVALIDO")
        # Identificador tecnico, no nombres de pacientes, correos o rutas.
        load = value["id_carga_origen"]
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", load):
            raise AutomationError("TERCEROS_CARGA_ORIGEN_INVALIDA")
        revision = value["hash_revision"].lower()
        if not re.fullmatch(r"[a-f0-9]{64}", revision):
            raise AutomationError("TERCEROS_REVISION_INVALIDA")
        return cls(origin, kind, load, revision)

    def document(self):
        return dict(origen=self.origen, tipo_fuente=self.tipo_fuente,
                    id_carga_origen=self.id_carga_origen, hash_revision=self.hash_revision)

    @property
    def job_id(self):
        return "terceros-" + digest(self.document())


def execution(value):
    from uuid import UUID
    try:
        token = str(UUID(value["id_ejecucion"]))
        count = value["solicitudes"]
        if type(count) is not int or not 1 <= count <= 1000:
            raise ValueError()
        return token, count
    except (KeyError, ValueError, TypeError, AttributeError):
        raise AutomationError("TERCEROS_RESERVA_INVALIDA") from None
