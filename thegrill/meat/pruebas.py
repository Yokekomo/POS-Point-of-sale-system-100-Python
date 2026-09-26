"""[01763] Pasar las pruebas desde la propia web, en el servidor de verdad.

Por qué existe esto, que no es lo normal y conviene decirlo.

Las pruebas se pasan en el ordenador de quien programa y en el servidor de
integración. Las dos cosas están bien y las dos comprueban **otra máquina**:
otra versión de Python, otro sistema de ficheros, otro reloj, otra zona
horaria, otra base de datos. El día que algo se rompe solo en el servidor de
verdad —una zona horaria que no está instalada, un disco lleno, una columna que
no volvió sola al actualizar— no hay forma de saberlo desde fuera, y lo que
llega es un cliente diciendo que «no va».

Esto pasa las mismas pruebas **ahí**, en la máquina que atiende a los clientes,
con su Python y su disco, y enseña el resultado en una pantalla. Y para cada
fallo dice **en qué número de comentario está**, que es como está numerado todo
el programa: así no hay que buscar el fichero y la línea, se va directo.

Tres cosas que se hacen a propósito y que son toda la seguridad de esto:

**Corre en otro proceso.** No se importa pytest aquí ni se llama a `pytest.main`
dentro del servidor. Las pruebas montan su propia base de datos con
`db.init_engine`, que es **global**: llamarlas dentro del proceso que atiende a
los clientes le cambiaría la base de datos a todo el mundo a mitad de turno.
En otro proceso, esa variable global es la suya y no la nuestra.

**Con una base de datos de mentira escrita en el entorno del hijo.** Aunque una
prueba se olvidara de montar la suya, la que encontraría es un fichero temporal
que se borra al terminar. Nunca la de los clientes.

**Y con plazo.** Una prueba colgada no puede dejar un proceso comiendo la
máquina para siempre. A los veinte minutos se corta.

Lo que **no** hace esto: escribir en la base de los clientes, mandar correos, ni
cobrar nada. Las pruebas trabajan contra sus propios ficheros temporales.
"""
from __future__ import annotations

import os
import pathlib
import re
import subprocess
import sys
import tempfile
import threading
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime

RAIZ = pathlib.Path(__file__).resolve().parents[2]
PLAZO = 20 * 60          # veinte minutos: la suite entera tarda unos once

# [01764] El número que lleva cada comentario del programa. Es lo que se enseña al
# lado de cada fallo: «se rompió en el 01683» lleva directo al sitio, sin tener
# que abrir el fichero y contar líneas.
MARCA = re.compile(r"\[(\d{5})\]")


@dataclass(frozen=True)
class Grupo:
    """[01765] Un conjunto de pruebas que se puede lanzar de una vez."""
    codigo: str
    args: tuple[str, ...]
    minutos: int            # lo que tarda más o menos, para avisar antes


GRUPOS: dict[str, Grupo] = {
    # Lo que se pasa cuando algo huele mal y hay clientes trabajando: dos
    # minutos y toca lo que más duele si se rompe —las cuentas, las puertas,
    # los idiomas, las columnas que tienen que volver solas al actualizar—.
    "rapidas": Grupo("rapidas", (
        "tests/test_matematicas.py", "tests/test_numeros.py", "tests/test_cifras.py",
        "tests/test_puertas.py", "tests/test_i18n.py", "tests/test_migration.py",
        "tests/test_meat_account.py", "tests/test_tarifa.py", "tests/test_errores.py",
        "tests/test_copia.py", "tests/test_exportar.py", "tests/test_manifiesto.py",
    ), 3),
    # Todo menos lo que necesita navegador —que no está en la imagen del
    # servidor a propósito— y menos las de aguante, que tardan solas más que
    # todo lo demás junto.
    "sin_navegador": Grupo("sin_navegador", (
        "tests/", "--ignore=tests/test_maqueta.py", "--ignore=tests/test_tutorial.py",
        "--ignore=tests/test_mobile.py", "--ignore=tests/test_speed.py",
    ), 9),
    "todas": Grupo("todas", ("tests/",), 12),
}


@dataclass
class Fallo:
    """[01766] Una prueba que no pasó, con dónde y con su número."""
    prueba: str
    fichero: str
    linea: int
    numero: str              # el [NNNNN] del comentario más cercano
    porque: str


@dataclass
class Tanda:
    """[01767] Una pasada de pruebas: en qué va y cómo terminó."""
    grupo: str
    quien: str
    empezo: datetime
    corriendo: bool = True
    segundos: float = 0.0
    total: int = 0
    fallos: list[Fallo] = field(default_factory=list)
    saltadas: int = 0
    error: str = ""

    @property
    def pasan(self) -> int:
        return max(0, self.total - len(self.fallos) - self.saltadas)

    @property
    def bien(self) -> bool:
        return not self.corriendo and not self.fallos and not self.error


# [01768] La última tanda, y solo una a la vez.
#
# En memoria y no en la base a propósito: esto es un dato de hace un momento,
# no un registro. Y una sola porque dos pasadas de la suite a la vez en la
# máquina que atiende a los clientes es exactamente lo que no hay que hacer.
_ultima: Tanda | None = None
_candado = threading.Lock()


def ultima() -> Tanda | None:
    """[01769] Cómo fue —o cómo va— la última vez que se pasaron."""
    return _ultima


def corriendo() -> bool:
    """[01770] Si hay una pasada en marcha ahora mismo."""
    return _ultima is not None and _ultima.corriendo


