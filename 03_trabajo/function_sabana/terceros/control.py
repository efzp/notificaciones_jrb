"""Cola SQL, reservas con vencimiento y finalizacion transaccional."""
from __future__ import annotations

from contextlib import contextmanager
from uuid import uuid4

from sabana.database import SESSION_OPTIONS
from .automation_domain import AutomationError, Notice, execution, setting
from .database import connect


@contextmanager
def transaction():
    connection = connect("JRB", write=True)
    cursor = connection.cursor()
    try:
        cursor.execute(SESSION_OPTIONS)
        cursor.execute("BEGIN TRANSACTION;")
        lock(cursor)
        yield cursor
        cursor.execute("COMMIT TRANSACTION;")
    except Exception:
        try:
            cursor.execute("IF @@TRANCOUNT>0 ROLLBACK TRANSACTION;")
        except Exception:
            pass
        raise
    finally:
        connection.close()


def lock(cursor):
    result = cursor.execute("DECLARE @r int; EXEC @r=sys.sp_getapplock @Resource=N'terceros:carga',"
                            "@LockMode='Exclusive',@LockOwner='Transaction',@LockTimeout=10000; SELECT @r;").fetchone()[0]
    if result < 0:
        raise AutomationError("TERCEROS_CONTROL_OCUPADO", 503)


def register(document):
    notice = Notice.parse(document)
    delay = setting("TERCEROS_DEBOUNCE_SECONDS", 600, 0, 3600)
    with transaction() as cursor:
        values = (notice.origen, notice.tipo_fuente, notice.id_carga_origen,
                  bytes.fromhex(notice.hash_revision))
        row = cursor.execute("SELECT id_actualizacion FROM ing.terceros_actualizacion WITH(UPDLOCK,HOLDLOCK) "
                             "WHERE origen=? AND tipo_fuente=? AND id_carga_origen=? AND hash_revision=?;", *values).fetchone()
        if row:
            return {"id_actualizacion": row[0], "reutilizada": True}
        row = cursor.execute("INSERT ing.terceros_actualizacion(origen,tipo_fuente,id_carga_origen,hash_revision,disponible_utc) "
                             "OUTPUT inserted.id_actualizacion VALUES(?,?,?,?,DATEADD(SECOND,?,SYSUTCDATETIME()));",
                             *values, delay).fetchone()
        # Ventana de silencio compartida: los archivos de un mismo lote esperan
        # juntos diez minutos desde el ultimo aviso nuevo. Un duplicado no la extiende.
        cursor.execute("UPDATE ing.terceros_actualizacion SET disponible_utc=DATEADD(SECOND,?,SYSUTCDATETIME()),"
                       "actualizado_utc=SYSUTCDATETIME() WHERE estado='PENDIENTE';", delay)
        return {"id_actualizacion": row[0], "reutilizada": False}


def state(request_id):
    connection = connect("JRB")
    try:
        row = connection.cursor().execute("SELECT estado,intentos,max_intentos,id_carga_terceros,codigo_ultimo_error "
                                           "FROM ing.terceros_actualizacion WHERE id_actualizacion=?;", request_id).fetchone()
        if not row:
            raise AutomationError("TERCEROS_SOLICITUD_NO_ENCONTRADA", 404)
        return dict(id_actualizacion=request_id, estado=row[0], intentos=row[1], max_intentos=row[2],
                    id_carga_terceros=row[3], codigo=row[4])
    finally:
        connection.close()


