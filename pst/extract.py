"""Extraccion de metadatos: no interpreta cumplimiento ni persiste cuerpos."""
from __future__ import annotations

import re
from datetime import datetime, timezone
from email import policy
from email.parser import Parser
from email.utils import getaddresses, parsedate_to_datetime

from .domain import PstFailure


def text(value, maximum):
    if value is None or value == "":
        return None
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="strict")
    value = str(value)
    if "\0" in value or len(value.encode("utf-16le"))//2 > maximum:
        raise PstFailure("PST_CAMPO_EXCEDE_LONGITUD")
    return value


def utc(value):
    if value is None:
        return None
    if isinstance(value, str):
        value = parsedate_to_datetime(value)
    if not isinstance(value, datetime):
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def record_values(record, wanted):
    result = {}
    for index in range(record.number_of_entries):
        entry = record.get_entry(index)
        if entry.entry_type not in wanted:
            continue
        if entry.value_type in (0x1e, 0x1f):
            result[entry.entry_type] = entry.data_as_string
        elif entry.value_type in (2, 3, 0x14):
            result[entry.entry_type] = entry.data_as_integer
        elif entry.value_type == 0x0b:
            result[entry.entry_type] = entry.data_as_boolean
        elif entry.value_type == 0x0102:
            result[entry.entry_type] = entry.data
    return result


def address(value, kind="SMTP"):
    value = text(value, 1024)
    if not value:
        return None
    kind = text(kind or "DESCONOCIDO", 16)
    if kind.upper() == "SMTP" and "@" not in value:
        kind = "DESCONOCIDO"
    return {"tipo": kind, "direccion": value}


def parse_report(headers, body):
    """DSN/MDN estructurado solamente; no buscar palabras como 'leido' en el asunto."""
    if not body:
        return []
    if isinstance(body, bytes):
        body = body.decode("utf-8", errors="replace")
    if len(body.encode("utf-8")) > 262144:
        raise PstFailure("PST_REPORTE_EXCEDE_LIMITE")
    mime = Parser(policy=policy.default).parsestr(str(headers)+"\r\n\r\n"+body)
    blocks = []
    for part in mime.walk():
        kind = {"message/delivery-status": 1, "message/disposition-notification": 2}.get(part.get_content_type())
        if kind:
            payload = part.get_payload()
            for block in payload if isinstance(payload, list) else []:
                blocks.append((kind, block))
    # Algunos exportadores entregan directamente el bloque MDN/DSN sin MIME.
    direct = Parser(policy=policy.default).parsestr(body)
    if direct.get("Final-Recipient") and (direct.get("Disposition") or direct.get("Action")):
        blocks.append((2 if direct.get("Disposition") else 1, direct))
    results = []
    for kind, block in blocks:
        if not (block.get("Final-Recipient") or block.get("Original-Recipient")):
            continue
        results.append({"tipo": kind, "destinatario_original": text(block.get("Original-Recipient"), 1024),
                        "destinatario_final": text(block.get("Final-Recipient"), 1024),
                        "accion": text(block.get("Action"), 64), "estado": text(block.get("Status"), 64),
                        "disposicion": text(block.get("Disposition"), 512),
                        "diagnostico": text(block.get("Diagnostic-Code"), 2048), "fecha_reporte_utc": None})
    return results


