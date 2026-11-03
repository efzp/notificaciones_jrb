"""Agrupacion operativa conservadora. Preserva los IDs y nombres originales."""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from functools import lru_cache

from rapidfuzz import fuzz, process

NORMALIZATION_VERSION = "regex-proximidad-v1"
DEFAULT_THRESHOLD = 95.0
DEFAULT_MARGIN = 5.0
REVIEW_THRESHOLD = 85.0


def plain(value):
    import unicodedata
    value=unicodedata.normalize("NFKD",str(value or ""))
    value="".join(c for c in value if not unicodedata.combining(c)).upper()
    return " ".join(re.sub(r"[^A-Z0-9]"," ",value).split())


@dataclass(frozen=True)
class Name:
    raw: str
    signature: str
    ordered: str
    legal: frozenset
    hints: frozenset
    numbers: frozenset
    qualifiers: frozenset


@lru_cache(maxsize=40000)
def normalize(value,role):
    text=plain(value)
    # Reglas de formato/expansion, sin nombres de empresas particulares.
    for pattern,replacement in (
        (r"\bS\s+A\s+S\b","SAS"),(r"\bS\s+A\b","SA"),
        (r"\bSOCIEDAD POR ACCIONES SIMPLIFICADA\b","SAS"),
        (r"\bSOCIEDAD ANONIMA\b","SA"),(r"\bLIMITADA\b","LTDA"),
        (r"\bC\s+I\s+A\b|\bCIA\b","COMPANIA"),
        (r"\bE\s+P\s+S\b","EPS"),(r"\bA\s+R\s+L\b","ARL"),
        (r"\bA\s+F\s+P\b","AFP"),(r"\bI\s+P\s+S\b","IPS")):
        text=re.sub(pattern,replacement,text)
    legal=frozenset(re.findall(r"\b(?:SA|SAS|LTDA|ESE|ESAL|SCA)\b",text))
    hints=frozenset(re.findall(r"\b(?:EPS|ARL|AFP|IPS)\b",text))
    numbers=frozenset(re.findall(r"\b\d+\b",text))
    qualifiers=frozenset(re.findall(r"\b(?:VIDA|GENERALES|COMERCIALES|COMERCIAL|GRUPO|HOLDING|FUNDACION|COOPERATIVA|TEMPORALES|INTEGRAL)\b",text))
    text=re.sub(r"\b(?:SA|SAS|LTDA|ESE|ESAL|SCA)\b"," ",text)
    text=re.sub(r"\b(?:EMPRESA|ENTIDAD) PROMOTORA DE SALUD\b"," ",text)
    text=re.sub(r"\bPROMOTORA DE SALUD\b"," ",text)
    text=re.sub(r"\bADMINISTRADORA DE (?:FONDOS DE )?PENSIONES(?: Y CESANTIAS)?\b"," ",text)
    text=re.sub(r"\bADMINISTRADORA DE RIESGOS (?:LABORALES|PROFESIONALES)\b"," ",text)
    text=re.sub(r"\b(?:EPS|ARL|AFP|IPS|ASEGURADORA|ASEGURADORAS)\b"," ",text)
    text=re.sub(r"\bCOMPANIA(?: DE)?\b"," ",text)
    if role in {"arl","aseguradora"}:
        text=re.sub(r"\bSEGUROS\b"," ",text)
    text=re.sub(r"\b(?:DE|DEL|LA|EL|LOS|LAS|Y|EN|POR|PARA)\b"," ",text)
    ordered=" ".join(text.split())
    return Name(plain(value)," ".join(sorted(ordered.split())),ordered,legal,hints,numbers,qualifiers)


def compatible(a,b):
    return not ((a.legal and b.legal and a.legal!=b.legal)
                or (a.hints and b.hints and a.hints.isdisjoint(b.hints))
                or (a.numbers and b.numbers and a.numbers!=b.numbers)
                or a.qualifiers!=b.qualifiers)


def score(a,b):
    if not a.signature or not b.signature or not compatible(a,b):return 0.0
    if a.signature==b.signature:return 100.0
    return round(.9*fuzz.ratio(a.signature,b.signature)+.1*fuzz.ratio(a.ordered,b.ordered),2)


def fuzzy_allowed(name):
    generic={"SERVICIOS","SERVICIO","COLOMBIA","EMPRESA","INVERSIONES","INGENIERIA","CONSTRUCCIONES","SOLUCIONES"}
    informative=[t for t in name.ordered.split() if len(t)>=4 and t not in generic and not t.isdigit()]
    return len(name.signature)>=8 and (len(informative)>=2 or any(len(t)>=6 for t in informative))


