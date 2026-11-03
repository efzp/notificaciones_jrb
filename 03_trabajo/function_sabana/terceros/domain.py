from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections import defaultdict
from datetime import date, datetime
from email.utils import getaddresses

VERSION = "terceros-v2"
ROLES = {"ARL": "arl", "AFP": "afp", "EPS": "eps", "ASEGURADORA": "aseguradora",
         "ASEGURADORAS": "aseguradora", "COMPANIA SEGUROS": "aseguradora",
         "EMPLEADOR": "ultimoempleador", "ULTIMOEMPLEADOR": "ultimoempleador",
         "REMITENTE": "remitente", "REGIONAL": "regional"}


def key(value):
    value = unicodedata.normalize("NFKD", str(value or ""))
    value = "".join(c for c in value if not unicodedata.combining(c)).upper()
    return " ".join(re.sub(r"[^A-Z0-9]", " ", value).split())


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=True, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


def emails(value):
    """Separar listas; preservar local-part y normalizar solo dominio. No DNS."""
    raw = str(value or "").strip()
    if not raw or raw.upper() in {"NULL", "NO APLICA", "N/A"}:
        return [], []
    if len(raw) > 4000 or any(ord(c) < 32 and c not in "\t\r\n" for c in raw):
        return [], ["CORREO_INVALIDO"]
    raw = re.sub(r"(?i)mailto:", "", raw).replace(";", ",").replace("\r", ",").replace("\n", ",")
    # Algunos archivos separan direcciones sin nombre por espacios.
    raw = re.sub(r"(?<=[\w.])\s+(?=[\w.+-]+@)", ",", raw)
    addresses = getaddresses([raw])
    found, issues = set(), []
    for _, address in addresses:
        address = address.strip().strip("'")
        if not address:
            continue
        if address.count("@") != 1:
            issues.append("CORREO_INVALIDO")
            continue
        local, domain = address.rsplit("@", 1)
        try:
            domain = domain.encode("idna").decode("ascii").lower()
        except UnicodeError:
            issues.append("CORREO_INVALIDO")
            continue
        # Conservar formatos inusuales para revision, sin adivinar correcciones.
        if (not re.fullmatch(r"[A-Za-z0-9!#$%&'*+/=?^_`{|}~.-]+", local)
                or len(local) > 64 or local.startswith(".") or local.endswith(".") or ".." in local
                or len(domain) > 253 or "." not in domain
                or any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
                       for label in domain.split("."))):
            issues.append("CORREO_INVALIDO")
            continue
        found.add(local+"@"+domain)
    if not found:
        issues.append("CORREO_INVALIDO")
    return sorted(found), sorted(set(issues))


def event_date(value, maximum):
    if value is None:
        return None, None
    try:
        parsed = date.fromisoformat(str(value)[:10])
    except ValueError:
        return None, "FECHA_INVALIDA"
    if parsed < date(2000, 1, 1) or parsed > maximum:
        return None, "FECHA_FUERA_RANGO"
    return parsed.isoformat(), None


def identifiers(subject):
    # No conservar cedulas en la instantanea de terceros; solo se usan en memoria.
    return set(re.findall(r"(?<!\d)\d{5,15}(?!\d)", str(subject or "")))


def subject_role(subject):
    text = key(subject)
    if text.startswith(("RE ", "RESPUESTA ")):
        return None
    while text.startswith(("RV ", "FW ", "FWD ")):
        text = text.split(" ",1)[1]
    if text.startswith(("NOTIFICACION PACIENTE ", "NOTIFICACION AL PACIENTE ")):
        return None
    roles = set()
    for word, role in (("EMPLEADOR", "ultimoempleador"), ("ARL", "arl"), ("AFP", "afp"),
                       ("EPS", "eps"), ("ASEGURADORA", "aseguradora"), ("REGIONAL", "regional")):
        if re.search(r"\b"+word+r"S?\b", text):
            roles.add(role)
    return next(iter(roles)) if len(roles) == 1 else None


