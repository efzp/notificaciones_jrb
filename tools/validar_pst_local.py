"""Piloto de solo lectura sin Azure ni SQL. Solo imprime conteos, sin datos de correos."""
import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pst.domain import PstFailure
from pst.extract import extract_message, folder_at, inventory
from pst.range_io import RangeReader


def validate(path, maximum=40):
    import pypff
    before = path.stat()
    stats = Counter()
    codes = Counter()
    with path.open("rb") as stream:
        def fetch(offset, length):
            stream.seek(offset)
            value = stream.read(length)
            stats["peticiones_rango"] += 1
            stats["bytes_leidos_por_rangos"] += len(value)
            return value
        reader = RangeReader(before.st_size, fetch)
        file = pypff.file()
        file.open_file_object(reader)
        try:
            folders = inventory(file)
            stats["carpetas"] = len(folders)
            stats["elementos_reportados"] = sum(f["elementos"] for f in folders)
            stats["rangos_inventario"] = stats["peticiones_rango"]
            stats["bytes_inventario"] = stats["bytes_leidos_por_rangos"]
            candidates = [f for f in folders if f["elementos"]]
            index = 0
            while stats["examinados"] < maximum and candidates:
                next_candidates = []
                for folder in candidates:
                    if stats["examinados"] >= maximum:
                        break
                    if index >= folder["elementos"]:
                        continue
                    native = folder_at(file, folder["ruta"])
                    stats["examinados"] += 1
                    try:
                        value = extract_message(native.get_sub_message(index))
                        if value is None:
                            stats["omitidos"] += 1
                        else:
                            stats["extraidos"] += 1
                            for key in ("destinatarios", "adjuntos", "referencias", "reportes"):
                                stats[key] += len(value[key])
                            stats["destinatarios_mapi"] += sum(r["fuente"] == 1 for r in value["destinatarios"])
                            stats["con_destinatarios_mapi_completos"] += bool(value["destinatarios_completos"])
                            codes.update(value["incidencias"])
                    except PstFailure as error:
                        stats["fallidos"] += 1
                        codes[error.code] += 1
                    except Exception as error:
                        stats["fallidos"] += 1
                        codes["PST_LECTURA_"+type(error).__name__.upper()] += 1
                    next_candidates.append(folder)
                candidates = next_candidates
                index += 1
        finally:
            file.close()
            reader.close()
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise RuntimeError("PST_MODIFICADO_DURANTE_PILOTO")
    return {"lector": pypff.get_version(), "tamano_pst": before.st_size, "solo_lectura": True,
            "sql_ejecutado": False, "estadisticas": dict(stats), "incidencias": dict(codes)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pst", type=Path)
    parser.add_argument("--maximum", type=int, default=40)
    args = parser.parse_args()
    if not 1 <= args.maximum <= 1000:
        parser.error("Use --maximum entre 1 y 1000.")
    print(json.dumps(validate(args.pst, args.maximum), ensure_ascii=True))