def lanzar(codigo: str, quien: str) -> Tanda:
    """[01771] Arranca una pasada. Si ya hay una, devuelve esa y no arranca otra."""
    global _ultima
    with _candado:
        if corriendo():
            return _ultima
        grupo = GRUPOS.get(codigo) or GRUPOS["rapidas"]
        _ultima = Tanda(grupo=grupo.codigo, quien=quien, empezo=datetime.utcnow())
        hilo = threading.Thread(target=_corre, args=(grupo, _ultima), daemon=True)
        hilo.start()
        return _ultima


def _corre(grupo: Grupo, tanda: Tanda) -> None:
    """[01772] El trabajo de verdad, en otro proceso y con su base de mentira."""
    arranca = time.monotonic()
    with tempfile.TemporaryDirectory() as temporal:
        parte = pathlib.Path(temporal) / "parte.xml"
        entorno = dict(os.environ)
        # Una base de mentira, por si alguna prueba se olvidara de montar la
        # suya. La de los clientes no se toca ni por accidente.
        entorno["GRILL_DB"] = f"sqlite:///{pathlib.Path(temporal) / 'pruebas.db'}"
        entorno["GRILL_UPLOAD_DIR"] = str(pathlib.Path(temporal) / "subidas")
        entorno["PYTHONDONTWRITEBYTECODE"] = "1"
        try:
            hecho = subprocess.run(
                [sys.executable, "-m", "pytest", *grupo.args, "-q", "-p", "no:randomly",
                 f"--junit-xml={parte}"],
                cwd=RAIZ, env=entorno, capture_output=True, text=True, timeout=PLAZO)
            if parte.exists():
                _lee_parte(parte, tanda)
            elif hecho.returncode != 0:
                tanda.error = (hecho.stderr or hecho.stdout or "")[-600:]
        except subprocess.TimeoutExpired:
            tanda.error = f"se pasó del plazo de {PLAZO // 60} minutos y se cortó"
        except Exception as porque:                     # noqa: BLE001
            tanda.error = str(porque)[:600]
    tanda.segundos = round(time.monotonic() - arranca, 1)
    tanda.corriendo = False


def _lee_parte(parte: pathlib.Path, tanda: Tanda) -> None:
    """[01773] Lo que dice el parte de pytest, traducido a lo que se enseña."""
    arbol = ET.parse(parte).getroot()
    suites = arbol.findall(".//testsuite") or [arbol]
    for suite in suites:
        tanda.total += int(suite.get("tests") or 0)
        tanda.saltadas += int(suite.get("skipped") or 0)
    for caso in arbol.iter("testcase"):
        malo = caso.find("failure") if caso.find("failure") is not None else caso.find("error")
        if malo is None:
            continue
        texto = f"{malo.get('message') or ''}\n{malo.text or ''}"
        fichero, linea = _donde_se_rompio(texto, caso)
        tanda.fallos.append(Fallo(
            prueba=f"{(caso.get('classname') or '').replace('.', '/')}::"
                   f"{caso.get('name') or ''}".strip(":"),
            fichero=fichero, linea=linea,
            numero=numero_de(fichero, linea),
            porque=_primera_linea(malo.get("message") or malo.text or "")))


# Los renglones de la traza: «thegrill/meat/tarifa.py:123: in guardar».
CUADRO = re.compile(r"([\w./\\-]+\.py):(\d+)")


def _donde_se_rompio(texto: str, caso) -> tuple[str, int]:
    """[01781] En qué punto del **programa** se rompió, no en qué punto de la prueba.

    Es lo que de verdad hace falta: que una prueba falle en su línea 30 no dice
    nada; que el programa reviente en `tarifa.py:123` sí, porque de ahí sale el
    número del comentario y con él se va directo al sitio.

    Por eso se busca el cuadro **más profundo que esté dentro de `thegrill/`**:
    la traza empieza en la prueba y baja hasta donde se rompió de verdad. Si no
    hay ninguno —una prueba que se equivoca ella sola— se enseña el último
    cuadro de todos, que es el de la prueba, que es lo único que hay.

    pytest no escribe el fichero ni la línea en su parte, así que sale de la
    traza: no es lo más limpio del mundo y es lo que hay.
    """
    nuestros, todos = [], []
    for fichero, linea in CUADRO.findall(texto or ""):
        limpio = fichero.replace("\\", "/").lstrip("./")
        todos.append((limpio, int(linea)))
        if limpio.startswith("thegrill/"):
            nuestros.append((limpio, int(linea)))
    if nuestros:
        return nuestros[-1]
    if todos:
        return todos[-1]
    # Nada que rascar: al menos el fichero de la prueba, que sale del parte.
    clase = ((caso.get("classname") if caso is not None else "") or "").replace(".", "/")
    return (f"{clase}.py" if clase else ""), 0


def _primera_linea(texto: str) -> str:
    """[01774] Lo primero que dice el fallo, que es lo que se lee de un vistazo."""
    for linea in (texto or "").splitlines():
        if linea.strip():
            return linea.strip()[:220]
    return ""


def numero_de(fichero: str, linea: int) -> str:
    """[01775] El número del comentario más cercano por encima de esa línea.

    Todo el programa lleva sus comentarios numerados —`[01683]`— precisamente
    para poder decir dónde pasa algo sin tener que dar un fichero y un número
    de línea, que cambian cada vez que alguien añade tres renglones. El número
    no cambia.

    Si esa parte del programa todavía no tiene comentario numerado, se devuelve
    vacío y se enseña el fichero y la línea, que es lo que había antes.
    """
    if not fichero or linea <= 0:
        return ""
    ruta = RAIZ / fichero
    if not ruta.is_file():
        return ""
    try:
        lineas = ruta.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return ""
    for i in range(min(linea, len(lineas)) - 1, -1, -1):
        hallado = MARCA.search(lineas[i])
        if hallado:
            return hallado.group(1)
    return ""