class Catalog:
    def __init__(self, entities, maximum, roles=None, threshold=95.0, margin=5.0):
        from .matching import EntityMatcher
        self.entities = {int(e["id_entidad"]): e["nombre"] for e in entities}
        self.matcher = EntityMatcher(entities,roles,threshold,margin)
        self.maximum = maximum
        self.groups = {}
        self.pending = {}
        self.counts = defaultdict(int)

    def issue(self, origin, role, name, email, code, reference):
        value = {"origen": origin, "tipo_parte": role, "nombre_origen": str(name or "")[:500],
                 "correo_origen": str(email or "")[:1024], "codigo": code,
                 "referencia": str(reference)[:200]}
        # Agrupar incidencias iguales; preservar conteo y una referencia + hash del conjunto.
        k = (origin, role, value["nombre_origen"], value["correo_origen"], code)
        if k not in self.pending:
            self.pending[k] = dict(value, ocurrencias=0, referencias=set())
        self.pending[k]["ocurrencias"] += 1
        self.pending[k]["referencias"].add(str(reference))

    def add(self, eid, role, email, origin, case, reference, when=None, source="", certified=False):
        assert role in set(ROLES.values()) and eid in self.entities
        k = (eid, role, email, origin)
        if k not in self.groups:
            self.groups[k] = {"cases": set(), "observations": set(), "dates": set(),
                              "sources": defaultdict(set), "certified": set()}
        g = self.groups[k]
        g["cases"].add(case)
        g["observations"].add(str(reference))
        g["sources"][source].add(str(reference))
        if certified:
            g["certified"].add(case)
        valid, problem = event_date(when, self.maximum)
        if valid:
            g["dates"].add(valid)
        if problem:
            self.issue(origin, role, self.entities[eid], email, problem, reference)

    def jnc(self, rows):
        for row in rows:
            role = ROLES.get(key(row.get("rol")))
            if role is None:
                self.counts["roles_excluidos"] += 1
                continue
            name = row.get("entidad") or ""
            reference = row["fuente"]+":"+str(row["id_origen"])
            addresses, issues = emails(row.get("correo"))
            for problem in issues:
                self.issue("JNC", role, name, row.get("correo"), problem, reference)
            if not addresses:
                self.counts["jnc_sin_correo_utilizable"] += 1
                continue
            match = self.matcher.resolve(name,role)
            if match["id_entidad"] is None:
                for address in addresses:
                    self.issue("JNC", role, name, address,
                               "ENTIDAD_AMBIGUA" if match["metodo"]=="AMBIGUA" else
                               "ENTIDAD_PROXIMIDAD_REVISION" if match["metodo"]=="REVISION" else "ENTIDAD_SIN_EQUIVALENCIA", reference)
                continue
            eid = match["id_entidad"]
            # Clave de caso comun entre las fuentes JNC; no persiste datos de pacientes.
            parts = [str(row.get(n) or "").strip() for n in ("cedula", "radicado", "audiencia")]
            if not parts[0] or not parts[1]:
                for address in addresses:
                    self.issue("JNC", role, name, address, "CASO_JNC_SIN_IDENTIDAD", reference)
                continue
            case = digest(parts)
            for address in addresses:
                self.add(eid, role, address, "JNC", case, reference, row.get("fecha"),
                         row["fuente"], bool(row.get("certificado_exacto")))

    def pst(self, cases, messages):
        by_document = defaultdict(list)
        for case in cases:
            mapped = dict(case,id_entidad=self.matcher.representative(case["id_entidad"],case["rol"]))
            if mapped["id_entidad"] is None:
                self.counts["pst_entidades_sin_nombre"]+=1
                continue
            by_document[str(case["documento"]).strip()].append(mapped)
        for message in messages:
            if not str(message.get("clase") or "").upper().startswith("IPM.NOTE"):
                self.counts["pst_reportes_excluidos"] += 1
                continue
            if not re.fullmatch(r"NOTIFICACIONES\s+SALA\s*\d+", key(message.get("remitente"))):
                self.counts["pst_emisor_no_resuelto"] += 1
                continue
            if message.get("flags") is not None and message["flags"] & 8:
                continue  # Borrador no enviado.
            role = subject_role(message["asunto"])
            if role is None:
                self.counts["pst_rol_no_resuelto"] += 1
                continue
            candidates = []
            for document in identifiers(message["asunto"]):
                candidates.extend(c for c in by_document.get(document, []) if c["rol"] == role)
            # Un paciente con varias audiencias o varias entidades del mismo rol es ambiguo.
            choices = {(c["id_caso"], c["id_entidad"]) for c in candidates}
            addresses = set()
            for value in message["para"]:
                found, problems = emails(value)
                addresses.update(found)
                for problem in problems:
                    self.issue("JRB_PST", role, "", value, problem, message["id_mensaje"])
            if len(choices) != 1:
                for address in addresses:
                    self.issue("JRB_PST", role, "", address,
                               "CASO_ENTIDAD_AMBIGUO" if choices else "CASO_SIN_MATCH", message["id_mensaje"])
                continue
            case_id, eid = next(iter(choices))
            for address in sorted(addresses):
                self.add(eid, role, address, "JRB_PST", str(case_id), str(message["id_mensaje"]),
                         message.get("fecha"), "PST_ASUNTO_ROL_EMISOR_NOMBRE")

    def payload(self):
        groups = []
        totals = defaultdict(set)
        for (eid, role, email, origin), g in self.groups.items():
            totals[(eid, role, origin)].update(g["cases"])
        for (eid, role, email, origin), g in sorted(self.groups.items()):
            groups.append({"id_entidad": eid, "tipo_parte": role, "correo": email, "origen": origin,
                           "casos_distintos": len(g["cases"]), "observaciones": len(g["observations"]),
                           "casos_entidad_con_correo": len(totals[(eid, role, origin)]),
                           "casos_certificado_exacto": len(g["certified"]),
                           "primera_observacion": min(g["dates"]) if g["dates"] else None,
                           "ultima_observacion": max(g["dates"]) if g["dates"] else None,
                           "hash_soporte": digest({k:sorted(v) for k,v in sorted(g["sources"].items())}),
                           "referencia_muestra": min(source+":"+reference for source,refs in g["sources"].items() for reference in refs),
                           "fuentes": {k:len(v) for k,v in sorted(g["sources"].items())}})
        pending = []
        for _, value in sorted(self.pending.items()):
            v = {k:x for k,x in value.items() if k != "referencias"}
            v["referencia"] = min(value["referencias"])
            v["hash_soporte"] = digest(sorted(value["referencias"]))
            pending.append(v)
        return {"version": VERSION, "fecha_maxima": self.maximum.isoformat(),
                "normalizacion":self.matcher.export(),
                "entidades": [{"id_entidad":k,"nombre":v} for k,v in sorted(self.entities.items())],
                "asociaciones": groups, "pendientes": pending, "conteos": dict(sorted(self.counts.items()))}


