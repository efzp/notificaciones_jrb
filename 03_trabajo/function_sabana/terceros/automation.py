"""Actividad de actualizacion. La instantanea completa solo vive en memoria."""
from __future__ import annotations

import logging
import threading

from . import control, repository
from .automation_domain import AutomationError, setting
from .capture import capture


class Lease:
    def __init__(self, reservation):
        self.reservation = reservation
        self.stop = threading.Event()
        self.lost = threading.Event()
        seconds = setting("TERCEROS_LEASE_SECONDS", 1200, 120, 3600)
        self.interval = min(60, seconds // 3)
        self.thread = threading.Thread(target=self.run, name="terceros-reserva", daemon=True)

    def run(self):
        while not self.stop.wait(self.interval):
            try:
                control.heartbeat(self.reservation)
            except Exception:
                self.lost.set()
                break

    def check(self, phase=None):
        if self.lost.is_set():
            raise AutomationError("TERCEROS_RESERVA_PERDIDA", 409)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.stop.set()
        self.thread.join(timeout=30)
        if not args[0]:
            if self.thread.is_alive():
                raise AutomationError("TERCEROS_RESERVA_PERDIDA", 409)
            self.check()


def process(value):
    request_id = value.get("id_actualizacion")
    if request_id is not None:
        if type(request_id) is not int or not 1 <= request_id <= 9223372036854775807:
            raise AutomationError("TERCEROS_SOLICITUD_INVALIDA")
        current = control.state(request_id)
        if current["estado"] in {"COMPLETADA", "AGOTADA"}:
            return current
    reservation = control.claim()
    if reservation["estado"] != "RESERVADA":
        # claim puede haber agotado el ultimo intento de una reserva expirada.
        if request_id is not None:
            current = control.state(request_id)
            if current["estado"] in {"COMPLETADA", "AGOTADA"}:
                return current
        return reservation
    try:
        with Lease(reservation) as lease:
            document = capture(progress=lease.check)
        # Renovar antes de entrar a la transaccion de publicacion. Esa transaccion
        # bloquea las solicitudes y verifica nuevamente el vencimiento al finalizar.
        control.heartbeat(reservation)
        result = repository.apply(document, reservation=reservation)
        if request_id is not None:
            current = control.state(request_id)
            if current["estado"] in {"COMPLETADA", "AGOTADA"}:
                return current
        return {"estado": "PROCESADA", "id_carga_terceros": result["id_carga_terceros"],
                "solicitudes": reservation["solicitudes"], "reutilizada": result["reutilizada"]}
    except Exception as error:
        logging.error("Terceros actualizacion fallo tipo=%s", type(error).__name__)
        code = error.code if isinstance(error, AutomationError) else "TERCEROS_ACTUALIZACION_FALLIDA"
        control.fail(reservation, code)
        if request_id is not None:
            current = control.state(request_id)
            if current["estado"] in {"COMPLETADA", "AGOTADA"}:
                return current
        return {"estado": "ESPERANDO", "espera_segundos": 60}
