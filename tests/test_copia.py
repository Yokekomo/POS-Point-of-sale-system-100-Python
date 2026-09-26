"""Que haya copia, y sobre todo que se sepa volver de ella.

Una copia que nunca se ha restaurado no es una copia: es un fichero que ocupa
sitio y tranquiliza. Por eso la prueba central de aquí no comprueba que el
paquete exista ni que pese algo: **borra la casa entera** —la base y las fotos
de las etiquetas— y vuelve de la copia, y luego mira que la carne, los nombres
y las fotos están, y que la foto es la misma byte a byte.

Lo que se vigila, y por qué cada cosa:

- **Que la copia se lleve las fotos.** La foto de la etiqueta es la prueba de
  qué matadero y qué lote traía cada pieza, y vive fuera de la base. Una copia
  que solo se llevara la base pasaría por buena hasta el día que hiciera falta.
- **Que la base se copie en caliente y entera.** El programa trabaja en modo
  WAL: lo último apuntado puede estar todavía en el fichero de al lado. Aquí se
  escribe una pieza, se copia sin cerrar nada y se comprueba que esa pieza está
  dentro del paquete.
- **Que restaurar no se trague un paquete cualquiera.** Una copia es un fichero
  que viaja, y un `.tar` puede llevar rutas que se salen de su carpeta.
- **Que no se restaure por error.** La orden machaca lo que hay: sin `--si` no
  hace nada y lo dice.
"""
import json
import os
import pathlib
import shutil
import sqlite3
import tarfile
from datetime import date, datetime, timedelta

import pytest

from thegrill import bench, cli, copia, db
from thegrill.models import Primal, Restaurant, User

HOY = date(2026, 9, 20)


@pytest.fixture
def casa(tmp_path):
    """Una casa con trabajo dentro y una foto de etiqueta en disco."""
    base = tmp_path / "carnes.db"
    fotos = tmp_path / "fotos"
    (fotos / "1" / "2026-09-20").mkdir(parents=True)
    (fotos / "1" / "2026-09-20" / "etiqueta.jpg").write_bytes(b"\xff\xd8\xff-esto-es-la-prueba")

    db.init_engine(f"sqlite:///{base}")
    db.create_all()
    with db.session_scope() as s:
        bench.build(s, days=6, seed=5, until=HOY)
    return tmp_path, f"sqlite:///{base}", base, fotos


def _como_esta(url):
    """Lo que hay ahora mismo en la base, para comparar antes y después."""
    db.init_engine(url)
    with db.session_scope() as s:
        return {
            "casas": sorted(r.name for r in s.query(Restaurant).all()),
            "personas": sorted(u.email for u in s.query(User).all()),
            "piezas": sorted(p.serial for p in s.query(Primal).all()),
        }


# ------------------------------------------------------------ ida y vuelta
def test_the_whole_house_comes_back_from_a_backup(casa):
    """La prueba que decide si esto sirve para algo: borrarlo todo y volver."""
    carpeta, url, base, fotos = casa
    antes = _como_esta(url)
    assert antes["piezas"], "la casa de la prueba salió vacía"
    foto = fotos / "1" / "2026-09-20" / "etiqueta.jpg"
    tenia = foto.read_bytes()

    paquete = copia.hacer(url, carpeta / "copias", fotos)

    # Se va el disco: la base, sus ficheros de al lado y todas las fotos.
    db.init_engine("sqlite:///:memory:")            # suelta el fichero
    for sobra in (base, pathlib.Path(str(base) + "-wal"), pathlib.Path(str(base) + "-shm")):
        sobra.unlink(missing_ok=True)
    import shutil
    shutil.rmtree(fotos)
    assert not base.exists() and not fotos.exists()

    copia.restaurar(paquete, url, fotos)

    assert _como_esta(url) == antes, "volvió una casa distinta de la que se copió"
    assert foto.read_bytes() == tenia, "la foto de la etiqueta volvió cambiada"


