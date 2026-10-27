from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
import unicodedata
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from typing import Any

PARSER_VERSION = "sabana-fechas/1.0"
MODEL_VERSION = "1.0.0"
PARTS = ("paciente", "arl", "afp", "eps", "aseguradora", "ultimoempleador")
PART_NAMES = dict(zip(PARTS, ("nombre_paciente", "arl", "afp", "eps", "aseguradora", "ultimo_empleador")))
NOTIFICATION_FIELDS = {
    "documento_notificacion": "documento_notificacion",
    "estado": "estado_reportado",
    "fecha_notificacion": "fecha_notificacion_reportada",
    "fecha_envio": "fecha_envio_reportada",
    "guia": "guia_reportada",
    "fecha_recibido": "fecha_recibido_reportada",
}


class AppError(Exception):
    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


def normalize_header(value: Any) -> str:
    value = unicodedata.normalize("NFKD", str(value or "").strip().casefold())
    value = "".join(c for c in value if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "_", value).strip("_")


def blank(value: Any) -> bool:
    return value is None or isinstance(value, str) and not value.strip()


def no_aplica(value: Any) -> bool:
    return isinstance(value, str) and normalize_header(value) in {"no_aplica", "noaplica", "n_a", "na"}


def sql_length(value: str) -> int:
    """Las longitudes nvarchar de SQL se expresan en unidades UTF-16."""
    return len(value.encode("utf-16le")) // 2


def source_text(value: Any) -> str:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return str(value)


def fact_hash(values: dict[str, Any]) -> bytes:
    payload = json.dumps(values, sort_keys=True, ensure_ascii=False, separators=(",", ":"),
                         allow_nan=False, default=lambda v: v.isoformat()).encode("utf-8")
    return hashlib.sha256(payload).digest()


@dataclass(frozen=True)
class FieldSpec:
    campo: str
    orden: int
    tipo_dato: str
    obligatorio: bool
    longitud_maxima: int | None = None
    tipo_parte: str | None = None
    bit_no_aplica: int | None = None


@dataclass(frozen=True)
class Profile:
    id_perfil: int
    fecha_minima: date
    fecha_maxima: date
    fields: tuple[FieldSpec, ...]
    aliases: dict[str, str]
    version_parser: str = PARSER_VERSION
    version_modelo: str = MODEL_VERSION

    def validate(self) -> None:
        if (self.version_parser, self.version_modelo) != (PARSER_VERSION, MODEL_VERSION):
            raise AppError("PERFIL_NO_SOPORTADO", "El perfil requiere otra version del parser.", 503)
        if (len(self.fields) != 50 or [f.orden for f in self.fields] != list(range(1, 51))
                or len({f.campo for f in self.fields}) != 50):
            raise AppError("PERFIL_INVALIDO", "El perfil debe definir 50 campos distintos y ordenados.", 503)
        if any(f.tipo_dato not in {"TEXTO", "FECHA", "IDENTIFICADOR"} for f in self.fields):
            raise AppError("PERFIL_INVALIDO", "Tipo de dato no soportado.", 503)
        common = {"fecha_audiencia", "tipo_de_dictamen", "reparto_paciente", "identificacion_paciente",
                  "nombre_paciente", "medico_ponente", "fecha_fijacion", "fecha_desfijacion", "entidad_remitente"}
        expected = common | set(PART_NAMES.values()) | {
            f"{prefix}_{part}" for part in PARTS for prefix in NOTIFICATION_FIELDS
        }
        if {f.campo for f in self.fields} != expected:
            raise AppError("PERFIL_INVALIDO", "Los campos no corresponden a la version del modelo.", 503)
        for spec in self.fields:
            expected_type = "FECHA" if spec.campo.startswith("fecha_") else (
                "IDENTIFICADOR" if spec.campo in {"reparto_paciente", "identificacion_paciente"} else "TEXTO")
            if spec.tipo_dato != expected_type:
                raise AppError("PERFIL_INVALIDO", "El tipo de un campo contradice el modelo SQL.", 503)
            if spec.campo in {"fecha_audiencia", "reparto_paciente", "identificacion_paciente"} and not spec.obligatorio:
                raise AppError("PERFIL_INVALIDO", "Las llaves del caso deben ser obligatorias.", 503)
        if self.fecha_minima > self.fecha_maxima:
            raise AppError("PERFIL_INVALIDO", "Rango de fechas incorrecto.", 503)

    @classmethod
    def from_json(cls, document: dict[str, Any]) -> Profile:
        profile = cls(
            id_perfil=document["id_perfil"],
            fecha_minima=date.fromisoformat(document["fecha_minima"]),
            fecha_maxima=date.fromisoformat(document["fecha_maxima"]),
            fields=tuple(FieldSpec(**f) for f in document["fields"]),
            aliases=document["aliases"],
            version_parser=document.get("version_parser", PARSER_VERSION),
            version_modelo=document.get("version_modelo", MODEL_VERSION),
        )
        profile.validate()
        return profile


