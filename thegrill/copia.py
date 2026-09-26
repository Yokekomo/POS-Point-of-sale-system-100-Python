"""[01697] La copia de seguridad, y —lo que de verdad importa— cómo se vuelve de ella.

Hasta hoy no había ninguna. Ni una orden, ni una tarea, ni una línea en el
despliegue: la base de datos vivía en un volumen de Docker y las fotos de las
etiquetas en otro, y el día que ese disco se fuera se iban con él las
recepciones, los despieces, los inventarios y las etiquetas de todas las casas
a la vez. No es un riesgo de los que se asumen mirando el coste: es el único
fallo del que no se puede volver.

Cuatro decisiones, y las cuatro tienen motivo:

**Se copian las fotos, no solo la base.** La foto de la etiqueta es la prueba
de qué matadero, qué lote y qué fecha de sacrificio traía cada pieza —lo que
pide una inspección— y vive en disco, fuera de la base. Una copia que solo se
llevara la base dejaría los registros sin su respaldo y, peor, parecería
completa.

**La copia de SQLite se hace con `backup()` y no copiando el fichero.** El
programa trabaja en modo WAL: lo último escrito puede estar todavía en el
fichero de al lado, así que un `cp` de la base a secas se lleva una foto rota
justo de lo que se acaba de apuntar, que es lo que más falta hace. `backup()`
la copia consistente y sin parar a nadie.

**Va con manifiesto dentro.** Un `.tar.gz` sin nada que diga qué es, de cuándo
y de qué versión es un fichero que nadie se atreve a restaurar. El manifiesto
lleva la fecha, la versión del programa, qué clase de base es y cuántas casas y
cuántas piezas hay dentro: cuatro números que, al restaurar, dicen en un
vistazo si volvió todo.

**Y sabe volver.** Una copia que nunca se ha restaurado no es una copia: es un
fichero. Por eso `restaurar` está aquí al lado de `hacer`, por eso es una orden
de la línea de órdenes y no un procedimiento escrito en un documento, y por eso
la prueba que las acompaña no comprueba que el fichero exista: borra la casa
entera, vuelve de la copia y mira que la carne, los nombres y las fotos están.
"""
from __future__ import annotations

import json
import os
import pathlib
import shutil
import sqlite3
import subprocess
import tarfile
import tempfile
from datetime import datetime

from thegrill import version

# Lo que va dentro del paquete. Nombres fijos: al restaurar se buscan por aquí.
MANIFIESTO = "copia.json"
BASE_SQLITE = "base.sqlite"
BASE_SQL = "base.sql"           # el volcado de PostgreSQL
FOTOS = "fotos"                 # dentro del paquete, en las copias del formato 1
LISTA = "fotos.txt"             # las que había ese día, una por línea
ALMACEN = "fotos"               # al lado de los paquetes, compartido por todos

FORMATO = 2                     # si algún día cambia lo de dentro, sube esto
FORMATOS = (1, 2)               # los que este programa sabe restaurar
PLAZO_VOLCADO = 3600            # una hora para `pg_dump`: una base grande tarda


class CopiaError(RuntimeError):
    """[01698] La copia no se pudo hacer, o el paquete no se puede restaurar."""


def _ruta_sqlite(url: str) -> str | None:
    """[01699] El fichero de una URL de SQLite, o nada si no es SQLite."""
    if not url.startswith("sqlite"):
        return None
    sin_esquema = url.split("://", 1)[-1]
    fichero = sin_esquema.lstrip("/") if sin_esquema.startswith("/") else sin_esquema
    # `sqlite:///relativo.db` y `sqlite:////absoluto.db` se distinguen por la barra.
    if url.startswith("sqlite:////"):
        fichero = "/" + url.split("sqlite:////", 1)[1]
    return fichero or None


def _cuenta(url: str) -> dict:
    """[01700] Cuatro números para el manifiesto: qué hay dentro de esta copia.

    Se leen con SQL a pelo y no con el modelo a propósito: esto tiene que poder
    correr también contra una base de una versión distinta a la del programa
    que hace la copia, que es justo el caso en el que hace falta.
    """
    from sqlalchemy import create_engine, text

    numeros = {}
    try:
        motor = create_engine(url, future=True)
        with motor.connect() as conexion:
            for nombre, tabla in (("casas", "restaurants"), ("personas", "users"),
                                  ("piezas", "primals"), ("lotes", "ingredient_lots")):
                try:
                    numeros[nombre] = conexion.execute(
                        text(f"SELECT count(*) FROM {tabla}")).scalar_one()
                except Exception:          # noqa: BLE001 — tabla que aún no existe
                    numeros[nombre] = None
        motor.dispose()
    except Exception as porque:            # noqa: BLE001
        numeros["error"] = str(porque)
    return numeros


