from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone

from .database import rows, snapshot
from .domain import Catalog, digest

CALIFICACION = """
SELECT s.id_calificacion_sistema_envio id_origen,'CALIFICACION' fuente,
 s.tipo_entidad rol,COALESCE(NULLIF(s.nombre_entidad_normalizado,N''),s.nombre_entidad) entidad,
 s.correo_normalizado correo,s.cedula_normalizada cedula,s.numero_radicado_normalizado radicado,
 c.fecha_audiencia audiencia,s.id_calificacion_sistema_caso id_caso,s.fecha_notificacion_reportada fecha
FROM jnc.calificacion_sistema_envio_entidad s
LEFT JOIN jnc.calificacion_sistema_caso c ON c.id_calificacion_sistema_caso=s.id_calificacion_sistema_caso
WHERE s.activo=1 AND s.tipo_entidad NOT IN (N'PACIENTE',N'PACIENTES');
"""

ESPERADA = """
SELECT n.id_notificacion_esperada id_origen,'ESPERADA' fuente,n.tipo_destinatario rol,
 CASE n.tipo_destinatario
 WHEN 'EPS' THEN COALESCE(c.eps,c0.eps) WHEN 'AFP' THEN COALESCE(c.afp,c0.afp)
 WHEN 'ARL' THEN COALESCE(c.arl,c0.arl) WHEN 'ASEGURADORAS' THEN COALESCE(c.compania_seguros,c0.asegurado)
 WHEN 'EMPLEADOR' THEN c.empresa_contratante
 WHEN 'REMITENTE' THEN COALESCE(c.entidad_remitente,c0.entidad_remitente)
 WHEN 'REGIONAL' THEN COALESCE(c.regional,c0.regional) END entidad,
 n.correo_normalizado correo,n.cedula_normalizada cedula,n.numero_radicado_normalizado radicado,
 COALESCE(c.fecha_audiencia,c0.hoja_trabajo_fecha_audiencia,c0.pestana_fecha) audiencia,
 COALESCE(n.id_calificacion_sistema_caso,n.id_caso) id_caso,n.fecha_envio_reportada fecha
FROM jnc.notificacion_esperada n
LEFT JOIN jnc.calificacion_sistema_caso c ON c.id_calificacion_sistema_caso=n.id_calificacion_sistema_caso AND c.activo=1
LEFT JOIN jnc.caso_calificado c0 ON c0.id_caso=n.id_caso AND c0.activo=1
WHERE n.activo=1 AND n.tipo_destinatario NOT IN (N'PACIENTE',N'PACIENTES');
"""

CERTIFICADOS = """
SELECT DISTINCT r.id_notificacion_esperada id_origen
FROM jnc.resultado_cruce_notificacion r
JOIN jnc.notificacion_correo_certificado c ON c.id_notificacion_correo=r.id_notificacion_correo_certificado_match
WHERE r.activo=1 AND r.cumple_correo=1 AND r.tipo_match_correo=N'EXACTO'
AND r.tipo_destinatario NOT IN (N'PACIENTE',N'PACIENTES');
"""

PARTES = """
SELECT DISTINCT c.id_caso,c.identificacion_paciente documento,cp.tipo_parte rol,cp.id_entidad
FROM ing.archivo a JOIN ing.carga ca ON ca.id_carga=a.id_carga_vigente AND ca.estado='PUBLICADA'
JOIN ing.fila_origen f ON f.id_carga=ca.id_carga
JOIN core.caso c ON c.id_caso=f.id_caso
JOIN ing.parte_fila p ON p.id_fila=f.id_fila
JOIN core.caso_parte cp ON cp.id_caso_parte=p.id_caso_parte
WHERE cp.tipo_parte IN ('arl','afp','eps','aseguradora','ultimoempleador') AND cp.id_entidad IS NOT NULL;
"""

MENSAJES = """
SELECT m.id_mensaje,m.asunto,m.clase_mapi clase,m.nombre_remitente remitente,
 CONVERT(date,DATEADD(HOUR,-5,m.envio_utc)) fecha,o.flags_mapi flags,
 COALESCE(s.direccion,d.direccion) correo
FROM ing.pst_contenido pc JOIN ing.pst_carga c ON c.id_carga=pc.id_carga_vigente AND c.estado='COMPLETA'
JOIN mail.mensaje_origen o ON o.id_carga=c.id_carga
JOIN mail.mensaje m ON m.id_mensaje=o.id_mensaje
LEFT JOIN mail.destinatario r ON r.id_mensaje=m.id_mensaje AND r.tipo=1
LEFT JOIN mail.direccion s ON s.id_direccion=r.id_smtp_resuelto
LEFT JOIN mail.direccion d ON d.id_direccion=r.id_direccion;
"""


def capture(progress=lambda phase: None,threshold=95.0,margin=5.0):
    started = datetime.now(timezone.utc)
    progress("LEYENDO_JNC")
    # Cada origen tiene su propia transaccion snapshot. No se simula una
    # transaccion distribuida entre servidores ni se consultan datos posteriores.
    with snapshot("JNC") as source:
        certified = {r["id_origen"] for r in rows(source, CERTIFICADOS)}
        progress("LEYENDO_CALIFICACIONES_JNC")
        data = list(rows(source, CALIFICACION))
        progress("LEYENDO_ESPERADAS_JNC")
        for row in rows(source, ESPERADA):
            row["certificado_exacto"] = row["id_origen"] in certified
            data.append(row)
    source_finished = datetime.now(timezone.utc)
    progress("LEYENDO_JRB")
    with snapshot("JRB") as source:
        entities = list(rows(source, "SELECT id_entidad,nombre FROM core.entidad;"))
        parties = list(rows(source, PARTES))
        roles=[{"id_entidad":r["id_entidad"],"tipo_parte":r["rol"]} for r in parties]
        roles.extend(rows(source,"""SELECT DISTINCT f.id_entidad_remitente AS id_entidad,'remitente' AS tipo_parte
          FROM ing.archivo a JOIN ing.carga c ON c.id_carga=a.id_carga_vigente AND c.estado='PUBLICADA'
          JOIN ing.fila_origen f ON f.id_carga=c.id_carga WHERE f.id_entidad_remitente IS NOT NULL;"""))
        messages = {}
        for row in rows(source, MENSAJES):
            mid = row["id_mensaje"]
            if mid not in messages:
                messages[mid] = {k:v for k,v in row.items() if k != "correo"}
                messages[mid]["para"] = set()
            if row["correo"]:
                messages[mid]["para"].add(row["correo"])
    catalog = Catalog(entities, started.date(),roles,threshold,margin)
    progress("RESOLVIENDO_ASOCIACIONES")
    catalog.counts["jnc_filas_no_paciente"] = len(data)
    catalog.counts["jrb_partes_vigentes"] = len(parties)
    catalog.counts["pst_mensajes_vigentes"] = len(messages)
    catalog.jnc(data)
    catalog.pst(parties, messages.values())
    payload = catalog.payload()
    return {"sha256":digest(payload), "capturado_utc":started.isoformat(),
            "fin_lectura_jnc_utc":source_finished.isoformat(),
            "fin_lectura_jrb_utc":datetime.now(timezone.utc).isoformat(), "datos":payload}
