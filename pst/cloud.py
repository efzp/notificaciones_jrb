from __future__ import annotations

import hashlib
import os
import re
import time
from contextlib import contextmanager
from urllib.parse import quote, urlparse

from .domain import PstFailure, integer_setting
from .range_io import RangeReader


@contextmanager
def identity():
    from azure.identity import DefaultAzureCredential, ManagedIdentityCredential
    credential = (ManagedIdentityCredential(client_id=os.environ.get("PST_MANAGED_IDENTITY_CLIENT_ID") or None)
                  if os.environ.get("WEBSITE_HOSTNAME") or os.environ.get("WEBSITE_INSTANCE_ID")
                  else DefaultAzureCredential(exclude_interactive_browser_credential=True))
    try:
        yield credential
    finally:
        credential.close()


class Graph:
    def __init__(self, credential):
        import requests
        from requests.adapters import HTTPAdapter
        from urllib3.util.retry import Retry
        self.credential = credential
        self.session = requests.Session()
        self.session.mount("https://", HTTPAdapter(max_retries=Retry(total=4, backoff_factor=2,
                           status_forcelist=(429, 500, 502, 503, 504), allowed_methods=("GET",), respect_retry_after_header=True)))

    def close(self):
        self.session.close()

    def get(self, path):
        token = self.credential.get_token("https://graph.microsoft.com/.default").token
        try:
            response = self.session.get("https://graph.microsoft.com/v1.0/"+path,
                                        headers={"Authorization": "Bearer "+token}, timeout=(10, 60))
            if response.status_code != 200:
                raise PstFailure("GRAPH_HTTP_"+str(response.status_code))
            return response.json()
        except PstFailure:
            raise
        except Exception:
            raise PstFailure("GRAPH_NO_DISPONIBLE") from None

    def metadata(self, request):
        base = "drives/"+quote(request.drive_id, safe="")+"/items/"
        value = self.get(base+quote(request.item_id, safe=""))
        if value.get("eTag") != request.version_origen:
            raise PstFailure("PST_VERSION_CAMBIO")
        if not value.get("file") or not value.get("name", "").lower().endswith(".pst"):
            raise PstFailure("PST_EXTENSION_INVALIDA")
        maximum = integer_setting("PST_MAX_BYTES", 20*1024**3, 1, 100*1024**3)
        if not 0 < value.get("size", 0) <= maximum:
            raise PstFailure("PST_TAMANO_NO_PERMITIDO")
        root = os.environ.get("PST_SOURCE_FOLDER_ID", "")
        if not root:
            raise PstFailure("PST_CARPETA_NO_CONFIGURADA")
        parent = value.get("parentReference", {}).get("id")
        for _ in range(64):
            if parent == root:
                break
            if not parent:
                raise PstFailure("PST_FUERA_DE_CARPETA")
            parent = self.get(base+quote(parent, safe="")+"?$select=id,parentReference").get("parentReference", {}).get("id")
        else:
            raise PstFailure("PST_FUERA_DE_CARPETA")
        return value

    def chunks(self, metadata):
        import requests
        url = metadata.get("@microsoft.graph.downloadUrl", "")
        parsed = urlparse(url)
        # URL generada por Graph, nunca aceptada desde el HTTP del usuario.
        if parsed.scheme != "https" or not parsed.hostname or not parsed.hostname.endswith(".sharepoint.com"):
            raise PstFailure("PST_DESCARGA_NO_AUTORIZADA")
        deadline = time.monotonic()+integer_setting("PST_STAGE_SECONDS", 1500, 60, 1650)
        total = 0
        try:
            # No enviar el bearer Graph a una URL preautenticada de SharePoint.
            # Sesion sin reintentos HTTP internos: urllib3 puede registrar una URL
            # preautenticada al reintentar. Durable reintenta la actividad sin exponerla.
            with requests.Session() as download, download.get(url, stream=True, timeout=(15, 90), allow_redirects=False) as response:
                if response.status_code != 200:
                    raise PstFailure("PST_DESCARGA_HTTP_"+str(response.status_code))
                for chunk in response.iter_content(8*1024*1024):
                    if time.monotonic() >= deadline:
                        raise PstFailure("PST_PREPARACION_TIEMPO_AGOTADO")
                    if chunk:
                        total += len(chunk)
                        if total > metadata["size"]:
                            raise PstFailure("PST_TAMANO_CAMBIO")
                        yield chunk
            if total != metadata["size"]:
                raise PstFailure("PST_DESCARGA_INCOMPLETA")
        except PstFailure:
            raise
        except Exception:
            raise PstFailure("PST_DESCARGA_INTERRUPPIDA") from None


