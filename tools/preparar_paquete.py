"""Preparar una carpeta nueva con solo los archivos de ejecucion de la Function."""
import argparse
import shutil
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1]


def prepare(destination: Path) -> list[str]:
    destination = destination.resolve()
    # La herramienta solo crea una carpeta nueva dentro del proyecto.
    if SOURCE not in destination.parents or destination.exists():
        raise ValueError("Use una carpeta nueva dentro del proyecto; no se sobrescriben archivos.")
    for name in ("function_app.py", "host.json", "requirements.txt"):
        if not (SOURCE / name).is_file():
            raise ValueError(f"Falta el archivo requerido {name}.")
    if not (SOURCE / "sabana" / "__init__.py").is_file():
        raise ValueError("Falta el paquete Python sabana.")
    destination.mkdir(parents=True)
    for name in ("function_app.py", "host.json", "requirements.txt"):
        shutil.copyfile(SOURCE / name, destination / name)
    for package in ("sabana", "pst"):
        shutil.copytree(SOURCE / package, destination / package,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    return sorted(str(p.relative_to(destination)).replace("\\", "/")
                  for p in destination.rglob("*") if p.is_file())


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    try:
        files = prepare(args.destination)
    except ValueError as error:
        parser.error(str(error))
    print("Paquete de despliegue preparado:")
    print("\n".join(files))