def validate_snapshot(document):
    payload = document["datos"]
    if payload.get("version") not in {"terceros-v1",VERSION} or digest(payload) != document.get("sha256"):
        raise ValueError("INSTANTANEA_INVALIDA")
    allowed = set(ROLES.values())
    seen = set()
    for row in payload["asociaciones"]:
        k = (row["id_entidad"], row["tipo_parte"], row["correo"], row["origen"])
        addresses, problems = emails(row["correo"])
        if (row["tipo_parte"] not in allowed or k in seen or row["origen"] not in {"JNC", "JRB_PST"}
                or addresses != [row["correo"]] or problems or row["casos_distintos"] <= 0
                or row["observaciones"] < row["casos_distintos"]
                or row["casos_entidad_con_correo"] < row["casos_distintos"]
                or not 0 <= row["casos_certificado_exacto"] <= row["casos_distintos"]):
            raise ValueError("ASOCIACION_INVALIDA")
        seen.add(k)
    for row in payload["pendientes"]:
        if row["tipo_parte"] not in allowed or row["origen"] not in {"JNC","JRB_PST"} or row["ocurrencias"] <= 0:
            raise ValueError("PENDIENTE_INVALIDO")
    if payload["version"]==VERSION:
        normal=payload["normalizacion"]
        if not 90<=normal["umbral"]<=100 or not 0<normal["margen"]<=20:
            raise ValueError("UMBRAL_INVALIDO")
        for row in normal["equivalencias"]:
            if row["tipo_parte"] not in allowed or row["origen"] not in {"JNC","JRB"}:
                raise ValueError("EQUIVALENCIA_INVALIDA")
            if (not 0<=row["puntaje"]<=100 or not 0<=row["segundo_puntaje"]<=100
                or row["metodo"] not in {"EXACTA","REGEX","PROXIMIDAD","REVISION","AMBIGUA","SIN_EQUIVALENCIA"}):
                raise ValueError("EQUIVALENCIA_INVALIDA")
            accepted=row["metodo"] in {"EXACTA","REGEX","PROXIMIDAD"}
            if accepted != (row["id_entidad_representante"] is not None) or (accepted and row["puntaje"]<normal["umbral"]):
                raise ValueError("EQUIVALENCIA_INVALIDA")
    return payload
