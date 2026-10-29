"""Valida Excel locales sin conectar SQL ni escribir sus valores en el informe."""
import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sabana.domain import AppError, Profile
from sabana.excel import parse_workbook

parser = argparse.ArgumentParser()
parser.add_argument("--inputs", type=Path, required=True)
parser.add_argument("--profile", type=Path, default=Path(__file__).resolve().parents[1] / "profile.example.json")
parser.add_argument("--output", type=Path, required=True)
args = parser.parse_args()
profile = Profile.from_json(json.loads(args.profile.read_text(encoding="utf-8")))
started = time.monotonic()
result = {"archivos": 0, "filas_detectadas": 0, "filas_interpretadas": 0, "archivos_bloqueados": 0,
          "por_codigo": Counter(), "inferencias": [], "bloqueos": []}
cases, insurers = set(), {}
recovered = Counter()
for path in sorted(args.inputs.rglob("*")):
    if not path.is_file() or path.suffix.lower() not in {".xlsx", ".xlsm"}:
        continue
    result["archivos"] += 1
    try:
        parsed = parse_workbook(path.read_bytes(), profile)
    except AppError as error:
        result["archivos_bloqueados"] += 1
        result["por_codigo"][error.code] += 1
        result["bloqueos"].append({"archivo": path.name, "codigo": error.code})
        continue
    result["filas_detectadas"] += parsed.total_rows
    result["filas_interpretadas"] += len(parsed.rows)
    result["por_codigo"].update(i.codigo for i in parsed.issues)
    for row in parsed.rows:
        values = row.values
        key = (path.parent.name, values["fecha_audiencia"], values["reparto_paciente"], values["identificacion_paciente"])
        cases.add(key)
        if values["aseguradora"] is not None:
            insurers.setdefault(key, set()).add(values["aseguradora"])
        for inference in parsed.structure["inferencias"]:
            if values[inference["campo"]] is not None:
                recovered[inference["campo"]] += 1
    if parsed.blocked:
        result["archivos_bloqueados"] += 1
        result["bloqueos"].append({"archivo": path.name, "errores": [i.public() for i in parsed.issues if i.severidad == "E"]})
    if parsed.structure["inferencias"]:
        result["inferencias"].append({"archivo": path.name, "mapping": parsed.structure["inferencias"]})
result["segundos"] = round(time.monotonic() - started, 3)
result["casos_unicos"] = len(cases)
result["casos_con_dos_aseguradoras"] = sum(len(names) == 2 for names in insurers.values())
result["valores_recuperados_por_campo"] = recovered
args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(result, ensure_ascii=False))