def extract_message(message):
    props = record_values(message.get_record_set(0), {
        0x001a, 0x0c1e, 0x0c1f, 0x5d01, 0x0064, 0x0065, 0x5d02,
        0x1035, 0x1042, 0x1046, 0x0031, 0x0e07, 0x0e08, 0x0e02, 0x0e03, 0x0e04})
    cls = text(props.get(0x001a), 255)
    if not cls:
        raise PstFailure("PST_CLASE_AUSENTE")
    if not (cls.upper().startswith("IPM.NOTE") or cls.upper().startswith("REPORT.")):
        return None
    raw = message.transport_headers or ""
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")
    if len(raw.encode("utf-8")) > 262144:
        raise PstFailure("PST_CABECERA_EXCEDE_LIMITE")
    headers = Parser(policy=policy.default).parsestr(raw, headersonly=True)
    issues, recipients, references, attachments, reports = [], [], [], [], []
    sender_smtp = props.get(0x5d01)
    sender = address(sender_smtp) if sender_smtp else address(props.get(0x0c1f), props.get(0x0c1e))
    if sender is None:
        parsed = getaddresses(headers.get_all("From", []))
        sender = address(parsed[0][1]) if parsed else None
    represented = address(props.get(0x5d02)) if props.get(0x5d02) else address(props.get(0x0065), props.get(0x0064))
    complete_recipients = False
    try:
        native = message.recipients
        if native is not None:
            for index in range(native.number_of_record_sets):
                row = record_values(native.get_record_set(index), {0x0c15, 0x3001, 0x3002, 0x3003, 0x39fe})
                kind = row.get(0x0c15)
                if kind not in (1, 2, 3):
                    issues.append("PST_DESTINATARIO_TIPO_NO_RECONOCIDO")
                    continue
                recipients.append({"tipo": kind, "fuente": 1, "ordinal": index,
                                   "direccion": address(row.get(0x3003), row.get(0x3002)),
                                   "smtp": address(row.get(0x39fe)), "nombre": text(row.get(0x3001), 512)})
            complete_recipients = not issues
    except (OSError, AttributeError):
        issues.append("PST_DESTINATARIOS_MAPI_NO_DISPONIBLES")
    # Se conservan ambas procedencias si existen; el cruce no debe sumarlas como envios.
    for field, kind, display in (("To", 1, 0x0e04), ("Cc", 2, 0x0e03), ("Bcc", 3, 0x0e02), ("Reply-To", 4, None)):
        values = headers.get_all(field, [])
        for index, (name, email) in enumerate(getaddresses(values)):
            recipients.append({"tipo": kind, "fuente": 2, "ordinal": index,
                               "direccion": address(email), "smtp": None, "nombre": text(name, 512)})
        if not values and not complete_recipients and display and props.get(display):
            recipients.append({"tipo": kind, "fuente": 3, "ordinal": 0,
                               "direccion": None, "smtp": None, "nombre": text(props[display], 512)})
    recipients = [r for r in recipients if r["direccion"] or r["smtp"] or r["nombre"]]
    if not complete_recipients:
        issues.append("PST_DESTINATARIOS_PARCIALES")
    for kind, value in ((1, props.get(0x1042) or headers.get("In-Reply-To")),
                        (2, props.get(0x1046) or headers.get("Original-Message-ID"))):
        if value:
            references.append({"tipo": kind, "ordinal": 0, "texto": text(value, 2048), "binario": None})
    for index, value in enumerate(re.findall(r"<[^<>]+>", str(headers.get("References", "")))):
        references.append({"tipo": 3, "ordinal": index, "texto": text(value, 2048), "binario": None})
    if props.get(0x0031):
        value = bytes(props[0x0031])
        if len(value) > 4096:
            raise PstFailure("PST_REFERENCIA_EXCEDE_LIMITE")
        references.append({"tipo": 4, "ordinal": 0, "texto": None, "binario": value})
    count = message.number_of_attachments
    for index in range(count):
        obj = message.get_attachment(index)
        values = record_values(obj.get_record_set(0), {0x3704, 0x3707, 0x370e, 0x3705, 0x3712, 0x7ffe})
        attachments.append({"ordinal": index, "nombre": text(values.get(0x3707) or values.get(0x3704), 1024),
                            "tamano": obj.size, "mime": text(values.get(0x370e), 255),
                            "metodo": values.get(0x3705), "content_id": text(values.get(0x3712), 1024),
                            "oculto": values.get(0x7ffe)})
    if cls.upper().startswith("REPORT."):
        # pypff no expone longitud del cuerpo: solo leer reportes cuyo tamano MAPI sea pequeno.
        for index in range(message.get_record_set(0).number_of_entries):
            entry = message.get_record_set(0).get_entry(index)
            if entry.entry_type == 0x1000:
                size = props.get(0x0e08)
                if isinstance(size, int) and 0 <= size <= 262144:
                    reports.extend(parse_report(headers, entry.data_as_string))
                else:
                    issues.append("PST_REPORTE_CUERPO_NO_EXTRAIDO")
                break
        # Los destinatarios del propio reporte no son los destinatarios afectados del envio original.
    return {"identificador": int(message.identifier), "clase": cls,
            "message_id": text(props.get(0x1035) or headers.get("Message-ID"), 2048),
            "asunto": text(message.subject, 2048), "remitente": sender, "representado": represented,
            "nombre_remitente": text(message.sender_name, 512), "envio": utc(message.client_submit_time),
            "recepcion": utc(message.delivery_time), "flags": props.get(0x0e07), "tamano": props.get(0x0e08),
            "destinatarios": recipients, "adjuntos": attachments, "referencias": references, "reportes": reports,
            "destinatarios_completos": complete_recipients, "adjuntos_reportados": count,
            "incidencias": sorted(set(issues))}


def folder_at(file, path):
    folder = file.root_folder
    for index in path:
        folder = folder.get_sub_folder(index)
    return folder


def inventory(file):
    found, seen = [], set()
    def visit(folder, path, parent):
        identifier = int(folder.identifier)
        if identifier in seen or len(path) > 64 or len(found) >= 100000:
            raise PstFailure("PST_ARBOL_INVALIDO")
        seen.add(identifier)
        found.append({"identificador": identifier, "padre": parent, "nombre": text(folder.name, 512) or "",
                      "ruta": path, "elementos": folder.number_of_sub_messages})
        for index in range(folder.number_of_sub_folders):
            visit(folder.get_sub_folder(index), path+[index], identifier)
    visit(file.root_folder, [], None)
    return found
