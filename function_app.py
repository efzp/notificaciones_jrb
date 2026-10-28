"""Entrada HTTP de Azure Functions (Python v2)."""
import json
import logging
import os
from uuid import uuid4

import azure.functions as func
import azure.durable_functions as df

from sabana.domain import AppError, Payload
from sabana.service import consolidate

app = df.DFApp(http_auth_level=func.AuthLevel.FUNCTION)
from pst.functions import bp as pst_blueprint
app.register_functions(pst_blueprint)


@app.function_name(name="ConsolidarSabana")
@app.route(route="consolidar-sabana", methods=["POST"])
def consolidar_sabana(req: func.HttpRequest) -> func.HttpResponse:
    correlation = str(uuid4())
    status = 500
    try:
        maximum = int(os.environ.get("MAX_EXCEL_BYTES", "10485760"))
        body = req.get_body()
        if len(body) > 4 * ((maximum + 2) // 3) + 65536:
            raise AppError("SOLICITUD_GRANDE", "La solicitud supera el limite configurado.", 413)
        try:
            document = json.loads(body)
        except (ValueError, UnicodeDecodeError):
            raise AppError("JSON_INVALIDO", "Envie un objeto JSON valido.") from None
        payload = Payload.from_json(document, max_bytes=maximum)
        result, status = consolidate(
            payload,
            max_seconds=int(os.environ.get("MAX_PROCESSING_SECONDS", "100")),
            max_rows=int(os.environ.get("MAX_EXCEL_ROWS", "2000")),
        )
    except AppError as error:
        status = error.status
        result = {"estado": "ERROR", "codigo": error.code, "mensaje": error.message}
    except Exception as error:
        # No registrar cuerpos HTTP, base64, valores de celdas ni errores SQL completos.
        logging.error("Error no controlado correlacion=%s tipo=%s", correlation, type(error).__name__)
        result = {"estado": "ERROR", "codigo": "ERROR_INTERNO", "mensaje": "Revise la ejecucion mediante su correlacion."}
    result["correlacion"] = correlation
    headers = {"Cache-Control": "no-store", "x-correlation-id": correlation}
    if status == 503:
        headers["Retry-After"] = "10"
    logging.info("ConsolidarSabana correlacion=%s http=%s id_carga=%s", correlation, status, result.get("id_carga"))
    return func.HttpResponse(json.dumps(result, ensure_ascii=False), status_code=status,
                             mimetype="application/json", headers=headers)