class EntityMatcher:
    def __init__(self,entities,roles=None,threshold=DEFAULT_THRESHOLD,margin=DEFAULT_MARGIN):
        if not 90<=threshold<=100 or not 0<margin<=20:raise ValueError("UMBRAL_INVALIDO")
        self.entities={int(e["id_entidad"]):e["nombre"] for e in entities}
        self.threshold=float(threshold);self.margin=float(margin)
        self.clusters={};self.canonical={};self.audit={};self.cache={}
        all_roles={"arl","afp","eps","aseguradora","ultimoempleador","remitente","regional"}
        eligible=defaultdict(set)
        if roles is None:
            for role in all_roles:eligible[role].update(self.entities)
        else:
            for row in roles:eligible[row["tipo_parte"]].add(int(row["id_entidad"]))
            # Regionales pueden figurar en el maestro sin tener rol propio en Sabana.
            eligible["regional"].update(e for e,n in self.entities.items() if "JUNTA" in plain(n) or "REGIONAL" in plain(n))
        for role in sorted(all_roles):
            self._build(role,sorted(eligible[role]))

    def _build(self,role,ids):
        names={eid:normalize(self.entities[eid],role) for eid in ids}
        for eid in list(names):
            if not names[eid].signature:
                self.canonical[(role,eid)]=None
                self._record(role,"JRB",self.entities[eid],eid,None,"SIN_EQUIVALENCIA",0.0,0.0)
                del names[eid]
        buckets=defaultdict(list)
        for eid,name in names.items():buckets[name.signature].append(eid)
        groups={}
        conflicted=set()
        # Agrupacion regex con verificacion completa: un alias sin forma legal
        # no puede servir de puente entre SA y SAS.
        for signature,members in sorted(buckets.items()):
            forms={names[e].legal for e in members if names[e].legal}
            if len(forms)>1:conflicted.add(signature)
            candidates=[]
            for eid in members:
                target=None
                for gid in candidates:
                    if len(forms)>1 and any(names[e].legal!=names[eid].legal for e in groups[gid]):continue
                    if all(compatible(names[eid],names[e]) for e in groups[gid]):target=gid;break
                if target is None:groups[eid]=[eid];candidates.append(eid)
                else:groups[target].append(eid)
        # Segunda fase: enlace completo, no componentes transitivos.
        merged={};signatures={}
        for gid,members in sorted(groups.items()):
            name=names[members[0]]
            ranked=[]
            if fuzzy_allowed(name):
                for _,base,target in process.extract(name.signature,signatures,scorer=fuzz.ratio,score_cutoff=80,limit=None):
                    if name.signature in conflicted and any(names[a].legal!=names[b].legal for a in members for b in merged[target]):continue
                    minimum=min(score(names[a],names[b]) for a in members for b in merged[target])
                    if minimum>=REVIEW_THRESHOLD:ranked.append((minimum,target))
            ranked.sort(key=lambda x:(-x[0],x[1]))
            if ranked and ranked[0][0]>=self.threshold and (len(ranked)==1 or ranked[0][0]-ranked[1][0]>=self.margin):
                target=ranked[0][1];merged[target].extend(members)
            else:
                merged[gid]=members[:];signatures[gid]=name.signature
        self.clusters[role]={gid:[names[e] for e in members] for gid,members in merged.items()}
        for gid,members in merged.items():
            representative=min(members)
            for eid in members:
                self.canonical[(role,eid)]=representative
                minimum=min(score(names[eid],names[other]) for other in members)
                method="EXACTA" if eid==representative else ("REGEX" if names[eid].signature==names[representative].signature else "PROXIMIDAD")
                self._record(role,"JRB",self.entities[eid],eid,representative,method,minimum,0.0)
        self.clusters[role]={min(members):[names[e] for e in members] for members in merged.values()}
        self.signatures=getattr(self,"signatures",{})
        self.signatures[role]={gid:members[0].signature for gid,members in self.clusters[role].items()}

    def _record(self,role,origin,name,source_id,target,method,best,second,candidate=None):
        k=(origin,role,source_id or 0,plain(name))
        self.audit[k]={"origen":origin,"tipo_parte":role,"nombre_origen":str(name)[:500],
                       "id_entidad_origen":source_id,"id_entidad_representante":target,
                       "id_entidad_candidata":candidate if candidate is not None else target,
                       "metodo":method,"puntaje":round(best,2),"segundo_puntaje":round(second,2)}

    def resolve(self,name,role):
        cachekey=(role,plain(name))
        if cachekey in self.cache:
            record=self.audit[("JNC",role,0,plain(name))]
            record["nombre_origen"]=min(record["nombre_origen"],str(name)[:500])
            return self.cache[cachekey]
        query=normalize(name,role);ranked=[]
        if query.signature:
            for _,_,gid in process.extract(query.signature,self.signatures.get(role,{}),scorer=fuzz.ratio,score_cutoff=80,limit=None):
                members=self.clusters[role][gid]
                minimum=min(score(query,m) for m in members)
                if minimum>=REVIEW_THRESHOLD:
                    regex=all(query.signature==m.signature for m in members)
                    exact=any(query.raw==m.raw for m in members)
                    if exact or regex or fuzzy_allowed(query):ranked.append((minimum,gid,exact,regex))
        ranked.sort(key=lambda x:(-x[0],x[1]));best=ranked[0] if ranked else (0.0,None,False,False)
        second=ranked[1][0] if len(ranked)>1 else 0.0
        accepted=best[0]>=self.threshold and (best[0]-second>=self.margin)
        target=best[1] if accepted else None
        method=("EXACTA" if best[2] else "REGEX" if best[3] else "PROXIMIDAD") if accepted else ("AMBIGUA" if best[0]>=self.threshold else "REVISION" if best[0]>=REVIEW_THRESHOLD else "SIN_EQUIVALENCIA")
        self._record(role,"JNC",name,None,target,method,best[0],second,best[1])
        result={"id_entidad":target,"metodo":method,"puntaje":best[0],"segundo_puntaje":second,
                "id_candidato":best[1]}
        self.cache[cachekey]=result
        return result

    def representative(self,eid,role):return self.canonical.get((role,eid),eid)

    def export(self):
        return {"version":NORMALIZATION_VERSION,"umbral":self.threshold,"margen":self.margin,
                "umbral_revision":REVIEW_THRESHOLD,"equivalencias":[self.audit[k] for k in sorted(self.audit)]}