def _volcar_sqlite(url: str, destino: pathlib.Path) -> None:
    """[01701] La base de SQLite, copiada en caliente y entera.

    `Connection.backup()` es la única forma correcta con WAL puesto: copiando
    el fichero a mano se queda fuera lo último escrito, que vive en el `-wal`.
    """
    origen = _ruta_sqlite(url)
    if not origen or not os.path.exists(origen):
        raise CopiaError(f"no encuentro la base de datos en «{origen or url}»")
    con_origen = sqlite3.connect(origen)
    con_destino = sqlite3.connect(destino)
    try:
        con_origen.backup(con_destino)
    finally:
        con_destino.close()
        con_origen.close()


def _volcar_postgres(url: str, destino: pathlib.Path) -> None:
    """[01702] La base de PostgreSQL, con su propia herramienta.

    Se llama a `pg_dump` y no se intenta recorrer las tablas desde aquí: es la
    única manera de traerse también los índices, las secuencias y los tipos, y
    de que lo que salga se pueda restaurar en una máquina vacía.
    """
    if shutil.which("pg_dump") is None:
        raise CopiaError("falta `pg_dump`: sin él no se puede copiar una base "
                         "PostgreSQL. Va en el paquete postgresql-client.")
    # SQLAlchemy escribe el controlador dentro del esquema; `pg_dump` no lo entiende.
    limpia = url.replace("postgresql+psycopg://", "postgresql://") \
                .replace("postgresql+psycopg2://", "postgresql://")
    hecho = subprocess.run(["pg_dump", "--no-owner", "--no-privileges",
                            "--file", str(destino), limpia],
                           capture_output=True, text=True, timeout=PLAZO_VOLCADO)
    if hecho.returncode != 0:
        raise CopiaError(f"`pg_dump` falló: {hecho.stderr.strip()[:400]}")


def hacer(db_url: str, destino: str | os.PathLike, fotos_dir: str | os.PathLike | None = None,
          ahora: datetime | None = None) -> pathlib.Path:
    """[01703] Una copia entera: la base y las fotos, en un solo fichero fechado.

    Devuelve la ruta del paquete. El nombre lleva la fecha y la hora, porque lo
    primero que se pregunta delante de una carpeta de copias es de cuándo es
    cada una.
    """
    ahora = ahora or datetime.utcnow()
    carpeta = pathlib.Path(destino)
    carpeta.mkdir(parents=True, exist_ok=True)
    paquete = carpeta / f"carnes-{ahora.strftime('%Y%m%d-%H%M%S')}.tar.gz"

    with tempfile.TemporaryDirectory() as temporal:
        trabajo = pathlib.Path(temporal)
        if db_url.startswith("sqlite"):
            clase, dentro = "sqlite", BASE_SQLITE
            _volcar_sqlite(db_url, trabajo / BASE_SQLITE)
        else:
            clase, dentro = "postgres", BASE_SQL
            _volcar_postgres(db_url, trabajo / BASE_SQL)

        fotos = pathlib.Path(fotos_dir) if fotos_dir else None
        guardadas = _al_almacen(fotos, carpeta / ALMACEN)
        cuantas = len(guardadas)
        if guardadas:
            (trabajo / LISTA).write_text("\n".join(guardadas) + "\n", encoding="utf-8")

        manifiesto = {
            "formato": FORMATO,
            "fotos_en": ALMACEN,
            "hecha": ahora.isoformat(timespec="seconds"),
            "version": version.actual(),
            "base": clase,
            "fichero": dentro,
            "fotos": cuantas,
            "contenido": _cuenta(db_url),
        }
        (trabajo / MANIFIESTO).write_text(
            json.dumps(manifiesto, indent=2, ensure_ascii=False), encoding="utf-8")

        with tarfile.open(paquete, "w:gz") as tar:
            for cosa in sorted(trabajo.iterdir()):
                tar.add(cosa, arcname=cosa.name)
    return paquete


