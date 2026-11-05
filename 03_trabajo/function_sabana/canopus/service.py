import logging

from . import excel, storage
from .domain import CanopusError, VERSION, enabled
from .repository import Repository


def process(info):
    if not enabled():
        raise RuntimeError("CANOPUS_INGESTA_DESHABILITADA")
    if info.get("version_parser") != VERSION:
        return {"estado": "ERROR", "codigo": "CANOPUS_VERSION_PARSER_NO_DISPONIBLE"}
    repository, load = None, None
    try:
        content = storage.download(info)
        repository = Repository()
        load = repository.register(info)
        rows, counts = excel.parse(content, repository.matcher())
        return repository.publish(load, info, rows, counts)
    except Exception as error:
        if repository is not None and load is not None:
            try:
                repository.mark_error(load)
            except Exception as marking:
                logging.error("Canopus registrar error tipo=%s", type(marking).__name__)
        if isinstance(error, CanopusError):
            result = {"estado": "ERROR", "codigo": error.code}
            if load is not None:
                result["id_carga_canopus"] = load
            return result
        logging.error("Canopus procesar fallo tipo=%s", type(error).__name__)
        raise RuntimeError("CANOPUS_ACTIVIDAD_NO_DISPONIBLE") from None
    finally:
        if repository is not None:
            try:
                repository.close()
            except Exception as error:
                logging.error("Canopus cerrar conexion tipo=%s", type(error).__name__)
