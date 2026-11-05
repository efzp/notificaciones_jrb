"""Leer solo identificacion, procedencia y contactos. No guardar datos clinicos."""
from __future__ import annotations

import io
import math
import zipfile
from collections import Counter
from datetime import date, datetime

from openpyxl import load_workbook
from openpyxl.utils.datetime import from_excel

from terceros.domain import emails, key
from .domain import CanopusError, limit

REQUIRED = {"IDENTIFICACION", "CORREO ELECTRONICO", "ULTIMO EMPLEADOR", "EMAIL EMPRESA"}
OPTIONAL = {"FECHA AUDIENCIA", "REPARTO", "CORREO ELECTRONICOFE"}


def text(value, size):
    if value is None:
        return None
    if isinstance(value, (bool, datetime, date)):
        raise CanopusError("CANOPUS_TEXTO_INVALIDO")
    value = str(value).strip()
    if len(value) > size or any(ord(c) < 32 and c not in "\t\r\n" for c in value):
        raise CanopusError("CANOPUS_TEXTO_INVALIDO")
    return value or None


def identification(value):
    if isinstance(value, float):
        if not math.isfinite(value) or not value.is_integer() or abs(value) >= 10**15:
            raise CanopusError("CANOPUS_CEDULA_INVALIDA")
        value = int(value)
    if isinstance(value, int) and (isinstance(value, bool) or value < 0 or value >= 10**15):
        raise CanopusError("CANOPUS_CEDULA_INVALIDA")
    result = text(value, 50)
    # No quitar ceros iniciales, separadores ni reconstruir numeros redondeados.
    if not result or not result.isascii() or not result.isdigit():
        raise CanopusError("CANOPUS_CEDULA_INVALIDA")
    return result


def reported_date(value, epoch):
    if value is None or value == "":
        return None, False
    if isinstance(value, datetime):
        return value.date(), False
    if isinstance(value, date):
        return value, False
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            converted = from_excel(value, epoch)
            return (converted.date(), False) if isinstance(converted, datetime) else (None, True)
        except (ValueError, OverflowError):
            return None, True
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(str(value).strip(), fmt).date(), False
        except ValueError:
            pass
    return None, True


def contacts(field, role, value, employer_id):
    addresses, issues = emails(value)
    code = ("CANOPUS_FE_POR_DEFINIR" if role == "por_definir" else
            "CANOPUS_EMPLEADOR_SIN_ASOCIAR" if role == "ultimoempleador" and employer_id is None else None)
    found = [dict(campo=field, rol=role, correo=address,
                  estado="REVISION" if code else "UTILIZABLE", valor=None, codigo=code) for address in addresses]
    if issues:
        # Una incidencia adicional conserva el fragmento original incluso si habia direcciones validas.
        raw = str(value).strip()
        found.append(dict(campo=field, rol=role, correo=None, estado="REVISION", valor=raw[:1024],
                          codigo="CANOPUS_CORREO_TRUNCADO" if len(raw) > 1024 else "CANOPUS_CORREO_INVALIDO"))
    return found


def parse(content, matcher=None):
    maximum_rows = limit("CANOPUS_MAX_ROWS", 50000, 100000)
    maximum_contacts = limit("CANOPUS_MAX_CONTACTS", 200000, 400000)
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            entries = archive.infolist()
            if (len(entries) > 2000 or sum(e.file_size for e in entries) > 64 * 1024 * 1024
                    or len({e.filename for e in entries}) != len(entries)
                    or any(e.flag_bits & 1 or "vbaproject" in e.filename.lower() for e in entries)):
                raise CanopusError("CANOPUS_XLSX_LIMITE")
            if "xl/workbook.xml" not in archive.namelist():
                raise CanopusError("CANOPUS_XLSX_INVALIDO")
        workbook = load_workbook(io.BytesIO(content), read_only=True, data_only=False, keep_links=False)
    except CanopusError:
        raise
    except Exception:
        raise CanopusError("CANOPUS_XLSX_INVALIDO") from None
    result, counts = [], Counter()
    try:
        for sheet in workbook.worksheets:
            if len(sheet.title) > 128:
                raise CanopusError("CANOPUS_HOJA_INVALIDA")
            # No confiar en dimensiones declaradas que pueden omitir filas.
            sheet.reset_dimensions()
            rows = sheet.iter_rows()
            header = next(rows, ())
            if not header:
                continue
            names = [key(c.value) for c in header]
            if not any(names):
                for number, row in enumerate(rows, 2):
                    if number > maximum_rows + 1:
                        raise CanopusError("CANOPUS_FILAS_LIMITE")
                    if any(c.value is not None for c in row):
                        raise CanopusError("CANOPUS_ENCABEZADO_INVALIDO")
                continue
            if not REQUIRED.issubset(names) or any(names.count(n) != 1 for n in REQUIRED | (OPTIONAL & set(names))):
                raise CanopusError("CANOPUS_ENCABEZADO_INVALIDO")
            columns = {n: names.index(n) for n in REQUIRED | (OPTIONAL & set(names))}
            for number, row in enumerate(rows, 2):
                if number > maximum_rows + 1:
                    raise CanopusError("CANOPUS_FILAS_LIMITE")
                if not any(c.value is not None for c in row):
                    continue
                if len(result) >= maximum_rows:
                    raise CanopusError("CANOPUS_FILAS_LIMITE")
                values = {}
                for name, column in columns.items():
                    cell = row[column] if column < len(row) else None
                    if cell is not None and cell.data_type in {"f", "e"}:
                        raise CanopusError("CANOPUS_FORMULA_O_ERROR_EN_ENTRADA")
                    values[name] = cell.value if cell is not None else None
                employer = text(values["ULTIMO EMPLEADOR"], 500)
                entity, state = None, "NO_APLICA" if not employer or key(employer) in {"NO APLICA", "N A", "NULL"} else "SIN_ENTIDAD"
                if state != "NO_APLICA" and matcher is not None:
                    match = matcher.resolve(employer, "ultimoempleador")
                    entity = match["id_entidad"]
                    state = "ASOCIADO" if entity is not None else "AMBIGUO" if match["metodo"] == "AMBIGUA" else "SIN_ENTIDAD"
                when, bad_date = reported_date(values.get("FECHA AUDIENCIA"), workbook.epoch)
                item = dict(hoja=sheet.title, numero=number, cedula=identification(values["IDENTIFICACION"]),
                            fecha=when, reparto=text(values.get("REPARTO"), 100), empleador=employer,
                            entidad=entity, estado_empleador=state, contactos=[])
                for column, role, field in (("CORREO ELECTRONICO", "paciente", "correo_electronico"),
                                            ("EMAIL EMPRESA", "ultimoempleador", "email_empresa"),
                                            ("CORREO ELECTRONICOFE", "por_definir", "correo_electronicofe")):
                    item["contactos"].extend(contacts(field, role, values.get(column), entity))
                counts["filas"] += 1
                counts["fechas_revision"] += int(bad_date)
                counts["empleadores_" + state.lower()] += 1
                counts["contactos"] += len(item["contactos"])
                counts["contactos_revision"] += sum(c["estado"] == "REVISION" for c in item["contactos"])
                if counts["contactos"] > maximum_contacts:
                    raise CanopusError("CANOPUS_CONTACTOS_LIMITE")
                result.append(item)
        if not result:
            raise CanopusError("CANOPUS_SIN_FILAS")
        return result, dict(counts)
    except CanopusError:
        raise
    except Exception:
        raise CanopusError("CANOPUS_XLSX_INVALIDO") from None
    finally:
        workbook.close()