def _al_almacen(fotos: pathlib.Path | None,
                almacen: pathlib.Path) -> list[str]:
    """[01835] Deja las fotos en el almacén de al lado y dice cuáles había.

    Antes cada copia diaria se llevaba **la carpeta entera** de fotos dentro
    del paquete, y se guardan las treinta últimas: cada foto vivía una vez en
    disco y treinta en las copias. Treinta y una veces la misma foto. Y encima
    sin ganar nada al apretar, porque un JPEG ya viene apretado: meter las
    fotos en el `.tar.gz` ahorra un 0,03 %, medido.

    Así que las fotos no van dentro del paquete: van a un almacén al lado, una
    sola vez, y el paquete se lleva la **lista** de las que había ese día.
    Volver de una copia es volver a ese día: se ponen exactamente las de su
    lista, ni una más.

    Del almacén no se borra nunca nada, ni cuando la foto desaparece de la
    carpeta de trabajo —una etiqueta que sale movida y se repite—. Es una copia
    de seguridad: lo que entra, se queda. Por eso tampoco se comparan fechas ni
    tamaños; si el fichero ya está, es el mismo, porque el nombre lo pone un
    `uuid` que no se repite.

    **Esto cambia una cosa importante y hay que saberla: el paquete ya no se
    basta solo.** Para llevarse una copia fuera hay que llevarse el paquete
    **y** el almacén. Lo dice el manifiesto (`fotos_en`) y lo dice el
    despliegue.
    """
    if fotos is None or not fotos.is_dir():
        return []
    almacen.mkdir(parents=True, exist_ok=True)
    listas: list[str] = []
    for fichero in sorted(fotos.rglob("*")):
        if not fichero.is_file() or fichero.is_symlink():
            continue
        relativa = fichero.relative_to(fotos)
        listas.append(relativa.as_posix())
        destino = almacen / relativa
        if destino.exists():
            continue
        destino.parent.mkdir(parents=True, exist_ok=True)
        # A un fichero temporal primero: si se corta la copia a la mitad, en el
        # almacén no se queda media foto con el nombre de la buena.
        medio = destino.with_name(destino.name + ".a-medias")
        shutil.copy2(fichero, medio)
        medio.replace(destino)
    return listas


def _del_almacen(lista: list[str], almacen: pathlib.Path,
                 destino: pathlib.Path) -> int:
    """[01836] Devuelve a su sitio las fotos que había el día de esa copia."""
    puestas = 0
    faltan: list[str] = []
    for relativa in lista:
        # Una lista que viniera de fuera no puede sacarnos de la carpeta.
        camino = (almacen / relativa).resolve()
        if not str(camino).startswith(str(almacen.resolve()) + os.sep):
            raise CopiaError(f"la lista de fotos trae una ruta que se sale: {relativa}")
        if not camino.is_file():
            faltan.append(relativa)
            continue
        fuera = destino / relativa
        fuera.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(camino, fuera)
        puestas += 1
    if faltan:
        raise CopiaError(
            f"faltan {len(faltan)} fotos en el almacén «{almacen}» (por ejemplo "
            f"«{faltan[0]}»). El paquete y el almacén viajan juntos: sin él la "
            "copia vuelve sin las etiquetas, que es lo que pide una inspección.")
    return puestas


def leer_manifiesto(paquete: str | os.PathLike) -> dict:
    """[01704] Qué hay dentro de una copia, sin restaurarla.

    Sirve para lo que se hace de verdad delante de una carpeta de copias:
    mirar de cuándo es y cuántas casas lleva antes de decidir cuál se usa.
    """
    try:
        with tarfile.open(paquete, "r:gz") as tar:
            miembro = tar.extractfile(MANIFIESTO)
            if miembro is None:
                raise CopiaError(f"«{paquete}» no lleva manifiesto: no es una copia nuestra")
            return json.loads(miembro.read().decode("utf-8"))
    except (tarfile.TarError, KeyError, ValueError) as porque:
        raise CopiaError(f"«{paquete}» no se puede leer como copia: {porque}") from porque


def _seguro(tar: tarfile.TarFile, hacia: pathlib.Path) -> None:
    """[01705] Desempaqueta comprobando que nada se salga de su carpeta.

    Un `.tar` puede llevar rutas como `../../etc/algo`, y `extractall` a secas
    las obedece. El paquete lo hacemos nosotros, pero una copia es un fichero
    que viaja —a un disco, a un servidor, al correo de alguien— y el día que se
    restaure uno que no salió de aquí, esto es lo único que hay en medio.
    """
    raiz = hacia.resolve()
    for miembro in tar.getmembers():
        camino = (raiz / miembro.name).resolve()
        if not str(camino).startswith(str(raiz) + os.sep) and camino != raiz:
            raise CopiaError(f"la copia trae una ruta que se sale: {miembro.name}")
        if miembro.issym() or miembro.islnk():
            raise CopiaError(f"la copia trae un enlace, y eso no: {miembro.name}")
    tar.extractall(hacia)          # noqa: S202 — comprobado arriba, miembro a miembro


