"""Calibrar cobertura operacional. Los puntajes no son probabilidades de identidad."""
import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from terceros.matching import EntityMatcher


def evaluate(profile,threshold):
    matcher=EntityMatcher(profile["entidades"],profile["roles"],threshold=threshold)
    methods=Counter();weighted=Counter();examples=[]
    for item in profile["nombres_jnc"]:
        result=matcher.resolve(item["nombre"],item["rol"])
        methods[result["metodo"]]+=1;weighted[result["metodo"]]+=item["ocurrencias"]
        if result["id_entidad"] is not None:
            examples.append(dict(item,**result,nombre_representante=matcher.entities[result["id_entidad"]]))
    audit=matcher.export()
    groups=Counter(row["tipo_parte"] for row in audit["equivalencias"] if row["origen"]=="JRB" and row["id_entidad_origen"]!=row["id_entidad_representante"])
    return {"umbral":threshold,"margen":matcher.margin,"metodos":dict(methods),"ocurrencias":dict(weighted),
            "ids_jrb_agrupados_por_rol":dict(groups),
            "ejemplos":sorted(examples,key=lambda r:(-r["ocurrencias"],r["rol"],r["nombre"]))[:40],
            "equivalencias":audit["equivalencias"]}


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("perfil",type=Path)
    parser.add_argument("salida",type=Path)
    args=parser.parse_args()
    if "resultados" not in args.salida.resolve().parts:parser.error("Use una carpeta resultados excluida de Git")
    profile=json.loads(args.perfil.read_text(encoding="utf-8"))
    results=[]
    for threshold in (93.0,95.0,97.0):
        result=evaluate(profile,threshold);results.append(result)
        print(json.dumps({k:v for k,v in result.items() if k not in {"equivalencias","ejemplos"}},ensure_ascii=True),flush=True)
    args.salida.parent.mkdir(parents=True,exist_ok=True)
    args.salida.write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding="utf-8")
