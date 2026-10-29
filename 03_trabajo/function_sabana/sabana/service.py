from __future__ import annotations

import logging
import time

from .database import SqlRepository, connect_sql, retryable_sql, sql_number
from .domain import AppError, Issue, ParsedWorkbook, Payload
from .excel import parse_workbook

log = logging.getLogger(__name__)


def consolidate(payload: Payload, *, connection_factory=connect_sql, max_seconds=100, max_rows=2000):
    deadline = time.monotonic() + max_seconds
    parsed = previous_profile = None
    for attempt in range(3):
        repository = None
        archive_id = load_id = None
        try:
            repository = SqlRepository(connection_factory(), deadline)
            profile = repository.profile(payload.id_perfil)
            if parsed is None or profile != previous_profile:
                try:
                    parsed = parse_workbook(payload.content, profile, max_rows=max_rows)
                except AppError as error:
                    if error.status != 422:
                        raise
                    parsed = ParsedWorkbook("", [], [Issue(error.code, "E", error.message)], {}, 0)
                previous_profile = profile
            archive_id, load_id, _ = repository.register(payload, parsed)
            if parsed.blocked:
                repository.record_failure(payload, load_id, parsed.issues)
                return {
                    "estado": "RECHAZADA", "codigo": "VALIDACION_EXCEL", "id_archivo": archive_id,
                    "id_carga": load_id, "filas_detectadas": parsed.total_rows, "filas_procesadas": 0,
                    "errores": sum(i.severidad == "E" for i in parsed.issues), "advertencias": parsed.warnings,
                    "incidencias": [i.public() for i in parsed.issues[:25]],
                    "total_incidencias": len(parsed.issues),
                }, 422
            result = repository.ingest(payload, load_id, parsed)
            return {
                "estado": "PUBLICADA", "id_archivo": archive_id, "id_carga": load_id,
                "filas_procesadas": parsed.total_rows, "sha256": payload.content_hash.hex(),
                "incidencias": [i.public() for i in parsed.issues[:25]],
                "total_incidencias": len(parsed.issues), **result,
            }, 200
        except Exception as error:
            if not isinstance(error, AppError) and retryable_sql(error) and attempt < 2 and time.monotonic() < deadline - 10:
                log.warning("Reintento SQL intento=%s numero=%s", attempt + 1, sql_number(error))
                time.sleep(2 ** attempt)
                continue
            if repository and load_id is not None:
                try:
                    # Un tiempo agotado no impide intentar el registro breve del fallo.
                    repository.deadline = time.monotonic() + 10
                    repository.record_failure(payload, load_id)
                except Exception:
                    log.warning("No se pudo registrar el fallo de carga id_carga=%s", load_id)
            if isinstance(error, AppError):
                raise
            number = sql_number(error)
            log.error("Fallo de carga tipo=%s numero_sql=%s id_carga=%s", type(error).__name__, number, load_id)
            if number in {51207, 51208}:
                raise AppError("VERSION_ORIGEN_CONFLICTIVA", "Evento atrasado o version de origen conflictiva; revise la carga vigente.", 409) from None
            if number and 51100 <= number <= 51399:
                raise AppError("VALIDACION_SQL", "SQL rechazo la carga; revise las incidencias y el perfil.", 422) from None
            raise AppError("SQL_NO_DISPONIBLE", "No fue posible completar la carga; revise conexion, permisos o reintente.", 503) from None
        finally:
            if repository:
                try:
                    repository.close()
                except Exception:
                    log.warning("No se pudo cerrar una conexion SQL.")
    raise AppError("SQL_NO_DISPONIBLE", "No fue posible completar la carga.", 503)
