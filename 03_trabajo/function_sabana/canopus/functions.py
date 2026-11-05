"""Recepcion corta y trabajo Durable separado de la solicitud de Power Automate."""
import asyncio
import json
import logging

import azure.functions as func
import azure.durable_functions as df

from . import service, storage
from .domain import CanopusError, JOB, enabled, intake, max_bytes

bp = df.Blueprint()


def response(value, status=200, location=None):
    headers = {"Cache-Control": "no-store"}
    if location:
        headers.update(Location=location, **{"Retry-After": "30"})
    return func.HttpResponse(json.dumps(value, ensure_ascii=True), status_code=status,
                             mimetype="application/json", headers=headers)


@bp.function_name(name="CargarCanopus")
@bp.route(route="canopus/cargas", methods=["POST"], auth_level=func.AuthLevel.FUNCTION)
@bp.durable_client_input(client_name="client")
async def start(req: func.HttpRequest, client):
    if not enabled():
        return response({"codigo": "CANOPUS_INGESTA_DESHABILITADA"}, 503)
    try:
        if len(req.get_body()) > 4 * ((max_bytes() + 2) // 3) + 8192:
            raise CanopusError("CANOPUS_SOLICITUD_GRANDE", 413)
        try:
            info, content = intake(req.get_json())
        except ValueError as error:
            if isinstance(error, CanopusError):
                raise
            raise CanopusError("CANOPUS_JSON_INVALIDO") from None
        job = info["id_seguimiento"]
        current = await asyncio.wait_for(client.get_status(job), timeout=10)
        if not current:
            # Unicamente se conserva la referencia al archivo, nunca el base64 en Durable.
            reference = await asyncio.wait_for(asyncio.to_thread(storage.stage, info, content), timeout=60)
            await asyncio.wait_for(client.start_new("OrquestarCargaCanopus", instance_id=job,
                                                   client_input=reference), timeout=10)
        location = req.url.split("?", 1)[0].rstrip("/") + "/" + job
        return response({"id_seguimiento": job, "estado": current.runtime_status.name if current else "ACEPTADA",
                         "consulta_estado": location}, 202, location)
    except CanopusError as error:
        return response({"codigo": error.code}, error.status)
    except Exception as error:
        logging.error("Canopus inicio fallo tipo=%s", type(error).__name__)
        return response({"codigo": "CANOPUS_INICIO_NO_DISPONIBLE"}, 503)


@bp.function_name(name="EstadoCargaCanopus")
@bp.route(route="canopus/cargas/{job_id}", methods=["GET"], auth_level=func.AuthLevel.FUNCTION)
@bp.durable_client_input(client_name="client")
async def status(req: func.HttpRequest, client):
    job = req.route_params.get("job_id", "")
    if not JOB.fullmatch(job):
        return response({"codigo": "CANOPUS_ID_INVALIDO"}, 400)
    try:
        current = await asyncio.wait_for(client.get_status(job), timeout=10)
        if not current:
            return response({"codigo": "CANOPUS_NO_ENCONTRADO"}, 404)
        state = current.runtime_status.name
        result = {"id_seguimiento": job, "estado": state}
        if state == "Completed":
            result["resultado"] = current.output
        elif state in {"Failed", "Terminated"}:
            result["codigo"] = "CANOPUS_ORQUESTACION_FALLIDA"
        return response(result)
    except Exception as error:
        logging.error("Canopus estado fallo tipo=%s", type(error).__name__)
        return response({"codigo": "CANOPUS_ESTADO_NO_DISPONIBLE"}, 503)


@bp.function_name(name="ReanudarCargaCanopus")
@bp.route(route="canopus/cargas/{job_id}/reanudar", methods=["POST"], auth_level=func.AuthLevel.FUNCTION)
@bp.durable_client_input(client_name="client")
async def resume(req: func.HttpRequest, client):
    if not enabled():
        return response({"codigo": "CANOPUS_INGESTA_DESHABILITADA"}, 503)
    job = req.route_params.get("job_id", "")
    if not JOB.fullmatch(job):
        return response({"codigo": "CANOPUS_ID_INVALIDO"}, 400)
    try:
        current = await asyncio.wait_for(client.get_status(job), timeout=10)
        if not current:
            return response({"codigo": "CANOPUS_NO_ENCONTRADO"}, 404)
        if current.runtime_status.name not in {"Failed", "Terminated"}:
            return response({"codigo": "CANOPUS_NO_REANUDABLE"}, 409)
        await asyncio.wait_for(client.restart(job, restart_with_new_instance_id=False), timeout=10)
        return response({"id_seguimiento": job, "estado": "REANUDADA"}, 202)
    except Exception as error:
        logging.error("Canopus reanudar fallo tipo=%s", type(error).__name__)
        return response({"codigo": "CANOPUS_REANUDACION_NO_DISPONIBLE"}, 503)


@bp.function_name(name="OrquestarCargaCanopus")
@bp.orchestration_trigger(context_name="context")
def orchestrate(context):
    return (yield from run_orchestration(context))


def run_orchestration(context):
    context.set_custom_status({"fase": "PROCESANDO"})
    result = yield context.call_activity_with_retry("CanopusProcesar", df.RetryOptions(15000, 3), context.get_input())
    context.set_custom_status({"fase": result["estado"]})
    return result


@bp.function_name(name="CanopusProcesar")
@bp.activity_trigger(input_name="value")
def process_activity(value):
    return service.process(value)
