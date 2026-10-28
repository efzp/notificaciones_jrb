"""HTTP rapido + orquestacion durable. No lanzar hilos despues de devolver HTTP."""
from __future__ import annotations

import json
import logging
import asyncio

import azure.functions as func
import azure.durable_functions as df

from sabana.domain import AppError
from .domain import JOB_PATTERN, PstFailure
from . import service

bp = df.Blueprint()


def response(document, status=200, location=None):
    headers = {"Cache-Control": "no-store"}
    if location:
        headers.update(Location=location, **{"Retry-After": "30"})
    return func.HttpResponse(json.dumps(document, ensure_ascii=False), status_code=status,
                             mimetype="application/json", headers=headers)


@bp.function_name(name="IniciarIngestaPst")
@bp.route(route="pst/ingestas", methods=["POST"], auth_level=func.AuthLevel.FUNCTION)
@bp.durable_client_input(client_name="client")
async def start(req: func.HttpRequest, client):
    try:
        if len(req.get_body()) > 4096:
            return response({"codigo": "PST_SOLICITUD_GRANDE"}, 413)
        try:
            document = req.get_json()
        except ValueError:
            return response({"codigo": "JSON_INVALIDO"}, 400)
        request = service.request_from(document)
        job_id = request.job_id
        current = await asyncio.wait_for(client.get_status(job_id), timeout=15)
        if current is None:
            await asyncio.wait_for(client.start_new("OrquestarPst", instance_id=job_id, client_input=request.document()), timeout=15)
        location = req.url.split("?", 1)[0].rstrip("/")+"/"+job_id
        return response({"id_seguimiento": job_id, "estado": "ACEPTADA" if current is None else current.runtime_status.name,
                         "consulta_estado": location}, 202, location)
    except AppError as error:
        return response({"codigo": error.code, "mensaje": error.message}, error.status)
    except Exception as error:
        logging.error("PST inicio fallo tipo=%s", type(error).__name__)
        return response({"codigo": "PST_INICIO_NO_DISPONIBLE"}, 503)


@bp.function_name(name="EstadoIngestaPst")
@bp.route(route="pst/ingestas/{job_id}", methods=["GET"], auth_level=func.AuthLevel.FUNCTION)
@bp.durable_client_input(client_name="client")
async def status(req: func.HttpRequest, client):
    job_id = req.route_params.get("job_id", "")
    if not JOB_PATTERN.fullmatch(job_id):
        return response({"codigo": "PST_ID_INVALIDO"}, 400)
    try:
        current = await asyncio.wait_for(client.get_status(job_id), timeout=15)
        if current is None:
            return response({"codigo": "PST_NO_ENCONTRADO"}, 404)
        state = current.runtime_status.name
        document = {"id_seguimiento": job_id, "estado": state, "progreso": current.custom_status}
        if state == "Completed":
            document["resultado"] = current.output
        elif state in ("Failed", "Terminated"):
            # output de Durable puede contener detalles de fallos: no exponerlo.
            document["codigo"] = "PST_INGESTA_FALLIDA"
        return response(document)
    except Exception as error:
        logging.error("PST estado fallo tipo=%s", type(error).__name__)
        return response({"codigo": "PST_ESTADO_NO_DISPONIBLE"}, 503)


@bp.function_name(name="ReanudarIngestaPst")
@bp.route(route="pst/ingestas/{job_id}/reanudar", methods=["POST"], auth_level=func.AuthLevel.FUNCTION)
@bp.durable_client_input(client_name="client")
async def resume(req: func.HttpRequest, client):
    job_id = req.route_params.get("job_id", "")
    if not JOB_PATTERN.fullmatch(job_id):
        return response({"codigo": "PST_ID_INVALIDO"}, 400)
    try:
        current = await asyncio.wait_for(client.get_status(job_id), timeout=15)
        if current is None:
            return response({"codigo": "PST_NO_ENCONTRADO"}, 404)
        if current.runtime_status.name not in ("Failed", "Terminated"):
            return response({"codigo": "PST_NO_REANUDABLE", "estado": current.runtime_status.name}, 409)
        await asyncio.wait_for(client.restart(job_id, restart_with_new_instance_id=False), timeout=15)
        return response({"id_seguimiento": job_id, "estado": "REANUDADA"}, 202)
    except Exception as error:
        logging.error("PST reanudar fallo tipo=%s", type(error).__name__)
        return response({"codigo": "PST_REANUDACION_NO_DISPONIBLE"}, 503)


@bp.function_name(name="OrquestarPst")
@bp.orchestration_trigger(context_name="context")
def orchestrate(context):
    return (yield from run_orchestration(context))


def run_orchestration(context):
    document = context.get_input()
    retry = df.RetryOptions(10000, 3)
    reference = document.get("_referencia")
    try:
        if reference is None:
            context.set_custom_status({"fase": "PREPARANDO_PST"})
            reference = yield context.call_activity_with_retry("PstPreparar", retry, document)
            if not reference["completa"]:
                context.set_custom_status({"fase": "INVENTARIANDO", "id_carga": reference["id_carga"]})
                yield context.call_activity_with_retry("PstInventariar", retry, reference)
        if reference["completa"]:
            result = yield context.call_activity_with_retry("PstFinalizar", retry, reference)
            return result
        # Mantener historial pequeno. SQL conserva el punto de reanudacion.
        for _ in range(50):
            progress = yield context.call_activity_with_retry("PstProcesarLote", retry, reference)
            if progress["terminado"]:
                return (yield context.call_activity_with_retry("PstFinalizar", retry, reference))
            context.set_custom_status(dict(fase="EXTRAYENDO", **progress))
        context.continue_as_new({"_referencia": reference})
    except Exception:
        if reference:
            yield context.call_activity_with_retry("PstMarcarError", retry, reference)
        raise RuntimeError("PST_INGESTA_FALLIDA") from None


def safe_activity(method, value):
    try:
        return method(value)
    except PstFailure as error:
        raise RuntimeError(error.code) from None
    except Exception as error:
        logging.error("PST actividad fallo tipo=%s", type(error).__name__)
        raise RuntimeError("PST_ACTIVIDAD_FALLIDA") from None


@bp.function_name(name="PstPreparar")
@bp.activity_trigger(input_name="value")
def prepare_activity(value):
    return safe_activity(service.prepare, value)


@bp.function_name(name="PstInventariar")
@bp.activity_trigger(input_name="value")
def inventory_activity(value):
    return safe_activity(service.build_inventory, value)


@bp.function_name(name="PstProcesarLote")
@bp.activity_trigger(input_name="value")
def batch_activity(value):
    return safe_activity(service.process_batch, value)


@bp.function_name(name="PstFinalizar")
@bp.activity_trigger(input_name="value")
def finish_activity(value):
    return safe_activity(service.finish, value)


@bp.function_name(name="PstMarcarError")
@bp.activity_trigger(input_name="value")
def fail_activity(value):
    return safe_activity(service.fail, value)
