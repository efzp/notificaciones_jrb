import collections
import hashlib
import json
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[2]
project = root / '03_trabajo/function_sabana'
sys.path.insert(0, str(project / '.validation/lectura_multifila'))
sys.path.insert(0, str(project))
from sabana.domain import Profile
from sabana.excel import parse_workbook

profile = Profile.from_json(json.loads((project / 'profile.example.json').read_text(encoding='utf-8-sig')))
items = json.loads((root / '03_trabajo/analisis_estructura_sabana/inventario_sharepoint.json').read_text(encoding='utf-8-sig'))
candidates = []
for item in items:
    source = root / '03_trabajo/analisis_estructura_sabana/inputs' / item['sala'] / item['name']
    if not source.exists():
        continue
    parsed = parse_workbook(source.read_bytes(), profile)
    if parsed.blocked or parsed.issues or not 20 <= len(parsed.rows) <= 150:
        continue
    insurers = collections.defaultdict(set)
    cases = set()
    for row in parsed.rows:
        values = row.values
        key = (values['fecha_audiencia'], values['reparto_paciente'], values['identificacion_paciente'])
        cases.add(key)
        if values['aseguradora']:
            insurers[key].add(values['aseguradora'])
    doubles = sum(len(values) == 2 for values in insurers.values())
    if doubles:
        candidates.append(dict(metadata=item, filas=len(parsed.rows), casos=len(cases), casos_dos_aseguradoras=doubles))

selected = min(candidates, key=lambda candidate: (abs(candidate['filas'] - 60), -candidate['casos_dos_aseguradoras']))
folder = Path(__file__).parent / 'multifila'
folder.mkdir(exist_ok=True)
(folder / 'seleccion.json').write_text(json.dumps(selected, ensure_ascii=False, indent=2), encoding='utf-8')
item = selected['metadata']
source = root / '03_trabajo/analisis_estructura_sabana/inputs' / item['sala'] / item['name']
parsed = parse_workbook(source.read_bytes(), profile)
expected = []
for row in parsed.rows:
    originals = {original.campo: original.valor_original for original in row.originals if original.mostrar_en_consolidado}
    hashes = {}
    for spec in profile.fields:
        value = row.values[spec.campo]
        if spec.campo in originals:
            value = originals[spec.campo]
        elif spec.tipo_parte and spec.bit_no_aplica and row.masks.get(spec.tipo_parte, 0) & spec.bit_no_aplica:
            value = 'NO APLICA'
        elif hasattr(value, 'isoformat'):
            value = value.isoformat()
        encoded = 'N' if value is None else 'V' + str(value)
        hashes[spec.campo] = hashlib.sha256(encoded.encode('utf-16le')).hexdigest()
    expected.append(dict(numero_fila=row.number, hashes=hashes))
result = dict(filas_detectadas=parsed.total_rows, filas_parseadas=len(parsed.rows), bloqueado=parsed.blocked,
              incidencias=[issue.public() for issue in parsed.issues], campos_por_fila=len(profile.fields), filas=expected)
(folder / 'huellas_esperadas.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps(selected, ensure_ascii=True))
