"""Avisos HTTP, agrupacion mediante temporizadores Durable y recuperacion diaria."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
from datetime import datetime, timedelta, timezone

import azure.functions as func
import azure.durable_functions as df

from . import automation, control
from .automation_domain import AutomationError, JOB_PATTERN, Notice, enabled

bp = df.Blueprint()


def response(document, status=200, location=None):
    headers = {"Cache-Control": "no-store"}
    if location:
        headers.update(Location=location, **{"Retry-After": "30"})
    return func.HttpResponse(json.dumps(document, ensure_ascii=True), status_code=status,
                             mimetype="application/json", headers=headers)


async def launch(client, job_id, document):
    current = await asyncio.wait_for(client.get_status(job_id), timeout=15)
    if not current:
        await asyncio.wait_for(client.start_new("OrquestarActualizacionTerceros", instance_id=job_id,
                                               client_input=document), timeout=15)
        return "ACEPTADA"
    return current.runtime_status.name


@bp.function_name(name="AvisarActualizacionTerceros")
@bp.route(route="terceros/actualizaciones", methods=["POST"], auth_level=func.AuthLevel.FUNCTION)
@bp.durable_client_input(client_name="client")
async def start(req: func.HttpRequest, client):
    if not enabled():
        return response({"codigo": "TERCEROS_AUTOMATIZACION_DESHABILITADA"}, 503)
    try:
        if len(req.get_body()) > 2048:
            return response({"codigo": "TERCEROS_SOLICITUD_GRANDE"}, 413)
        try:
            notice = Notice.parse(req.get_json())
        except ValueError as error:
            if isinstance(error, AutomationError):
                raise
            raise AutomationError("JSON_INVALIDO") from None
        state = await launch(client, notice.job_id, {"aviso": notice.document()})
        location = req.url.split("?", 1)[0].rstrip("/") + "/" + notice.job_id
        return response({"id_seguimiento": notice.job_id, "estado": state,
                         "consulta_estado": location}, 202, location)
    except AutomationError as error:
        return response({"codigo": error.code}, error.status)
    except Exception as error:
        logging.error("Terceros aviso fallo tipo=%s", type(error).__name__)
        return response({"codigo": "TERCEROS_INICIO_NO_DISPONIBLE"}, 503)


@bp.function_name(name="EstadoActualizacionTerceros")
@bp.route(route="terceros/actualizaciones/{job_id}", methods=["GET"], auth_level=func.AuthLevel.FUNCTION)
@bp.durable_client_input(client_name="client")
async def status(req: func.HttpRequest, client):
    job_id = req.route_params.get("job_id", "")
    if not JOB_PATTERN.fullmatch(job_id):
        return response({"codigo": "TERCEROS_ID_INVALIDO"}, 400)
    try:
        current = await asyncio.wait_for(client.get_status(job_id), timeout=15)
        if not current:
            return response({"codigo": "TERCEROS_NO_ENCONTRADO"}, 404)
        state = current.runtime_status.name
        value = {"id_seguimiento": job_id, "estado": state}
        if state == "Completed":
            value["resultado"] = current.output
        elif state in {"Failed", "Terminated"}:
            value["codigo"] = "TERCEROS_ORQUESTACION_FALLIDA"
        return response(value)
    except Exception as error:
        logging.error("Terceros estado fallo tipo=%s", type(error).__name__)
        return response({"codigo": "TERCEROS_ESTADO_NO_DISPONIBLE"}, 503)


@bp.function_name(name="ReanudarActualizacionTerceros")
@bp.route(route="terceros/actualizaciones/{job_id}/reanudar", methods=["POST"], auth_level=func.AuthLevel.FUNCTION)
@bp.durable_client_input(client_name="client")
async def resume(req: func.HttpRequest, client):
    if not enabled():
        return response({"codigo": "TERCEROS_AUTOMATIZACION_DESHABILITADA"}, 503)
    job_id = req.route_params.get("job_id", "")
    if not JOB_PATTERN.fullmatch(job_id):
        return response({"codigo": "TERCEROS_ID_INVALIDO"}, 400)
    try:
        current = await asyncio.wait_for(client.get_status(job_id), timeout=15)
        if not current:
            return response({"codigo": "TERCEROS_NO_ENCONTRADO"}, 404)
        if current.runtime_status.name not in {"Failed", "Terminated"}:
            return response({"codigo": "TERCEROS_NO_REANUDABLE"}, 409)
        await asyncio.wait_for(client.restart(job_id, restart_with_new_instance_id=False), timeout=15)
        return response({"id_seguimiento": job_id, "estado": "REANUDADA"}, 202)
    except Exception as error:
        logging.error("Terceros reanudar fallo tipo=%s", type(error).__name__)
        return response({"codigo": "TERCEROS_REANUDACION_NO_DISPONIBLE"}, 503)


@bp.function_name(name="RecuperarActualizacionesTerceros")
@bp.timer_trigger(arg_name="timer", schedule=os.environ.get("TERCEROS_RECOVERY_SCHEDULE", "0 0 8 * * *"),
                  run_on_startup=False, use_monitor=True)
@bp.durable_client_input(client_name="client")
async def recovery(timer: func.TimerRequest, client):
    if not enabled():
        return
    # La cola SQL conserva trabajos aunque el historial Durable ya no exista.
    job_id = "terceros-" + hashlib.sha256(
        ("recuperar:" + datetime.now(timezone.utc).strftime("%Y%m%d%H%M")).encode()).hexdigest()
    await launch(client, job_id, {"recuperar": True})


@bp.function_name(name="OrquestarActualizacionTerceros")
@bp.orchestration_trigger(context_name="context")
def orchestrate(context):
    return (yield from run_orchestration(context))


def run_orchestration(context):
    value = context.get_input()
    retry = df.RetryOptions(10000, 6)
    request_id = value.get("id_actualizacion")
    if request_id is None and not value.get("recuperar"):
        registered = yield context.call_activity_with_retry("TercerosRegistrarAviso", retry, value["aviso"])
        request_id = registered["id_actualizacion"]
    for _ in range(50):
        result = yield context.call_activity_with_retry("TercerosProcesarActualizacion", retry,
                                                      {"id_actualizacion": request_id})
        if result["estado"] in {"COMPLETADA", "AGOTADA", "SIN_PENDIENTES"}:
            return result
        context.set_custom_status({"fase": result["estado"], "id_actualizacion": request_id})
        if result["estado"] == "ESPERANDO":
            delay = max(5, min(3600, int(result["espera_segundos"])))
            yield context.create_timer(context.current_utc_datetime + timedelta(seconds=delay))
    context.continue_as_new({"id_actualizacion": request_id, "recuperar": request_id is None})


def safe_activity(method, value):
    if not enabled():
        raise RuntimeError("TERCEROS_AUTOMATIZACION_DESHABILITADA")
    try:
        return method(value)
    except Exception as error:
        logging.error("Terceros actividad fallo tipo=%s", type(error).__name__)
        raise RuntimeError("TERCEROS_ACTIVIDAD_FALLIDA") from None


@bp.function_name(name="TercerosRegistrarAviso")
@bp.activity_trigger(input_name="value")
def register_activity(value):
    return safe_activity(control.register, value)


@bp.function_name(name="TercerosProcesarActualizacion")
@bp.activity_trigger(input_name="value")
def process_activity(value):
    return safe_activity(automation.process, value)
