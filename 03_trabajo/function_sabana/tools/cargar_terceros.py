"""Migracion y carga por instantanea. No imprime correos ni datos de pacientes."""
from __future__ import annotations

import argparse
import gzip
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from terceros.domain import validate_snapshot
from terceros.repository import apply, migrate, summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    migration = sub.add_parser("migrar")
    migration.add_argument("--aplicar", action="store_true")
    extract = sub.add_parser("capturar")
    extract.add_argument("archivo", type=Path)
    extract.add_argument("--umbral",type=float,default=95.0)
    extract.add_argument("--margen",type=float,default=5.0)
    load = sub.add_parser("cargar")
    load.add_argument("archivo", type=Path)
    load.add_argument("--aplicar", action="store_true")
    args = parser.parse_args()
    try:
        if args.command == "migrar":
            result = migrate() if args.aplicar else {"simulacion":True,"accion":"Aplicar migraciones 09 y 10 en JRB"}
        elif args.command == "capturar":
            from terceros.capture import capture
            if args.archivo.suffix != ".gz" or "resultados" not in args.archivo.resolve().parts:
                raise ValueError("USE_ARCHIVO_GZ_EN_CARPETA_RESULTADOS_EXCLUIDA_DE_GIT")
            if args.archivo.exists():
                raise ValueError("LA_INSTANTANEA_YA_EXISTE")
            document = capture(progress=lambda phase: print(json.dumps({"fase":phase}),file=sys.stderr,flush=True),
                               threshold=args.umbral,margin=args.margen)
            validate_snapshot(document)
            args.archivo.parent.mkdir(parents=True, exist_ok=True)
            with args.archivo.open("xb") as target:
                with gzip.GzipFile(fileobj=target,mode="wb",mtime=0) as stream:
                    stream.write(json.dumps(document,ensure_ascii=True,sort_keys=True,default=str).encode())
            result = dict(summary(document), capturada=True)
        else:
            with gzip.open(args.archivo,"rt",encoding="utf-8") as stream:
                document = json.load(stream)
            result = apply(document) if args.aplicar else dict(summary(document),simulacion=True)
        print(json.dumps(result,ensure_ascii=True,sort_keys=True))
        return 0
    except Exception as error:
        # No imprimir el texto de ODBC, SDK, JSON ni campos de negocio.
        safe = str(error) if isinstance(error,(ValueError,RuntimeError)) else type(error).__name__
        if not safe or any(c not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ_0123456789" for c in safe):
            safe = type(error).__name__
        from sabana.database import sql_number
        print(json.dumps({"estado":"ERROR","codigo":safe,"numero_sql":sql_number(error)}),file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