@contextmanager
def staging(credential):
    from azure.storage.blob import BlobServiceClient
    url = os.environ.get("PST_STORAGE_ACCOUNT_URL", "")
    container = os.environ.get("PST_STAGING_CONTAINER", "pst-staging")
    if not re.fullmatch(r"https://[a-z0-9]{3,24}\.blob\.core\.windows\.net/?", url):
        raise PstFailure("PST_STORAGE_NO_CONFIGURADO")
    with BlobServiceClient(url, credential=credential, retry_total=3, connection_timeout=15, read_timeout=90) as service:
        yield service.get_container_client(container)


def stage_file(container, graph, request, metadata):
    from azure.core.exceptions import ResourceExistsError, ResourceNotFoundError
    from azure.core import MatchConditions
    blob = container.get_blob_client(request.job_id+".pst")
    try:
        properties = blob.get_blob_properties()
    except ResourceNotFoundError:
        digest = hashlib.sha256()
        def stream():
            for chunk in graph.chunks(metadata):
                digest.update(chunk)
                yield chunk
        try:
            # Un blob de bloque solo se hace visible cuando termina el upload.
            blob.upload_blob(stream(), overwrite=False, max_concurrency=1, length=metadata["size"],
                             metadata={"job": request.job_id})
            graph.metadata(request)  # Verificar eTag nuevamente despues de la copia.
            properties = blob.get_blob_properties()
            blob.set_blob_metadata({"job": request.job_id, "sha256": digest.hexdigest()},
                                   etag=properties.etag, match_condition=MatchConditions.IfNotModified)
            properties = blob.get_blob_properties()
        except ResourceExistsError:
            properties = blob.get_blob_properties()
    if properties.size != metadata["size"] or properties.metadata.get("job") != request.job_id:
        raise PstFailure("PST_STAGING_CONFLICTO")
    if not re.fullmatch(r"[0-9a-f]{64}", properties.metadata.get("sha256", "")):
        # Recupera el corte entre commit del blob y registro del hash, sin sobrescribir el PST.
        digest = hashlib.sha256()
        deadline = time.monotonic()+integer_setting("PST_STAGE_SECONDS", 1500, 60, 1650)
        for chunk in blob.download_blob(etag=properties.etag, match_condition=MatchConditions.IfNotModified).chunks():
            if time.monotonic()>=deadline:
                raise PstFailure("PST_PREPARACION_TIEMPO_AGOTADO")
            digest.update(chunk)
        graph.metadata(request)
        blob.set_blob_metadata({"job": request.job_id, "sha256": digest.hexdigest()},
                               etag=properties.etag, match_condition=MatchConditions.IfNotModified)
        properties = blob.get_blob_properties()
    return {"blob": blob.blob_name, "blob_etag": properties.etag, "bytes": properties.size,
            "sha256": properties.metadata["sha256"]}


@contextmanager
def open_pst(container, reference):
    import pypff
    from azure.core import MatchConditions
    blob = container.get_blob_client(reference["blob"])
    def fetch(offset, length):
        try:
            return blob.download_blob(offset=offset, length=length, etag=reference["blob_etag"],
                                      match_condition=MatchConditions.IfNotModified, max_concurrency=1).readall()
        except Exception:
            raise PstFailure("PST_RANGO_NO_DISPONIBLE") from None
    reader = RangeReader(reference["bytes"], fetch)
    file = pypff.file()
    opened = False
    try:
        file.open_file_object(reader)
        opened = True
        yield file
    finally:
        try:
            if opened:
                file.close()
        finally:
            reader.close()
            if reader.failed:
                raise PstFailure("PST_RANGO_NO_DISPONIBLE") from None