def claim():
    lease = setting("TERCEROS_LEASE_SECONDS", 1200, 120, 3600)
    limit = setting("TERCEROS_BATCH_REQUESTS", 1000, 1, 1000)
    with transaction() as cursor:
        # Si el proceso murio, la siguiente ejecucion recupera la reserva.
        cursor.execute("UPDATE ing.terceros_actualizacion SET "
                       "estado=CASE WHEN intentos>=max_intentos THEN 'AGOTADA' ELSE 'REINTENTO' END,"
                       "reserva_hasta_utc=NULL,actualizado_utc=SYSUTCDATETIME(),"
                       "finalizado_utc=CASE WHEN intentos>=max_intentos THEN SYSUTCDATETIME() ELSE NULL END,"
                       "disponible_utc=SYSUTCDATETIME(),codigo_ultimo_error='TERCEROS_RESERVA_EXPIRADA' "
                       "WHERE estado='EN_PROCESO' AND reserva_hasta_utc<=SYSUTCDATETIME();")
        busy = cursor.execute("SELECT MAX(DATEDIFF(SECOND,SYSUTCDATETIME(),reserva_hasta_utc)) "
                              "FROM ing.terceros_actualizacion WHERE estado='EN_PROCESO';").fetchone()[0]
        if busy is not None:
            return {"estado": "ESPERANDO", "espera_segundos": max(5, min(3600, busy + 1))}
        token = str(uuid4())  # Nuevo token por intento fisico; un proceso viejo no lo reutiliza.
        rows = cursor.execute(";WITH pendientes AS (SELECT TOP(?) * FROM ing.terceros_actualizacion "
                              "WITH(UPDLOCK,HOLDLOCK) WHERE estado IN('PENDIENTE','REINTENTO') "
                              "AND disponible_utc<=SYSUTCDATETIME() AND intentos<max_intentos "
                              "ORDER BY disponible_utc,id_actualizacion) UPDATE pendientes SET "
                              "estado='EN_PROCESO',intentos=intentos+1,id_ejecucion=?,inicio_intento_utc=SYSUTCDATETIME(),"
                              "reserva_hasta_utc=DATEADD(SECOND,?,SYSUTCDATETIME()),actualizado_utc=SYSUTCDATETIME(),"
                              "codigo_ultimo_error=NULL OUTPUT inserted.id_actualizacion;", limit, token, lease).fetchall()
        if rows:
            return {"estado": "RESERVADA", "id_ejecucion": token, "solicitudes": len(rows)}
        wait = cursor.execute("SELECT MIN(DATEDIFF(SECOND,SYSUTCDATETIME(),disponible_utc)) "
                              "FROM ing.terceros_actualizacion WHERE estado IN('PENDIENTE','REINTENTO');").fetchone()[0]
        return ({"estado": "SIN_PENDIENTES"} if wait is None else
                {"estado": "ESPERANDO", "espera_segundos": max(5, min(3600, wait + 1))})


def assert_reserved(cursor, reservation):
    token, count = execution(reservation)
    actual = cursor.execute("SELECT COUNT(*) FROM ing.terceros_actualizacion WITH(UPDLOCK,HOLDLOCK) "
                            "WHERE id_ejecucion=? AND estado='EN_PROCESO' AND reserva_hasta_utc>SYSUTCDATETIME();",
                            token).fetchone()[0]
    if actual != count:
        raise AutomationError("TERCEROS_RESERVA_PERDIDA", 409)


def heartbeat(reservation):
    token, count = execution(reservation)
    lease = setting("TERCEROS_LEASE_SECONDS", 1200, 120, 3600)
    with transaction() as cursor:
        assert_reserved(cursor, reservation)
        cursor.execute("UPDATE ing.terceros_actualizacion SET reserva_hasta_utc=DATEADD(SECOND,?,SYSUTCDATETIME()),"
                       "actualizado_utc=SYSUTCDATETIME() WHERE id_ejecucion=? AND estado='EN_PROCESO';", lease, token)


def complete(cursor, reservation, load_id):
    assert_reserved(cursor, reservation)
    token, count = execution(reservation)
    cursor.execute("UPDATE ing.terceros_actualizacion SET estado='COMPLETADA',id_carga_terceros=?,"
                   "reserva_hasta_utc=NULL,finalizado_utc=SYSUTCDATETIME(),actualizado_utc=SYSUTCDATETIME(),"
                   "codigo_ultimo_error=NULL WHERE id_ejecucion=? AND estado='EN_PROCESO';", load_id, token)


def fail(reservation, code="TERCEROS_ACTUALIZACION_FALLIDA"):
    token, count = execution(reservation)
    # Solo codigos propios, nunca el mensaje del driver ni valores de negocio.
    allowed = {"TERCEROS_ACTUALIZACION_FALLIDA", "TERCEROS_RESERVA_PERDIDA"}
    if code not in allowed:
        code = "TERCEROS_ACTUALIZACION_FALLIDA"
    with transaction() as cursor:
        cursor.execute("UPDATE ing.terceros_actualizacion SET "
                       "estado=CASE WHEN intentos>=max_intentos THEN 'AGOTADA' ELSE 'REINTENTO' END,"
                       "reserva_hasta_utc=NULL,actualizado_utc=SYSUTCDATETIME(),"
                       "finalizado_utc=CASE WHEN intentos>=max_intentos THEN SYSUTCDATETIME() ELSE NULL END,"
                       "disponible_utc=DATEADD(SECOND,CASE WHEN intentos<5 THEN 60*POWER(2,intentos-1) ELSE 900 END,SYSUTCDATETIME()),"
                       "codigo_ultimo_error=? WHERE id_ejecucion=? AND estado='EN_PROCESO' "
                       "AND reserva_hasta_utc>SYSUTCDATETIME();", code, token)
