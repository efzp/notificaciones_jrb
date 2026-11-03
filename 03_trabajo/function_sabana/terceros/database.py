from __future__ import annotations

import os
import struct
import time
from contextlib import contextmanager

TARGETS = {"JNC": ("sql-jnc-notif-dev.database.windows.net", "sqldb-jnc-notif-dev"),
           "JRB": ("sql-jrb-sabana-dev.database.windows.net", "sqldb-jrb-sabana-dev")}


def connect(source, write=False):
    import pyodbc
    from azure.identity import AzureCliCredential, ManagedIdentityCredential
    if source not in TARGETS or (write and source != "JRB"):
        raise ValueError("DESTINO_NO_PERMITIDO")
    server, database = TARGETS[source]
    credential = (ManagedIdentityCredential(client_id=os.environ.get("TERCEROS_MANAGED_IDENTITY_CLIENT_ID") or None)
                  if os.environ.get("WEBSITE_HOSTNAME") or os.environ.get("WEBSITE_INSTANCE_ID")
                  else AzureCliCredential())
    try:
        token = credential.get_token("https://database.windows.net/.default").token.encode("utf-16le")
    finally:
        credential.close()
    packed = struct.pack("<I", len(token))+token
    pyodbc.pooling = False
    for attempt in range(4):
        try:
            connection = pyodbc.connect(
                f"Driver={{ODBC Driver 18 for SQL Server}};Server=tcp:{server},1433;Database={database};"
                "Encrypt=yes;TrustServerCertificate=no;Application Name=JRB-Catalogo-Terceros;"
                + ("" if write else "ApplicationIntent=ReadOnly;"),
                attrs_before={1256:packed}, autocommit=True, timeout=20)
            connection.timeout = 180
            actual = connection.cursor().execute("SELECT DB_NAME();").fetchone()[0]
            if actual != database:
                connection.close()
                raise ValueError("BASE_INCORRECTA")
            return connection
        except pyodbc.Error as error:
            text = str(error)
            if attempt == 3 or not any(f"({n})" in text for n in (40613, 40197, 40501)):
                raise RuntimeError("CONEXION_SQL_"+source+"_NO_DISPONIBLE") from None
            time.sleep(2**attempt)


@contextmanager
def snapshot(source):
    connection = connect(source)
    try:
        cursor = connection.cursor()
        enabled = cursor.execute("SELECT snapshot_isolation_state FROM sys.databases WHERE name=DB_NAME();").fetchone()[0]
        if enabled != 1:
            raise RuntimeError("AISLAMIENTO_SNAPSHOT_REQUERIDO_"+source)
        cursor.execute("SET TRANSACTION ISOLATION LEVEL SNAPSHOT; BEGIN TRANSACTION;")
        yield connection
        cursor.execute("COMMIT TRANSACTION;")
    except Exception:
        try:
            connection.cursor().execute("IF @@TRANCOUNT>0 ROLLBACK TRANSACTION;")
        except Exception:
            pass
        raise
    finally:
        connection.close()


def rows(connection, sql):
    cursor = connection.cursor()
    try:
        cursor.execute(sql)
        names = [c[0] for c in cursor.description]
        for row in cursor:
            yield dict(zip(names, row))
    finally:
        cursor.close()
