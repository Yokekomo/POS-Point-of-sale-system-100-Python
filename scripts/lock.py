"""Rehace `requirements.lock` a partir de lo que hay instalado.

    python -m scripts.lock > requirements.lock

Recorre las dependencias de `requirements.txt` y las de sus dependencias, y
escribe la versión exacta de cada una. Lo que no está instalado se dice en un
comentario, para que no pase desapercibido.
"""
import importlib.metadata as metadata
import pathlib
import sys

CABECERA = """# Las versiones exactas con las que este código está probado.
#
# `requirements.txt` dice qué hace falta y admite versiones nuevas: sirve para
# desarrollar. Este fichero dice con cuáles funciona de verdad, y es el que se
# instala en el servidor: un despliegue de un martes no puede traer una
# librería distinta de la del lunes solo porque haya salido.
#
#     pip install -r requirements.lock
#
# Para subir una versión: se cambia aquí, se pasan las pruebas, y se sube el
# cambio como cualquier otro. Nunca al revés.
#
# Rehacer la lista después de actualizar algo:
#     python -m scripts.lock > requirements.lock
"""


def direct(path: str = "requirements.txt") -> list[str]:
    """Lo que pide `requirements.txt`, sin comentarios ni versiones."""
    out = []
    for line in pathlib.Path(path).read_text().splitlines():
        line = line.split("#")[0].strip()
        if not line:
            continue
        for sep in (">=", "==", "<=", "~=", ">", "<"):
            if sep in line:
                line = line.split(sep)[0]
                break
        out.append(line.strip())
    return out


def closure(names: list[str]) -> tuple[dict[str, str], list[str]]:
    """Cada dependencia y las suyas, con su versión instalada."""
    from packaging.requirements import Requirement

    found: dict[str, str] = {}
    missing: list[str] = []
    pending = list(names)
    while pending:
        name = pending.pop().lower().replace("_", "-")
        if name in found or name in missing:
            continue
        try:
            dist = metadata.distribution(name)
        except metadata.PackageNotFoundError:
            missing.append(name)
            continue
        found[name] = dist.version
        for raw in dist.requires or []:
            requirement = Requirement(raw)
            if requirement.marker and not requirement.marker.evaluate({"extra": ""}):
                continue      # dependencia de un extra que no usamos
            pending.append(requirement.name)
    return found, missing


def main() -> int:
    found, missing = closure(direct())
    print(CABECERA)
    for name, version in sorted(found.items()):
        print(f"{name}=={version}")
    for name in sorted(missing):
        print(f"# sin instalar, no se fija: {name}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