def restaurar(paquete: str | os.PathLike, db_url: str,
              fotos_dir: str | os.PathLike | None = None) -> dict:
    """[01706] Vuelve de una copia. **Machaca lo que haya**, y por eso avisa.

    Devuelve el manifiesto de lo que se ha restaurado, para poder comparar esos
    cuatro números con lo que se esperaba.

    No intenta mezclar nada con lo que hubiera: restaurar es volver a un día, no
    juntar dos. Mezclar registros de trazabilidad de dos momentos distintos es
    peor que no restaurar, porque deja una base que cuadra menos que ninguna de
    las dos y nadie sabe por qué.
    """
    manifiesto = leer_manifiesto(paquete)
    if manifiesto.get("formato") not in FORMATOS:
        raise CopiaError(f"esa copia es del formato {manifiesto.get('formato')} y este "
                         f"programa entiende el {FORMATO}")

    with tempfile.TemporaryDirectory() as temporal:
        trabajo = pathlib.Path(temporal)
        with tarfile.open(paquete, "r:gz") as tar:
            _seguro(tar, trabajo)

        if manifiesto.get("base") == "sqlite":
            origen = trabajo / BASE_SQLITE
            fichero = _ruta_sqlite(db_url)
            if not fichero:
                raise CopiaError("la copia es de SQLite y el destino no lo es")
            pathlib.Path(fichero).parent.mkdir(parents=True, exist_ok=True)
            # Los ficheros de al lado del WAL: si se quedan, contradicen a la
            # base que acabamos de poner y el programa arranca con una mezcla.
            for sobra in (fichero + "-wal", fichero + "-shm"):
                pathlib.Path(sobra).unlink(missing_ok=True)
            shutil.copyfile(origen, fichero)
        else:
            _restaurar_postgres(trabajo / BASE_SQL, db_url)

        if fotos_dir:
            destino = pathlib.Path(fotos_dir)
            dentro = trabajo / FOTOS
            if destino.exists():
                shutil.rmtree(destino)
            destino.mkdir(parents=True, exist_ok=True)
            if dentro.is_dir():
                # [01837] Una copia del formato 1: las fotos venían dentro del paquete.
                # Se siguen restaurando, que una copia vieja tiene que poder
                # abrirse el día que haga falta.
                shutil.rmtree(destino)
                shutil.copytree(dentro, destino)
            elif (trabajo / LISTA).is_file():
                lista = [l for l in (trabajo / LISTA).read_text(encoding="utf-8")
                         .splitlines() if l.strip()]
                _del_almacen(lista, pathlib.Path(paquete).parent / ALMACEN, destino)
    return manifiesto


def _restaurar_postgres(volcado: pathlib.Path, db_url: str) -> None:
    """[01707] Mete el volcado en PostgreSQL con su propia herramienta."""
    if shutil.which("psql") is None:
        raise CopiaError("falta `psql`: sin él no se puede restaurar en PostgreSQL. "
                         "Va en el paquete postgresql-client.")
    limpia = db_url.replace("postgresql+psycopg://", "postgresql://") \
                   .replace("postgresql+psycopg2://", "postgresql://")
    hecho = subprocess.run(["psql", "--quiet", "--set", "ON_ERROR_STOP=1",
                            "--file", str(volcado), limpia],
                           capture_output=True, text=True, timeout=PLAZO_VOLCADO)
    if hecho.returncode != 0:
        raise CopiaError(f"`psql` falló al restaurar: {hecho.stderr.strip()[:400]}")


def limpiar(destino: str | os.PathLike, guardar: int = 30,
            ahora: datetime | None = None) -> list[pathlib.Path]:
    """[01708] Se queda con las últimas `guardar` copias y borra las demás.

    Por número y no por días a propósito. Por días, el disco de un servidor al
    que le fallara la tarea durante un mes se quedaría sin ninguna copia justo
    después de un mes sin copias —dos fallos que se tapan el uno al otro—.
    Por número siempre quedan las últimas que se llegaron a hacer, pasara lo
    que pasara en medio.
    """
    carpeta = pathlib.Path(destino)
    if not carpeta.is_dir():
        return []
    hay = sorted(carpeta.glob("carnes-*.tar.gz"), key=lambda f: f.name)
    if guardar <= 0 or len(hay) <= guardar:
        return []
    sobran = hay[:len(hay) - guardar]
    for fichero in sobran:
        fichero.unlink(missing_ok=True)
    return sobran
