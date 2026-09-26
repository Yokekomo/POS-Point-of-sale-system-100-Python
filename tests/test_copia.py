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
import pathlib
import sqlite3
import tarfile
from datetime import date, timedelta

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
    """Las fotos van dentro del paquete, no solo la base."""
    carpeta, url, _, fotos = casa
    paquete = copia.hacer(url, carpeta / "copias", fotos)
    with tarfile.open(paquete, "r:gz") as tar:
        dentro = tar.getnames()
    assert any(n.endswith("etiqueta.jpg") for n in dentro), dentro
    assert copia.leer_manifiesto(paquete)["fotos"] == 1


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