def test_a_backup_without_the_label_photos_would_look_fine_and_not_be(casa):
    """Las fotos entran en la copia. En el almacén de al lado, pero entran."""
    carpeta, url, _, fotos = casa
    paquete = copia.hacer(url, carpeta / "copias", fotos)
    with tarfile.open(paquete, "r:gz") as tar:
        dentro = tar.getnames()
    assert copia.LISTA in dentro, dentro
    assert copia.leer_manifiesto(paquete)["fotos"] == 1
    guardada = (carpeta / "copias" / copia.ALMACEN / "1" / "2026-09-20" / "etiqueta.jpg")
    assert guardada.read_bytes() == (fotos / "1" / "2026-09-20" / "etiqueta.jpg").read_bytes()


# --------------------------------------- la misma foto no se guarda 31 veces
def test_the_same_photo_is_not_stored_once_for_every_backup(casa):
    """Cada copia diaria se llevaba la carpeta entera de fotos dentro.

    Y se guardan las treinta últimas: la misma foto vivía una vez en disco y
    treinta en las copias. Treinta y una veces. Y sin ganar nada al apretar,
    porque un JPEG ya viene apretado.
    """
    carpeta, url, _, fotos = casa
    # Una foto de tamaño creíble, que si pesa veinte bytes no se nota nada.
    gorda = fotos / "1" / "2026-09-20" / "gorda.jpg"
    gorda.write_bytes(b"\xff\xd8\xff" + os.urandom(400_000))

    def pesa(donde):
        return sum(f.stat().st_size for f in pathlib.Path(donde).rglob("*") if f.is_file())

    copia.hacer(url, carpeta / "copias", fotos, ahora=datetime(2026, 9, 20, 3, 0))
    despues_de_una = pesa(carpeta / "copias")
    for dia in range(21, 26):
        copia.hacer(url, carpeta / "copias", fotos, ahora=datetime(2026, 9, dia, 3, 0))
    seis = pesa(carpeta / "copias")

    # Cinco copias más no pueden costar cinco fotos más.
    crecio = seis - despues_de_una
    assert crecio < 400_000, (
        f"seis copias de la misma foto ocupan {crecio} bytes de más: se está "
        "guardando la foto una vez por copia")


def test_a_photo_that_was_replaced_still_comes_back_from_its_own_day(casa):
    """Una etiqueta sale movida y se repite: la copia de ayer trae la de ayer.

    Del almacén no se borra nada, ni cuando la foto desaparece de la carpeta
    de trabajo. Es una copia de seguridad: lo que entra, se queda.
    """
    carpeta, url, _, fotos = casa
    vieja = fotos / "1" / "2026-09-20" / "etiqueta.jpg"
    tenia = vieja.read_bytes()
    ayer = copia.hacer(url, carpeta / "copias", fotos, ahora=datetime(2026, 9, 20, 3, 0))

    vieja.unlink()                      # la foto movida, fuera
    (fotos / "1" / "2026-09-20" / "otra.jpg").write_bytes(b"\xff\xd8\xff-la-buena")
    copia.hacer(url, carpeta / "copias", fotos, ahora=datetime(2026, 9, 21, 3, 0))

    copia.restaurar(ayer, url, fotos)
    assert vieja.read_bytes() == tenia
    assert not (fotos / "1" / "2026-09-20" / "otra.jpg").exists(), \
        "volver a ayer trajo una foto que ayer no existía"


