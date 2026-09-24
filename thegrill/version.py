"""Qué versión de esto se está ejecutando.

Sirve para una pregunta que se repite cada vez que alguien prueba algo por su
cuenta: «esto que ves, ¿es lo de hoy o lo de la semana pasada?». Sin un número
a la vista, un arreglo que no se ha bajado y un arreglo que no funciona se
parecen demasiado, y se pierde media tarde buscando el segundo cuando era el
primero.

Sale del propio git de la carpeta. Si no hay git —un ZIP, una imagen de
Docker—, se dice que no se sabe en vez de inventarse un número.
"""
import subprocess
from functools import lru_cache
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
DESCONOCIDA = "sin git"


def _git(*args: str) -> str:
    """Lo que conteste git, o vacío si no hay git o tarda demasiado.

    Es para poner la versión en la pantalla, no para trabajar: si falla, la
    aplicación arranca igual.
    """
    try:
        salida = subprocess.run(("git", "-C", str(RAIZ), *args),
                                capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return ""
    return salida.stdout.strip() if salida.returncode == 0 else ""


@lru_cache(maxsize=1)
def actual() -> str:
    """Una línea corta: el día del último cambio y su número. Nunca revienta."""
    fecha = _git("log", "-1", "--format=%cd", "--date=format:%d/%m/%Y %H:%M")
    commit = _git("rev-parse", "--short", "HEAD")
    if not commit:
        return DESCONOCIDA
    rama = _git("rev-parse", "--abbrev-ref", "HEAD")
    sucia = " · con cambios sin guardar" if _git("status", "--porcelain") else ""
    partes = [p for p in (fecha, commit, rama) if p]
    return " · ".join(partes) + sucia