@dataclass(frozen=True)
class Payload:
    drive_id: str
    item_id: str
    nombre_archivo: str
    ruta_sharepoint: str
    version_origen: str
    fecha_modificacion_origen: str
    sala: int
    id_perfil: int
    content: bytes = field(repr=False)

    @property
    def content_hash(self) -> bytes:
        return hashlib.sha256(self.content).digest()

    @classmethod
    def from_json(cls, data: Any, max_bytes: int = 10 * 1024 * 1024) -> Payload:
        if not isinstance(data, dict):
            raise AppError("JSON_INVALIDO", "El cuerpo debe ser un objeto JSON.")
        limits = {"drive_id": 128, "item_id": 128, "nombre_archivo": 255,
                  "ruta_sharepoint": 2048, "version_origen": 128}
        texts = {}
        for name, maximum in limits.items():
            value = data.get(name)
            if not isinstance(value, str):
                raise AppError("METADATO_INVALIDO", f"Revise el campo {name}.")
            try:
                length = sql_length(value)
            except UnicodeError:
                raise AppError("METADATO_INVALIDO", f"Revise la codificacion de {name}.") from None
            if (not value.strip() or value != value.strip() or length > maximum or "\x00" in value):
                raise AppError("METADATO_INVALIDO", f"Revise el campo {name}.")
            texts[name] = value
        if not texts["nombre_archivo"].lower().endswith((".xlsx", ".xlsm")):
            raise AppError("FORMATO_NO_SOPORTADO", "Solo se admiten archivos .xlsx o .xlsm.")
        if texts["nombre_archivo"].startswith("~$"):
            raise AppError("ARCHIVO_TEMPORAL", "No se procesan archivos temporales de Excel.")
        for name, minimum, maximum in (("sala", 1, 99), ("id_perfil", 1, 32767)):
            if type(data.get(name)) is not int or not minimum <= data[name] <= maximum:
                raise AppError("METADATO_INVALIDO", f"{name} debe ser un entero entre {minimum} y {maximum}.")
        timestamp = data.get("fecha_modificacion_origen")
        match = re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.(\d{1,7}))?(Z|[+-]\d{2}:\d{2})", str(timestamp))
        if not match:
            raise AppError("FECHA_ORIGEN_INVALIDA", "Use una fecha ISO con zona horaria y hasta 7 decimales.")
        try:
            utc = datetime.fromisoformat(timestamp.replace("Z", "+00:00")).astimezone(timezone.utc)
        except (ValueError, OverflowError):
            raise AppError("FECHA_ORIGEN_INVALIDA", "Fecha de modificacion de origen incorrecta.") from None
        # Preservar el septimo decimal que admite datetime2(7).
        fraction = (match.group(1) or "").ljust(7, "0")
        normalized = utc.strftime("%Y-%m-%dT%H:%M:%S") + "." + fraction
        encoded = data.get("contenido_base64")
        if not isinstance(encoded, str) or not encoded:
            raise AppError("BASE64_INVALIDO", "contenido_base64 debe contener el archivo.")
        if len(encoded) > 4 * ((max_bytes + 2) // 3):
            raise AppError("ARCHIVO_GRANDE", "El archivo supera el limite configurado.", 413)
        try:
            content = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error):
            raise AppError("BASE64_INVALIDO", "El contenido no es base64 valido.") from None
        if not content or len(content) > max_bytes:
            raise AppError("ARCHIVO_GRANDE", "El archivo esta vacio o supera el limite.", 413)
        return cls(**texts, fecha_modificacion_origen=normalized, sala=data["sala"],
                   id_perfil=data["id_perfil"], content=content)


@dataclass(frozen=True)
class Issue:
    codigo: str
    severidad: str
    detalle: str
    fila: int | None = None
    campo: str | None = None

    def public(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Original:
    campo: str
    valor_original: str
    motivo: str
    parser_aplicado: str | None
    mostrar_en_consolidado: bool


@dataclass
class ParsedRow:
    number: int
    values: dict[str, Any]
    masks: dict[str, int]
    originals: list[Original]


@dataclass
class ParsedWorkbook:
    sheet: str
    rows: list[ParsedRow]
    issues: list[Issue]
    structure: dict[str, Any]
    total_rows: int

    @property
    def blocked(self) -> bool:
        return any(i.severidad == "E" for i in self.issues)

    @property
    def warnings(self) -> int:
        return sum(i.severidad == "W" for i in self.issues)