def test_a_backup_from_the_old_format_still_restores(casa, tmp_path):
    """Una copia vieja tiene que poder abrirse el día que haga falta."""
    carpeta, url, _, fotos = casa
    antiguo = tmp_path / "vieja"
    (antiguo / copia.FOTOS / "1" / "2026-09-20").mkdir(parents=True)
    (antiguo / copia.FOTOS / "1" / "2026-09-20" / "etiqueta.jpg").write_bytes(b"de-2026")
    copia._volcar_sqlite(url, antiguo / copia.BASE_SQLITE)
    (antiguo / copia.MANIFIESTO).write_text(json.dumps({
        "formato": 1, "hecha": "2026-09-20T03:00:00", "version": "0",
        "base": "sqlite", "fichero": copia.BASE_SQLITE, "fotos": 1, "contenido": {}}))
    paquete = tmp_path / "copias" / "carnes-20260920-030000.tar.gz"
    paquete.parent.mkdir(parents=True)
    with tarfile.open(paquete, "w:gz") as tar:
        for cosa in sorted(antiguo.iterdir()):
            tar.add(cosa, arcname=cosa.name)

    copia.restaurar(paquete, url, fotos)
    assert (fotos / "1" / "2026-09-20" / "etiqueta.jpg").read_bytes() == b"de-2026"


def test_a_package_that_travelled_without_its_store_says_so(casa, tmp_path):
    """El paquete ya no se basta solo, y eso hay que decirlo, no callarlo.

    Volver con las etiquetas en blanco y sin una palabra es lo peor que puede
    hacer una restauración: parece que salió bien.
    """
    carpeta, url, _, fotos = casa
    paquete = copia.hacer(url, carpeta / "copias", fotos)
    solo = tmp_path / "otro-disco"
    solo.mkdir()
    shutil.copy2(paquete, solo / paquete.name)     # el paquete sin su almacén

    with pytest.raises(copia.CopiaError, match="almacén"):
        copia.restaurar(solo / paquete.name, url, fotos)


def test_pruning_the_old_packages_never_touches_the_store(casa):
    """Borrar copias viejas no puede llevarse las fotos de todas."""
    carpeta, url, _, fotos = casa
    for dia in range(20, 26):
        copia.hacer(url, carpeta / "copias", fotos, ahora=datetime(2026, 9, dia, 3, 0))
    copia.limpiar(carpeta / "copias", guardar=2)
    guardada = carpeta / "copias" / copia.ALMACEN / "1" / "2026-09-20" / "etiqueta.jpg"
    assert guardada.is_file(), "la limpieza se llevó el almacén de fotos"
    assert len(list((carpeta / "copias").glob("carnes-*.tar.gz"))) == 2


def test_what_was_just_written_is_inside_the_backup(casa):
    """En modo WAL, copiar el fichero a secas se deja lo último apuntado."""
    carpeta, url, _, fotos = casa
    db.init_engine(url)
    with db.session_scope() as s:
        una = s.query(Restaurant).filter(Restaurant.platform.isnot(True)).first()
        s.add(Primal(restaurant_id=una.id, serial="RECIEN-ESCRITA", sku="X",
                     weight_kg=1.0, received_kg=1.0, received_date=HOY))

    paquete = copia.hacer(url, carpeta / "copias", fotos)   # sin cerrar nada

    import tempfile
    with tempfile.TemporaryDirectory() as fuera:
        with tarfile.open(paquete, "r:gz") as tar:
            tar.extract(copia.BASE_SQLITE, fuera)
        con = sqlite3.connect(pathlib.Path(fuera) / copia.BASE_SQLITE)
        seriales = [f[0] for f in con.execute("SELECT serial FROM primals")]
        con.close()
    assert "RECIEN-ESCRITA" in seriales, "la copia se dejó fuera lo último apuntado"


def test_the_manifest_says_what_is_inside(casa):
    """Cuatro números que, al restaurar, dicen de un vistazo si volvió todo."""
    carpeta, url, _, fotos = casa
    dentro = copia.leer_manifiesto(copia.hacer(url, carpeta / "copias", fotos))
    assert dentro["formato"] == copia.FORMATO
    assert dentro["base"] == "sqlite"
    assert dentro["version"] and dentro["hecha"]
    assert dentro["contenido"]["piezas"] > 0
    assert dentro["contenido"]["casas"] > 0


