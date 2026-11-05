"""Archivo temporal privado. Ningun contenido Excel entra en el historial Durable."""
import hashlib
import os
import re
from contextlib import contextmanager

from azure.core import MatchConditions
from azure.core.exceptions import ResourceExistsError
from azure.identity import AzureCliCredential, ManagedIdentityCredential
from azure.storage.blob import BlobServiceClient, ContentSettings

from .domain import CanopusError, max_bytes


@contextmanager
def container():
    url = os.getenv("CANOPUS_STORAGE_ACCOUNT_URL", "")
    name = os.getenv("CANOPUS_STAGING_CONTAINER", "canopus-staging")
    if not re.fullmatch(r"https://[a-z0-9]{3,24}\.blob\.core\.windows\.net", url) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,61}[a-z0-9]", name):
        raise CanopusError("CANOPUS_STORAGE_CONFIGURACION_INVALIDA", 503)
    credential = (ManagedIdentityCredential(client_id=os.getenv("TERCEROS_MANAGED_IDENTITY_CLIENT_ID") or None)
                  if os.getenv("WEBSITE_HOSTNAME") or os.getenv("WEBSITE_INSTANCE_ID") else AzureCliCredential())
    try:
        with BlobServiceClient(url, credential=credential, retry_total=0, connection_timeout=5, read_timeout=20) as service:
            yield service.get_container_client(name)
    finally:
        credential.close()


def stage(info, content):
    name = info["id_seguimiento"] + ".xlsx"
    with container() as client:
        blob = client.get_blob_client(name)
        try:
            blob.upload_blob(content, overwrite=False, metadata={"sha256": info["hash_contenido"]},
                             content_settings=ContentSettings(content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"), timeout=20)
        except ResourceExistsError:
            pass
        properties = blob.get_blob_properties(timeout=10)
        if properties.size != info["bytes"] or properties.metadata.get("sha256") != info["hash_contenido"]:
            raise CanopusError("CANOPUS_BLOB_CONFLICTIVO", 409)
        return dict(info, blob=name, etag=properties.etag)


def download(info):
    if (info.get("blob") != info["id_seguimiento"] + ".xlsx" or not 0 < info["bytes"] <= max_bytes()):
        raise CanopusError("CANOPUS_REFERENCIA_INVALIDA")
    with container() as client:
        blob = client.get_blob_client(info["blob"])
        properties = blob.get_blob_properties(timeout=10)
        if properties.size != info["bytes"] or properties.etag != info["etag"]:
            raise CanopusError("CANOPUS_BLOB_MODIFICADO", 409)
        content = blob.download_blob(etag=info["etag"], match_condition=MatchConditions.IfNotModified,
                                     max_concurrency=1, timeout=30).readall()
    if len(content) != info["bytes"] or hashlib.sha256(content).hexdigest() != info["hash_contenido"]:
        raise CanopusError("CANOPUS_HASH_INVALIDO")
    return content