# ------------------------------------------------------- lo que no se acepta
def test_a_package_that_is_not_ours_is_refused(casa, tmp_path):
    carpeta, url, _, fotos = casa
    cualquiera = tmp_path / "cualquiera.tar.gz"
    with tarfile.open(cualquiera, "w:gz") as tar:
        suelto = tmp_path / "suelto.txt"
        suelto.write_text("hola")
        tar.add(suelto, arcname="suelto.txt")
    with pytest.raises(copia.CopiaError):
        copia.leer_manifiesto(cualquiera)
    with pytest.raises(copia.CopiaError):
        copia.restaurar(cualquiera, url, fotos)


def test_a_package_that_escapes_its_folder_is_refused(casa, tmp_path):
    """Una copia viaja: a un disco, a un servidor, al correo de alguien."""
    carpeta, url, _, fotos = casa
    paquete = copia.hacer(url, carpeta / "copias", fotos)

    malo = tmp_path / "malo.tar.gz"
    import shutil
    import tempfile
    with tempfile.TemporaryDirectory() as fuera:
        with tarfile.open(paquete, "r:gz") as tar:
            tar.extractall(fuera)
        with tarfile.open(malo, "w:gz") as tar:
            for cosa in sorted(pathlib.Path(fuera).iterdir()):
                tar.add(cosa, arcname=cosa.name)
            colado = pathlib.Path(fuera) / "colado"
            colado.write_text("me salgo")
            tar.add(colado, arcname="../../colado")

    with pytest.raises(copia.CopiaError, match="se sale"):
        copia.restaurar(malo, url, fotos)
    shutil.rmtree(fuera, ignore_errors=True)


# ----------------------------------------------------------- las dos órdenes
def test_restoring_by_mistake_takes_two_goes(casa, capsys):
    """La orden machaca lo que hay: sin `--si` enseña qué trae y no toca nada."""
    carpeta, url, base, fotos = casa
    paquete = copia.hacer(url, carpeta / "copias", fotos)
    antes = base.stat().st_mtime_ns

    assert cli.main(["--db", url, "restaurar", str(paquete), "--fotos", str(fotos)]) == 1
    escrito = capsys.readouterr().out
    assert "MACHACA" in escrito and "--si" in escrito
    assert base.stat().st_mtime_ns == antes, "restauró sin que se lo confirmaran"

    assert cli.main(["--db", url, "restaurar", str(paquete),
                     "--fotos", str(fotos), "--si"]) in (None, 0)


def test_the_backup_command_prunes_the_old_ones(casa):
    carpeta, url, _, fotos = casa
    from datetime import datetime
    for minuto in range(5):
        copia.hacer(url, carpeta / "copias", fotos,
                    ahora=datetime(2026, 9, 20, 3, minuto, 0))
    assert len(list((carpeta / "copias").glob("*.tar.gz"))) == 5

    borradas = copia.limpiar(carpeta / "copias", guardar=2)
    quedan = sorted(f.name for f in (carpeta / "copias").glob("*.tar.gz"))
    assert len(borradas) == 3 and len(quedan) == 2
    # Se quedan las ÚLTIMAS, que son las que sirven.
    assert quedan == ["carnes-20260920-030300.tar.gz", "carnes-20260920-030400.tar.gz"]


def test_keeping_zero_means_never_deleting(casa):
    """Quien pone cero es que se las guarda él: no se le borra ninguna."""
    carpeta, url, _, fotos = casa
    from datetime import datetime
    for minuto in range(3):
        copia.hacer(url, carpeta / "copias", fotos,
                    ahora=datetime(2026, 9, 20, 4, minuto, 0))
    assert copia.limpiar(carpeta / "copias", guardar=0) == []
    assert len(list((carpeta / "copias").glob("*.tar.gz"))) == 3


def test_a_database_that_is_not_there_says_so(tmp_path):
    with pytest.raises(copia.CopiaError, match="no encuentro"):
        copia.hacer(f"sqlite:///{tmp_path/'no-existe.db'}", tmp_path / "copias")
