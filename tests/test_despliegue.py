"""Lo que tiene que subir con el programa, y no depender de que alguien se acuerde.

Dos cosas tienen que pasar todos los días en un servidor de esto: la copia de
seguridad y la limpieza de datos personales. Las dos estaban escritas como
líneas de `cron` que hay que pegar a mano al montar cada servidor. La copia ya
se arregló —su propio contenedor—; la limpieza seguía siendo una línea en un
fichero de texto, y la que no se pega no se echa de menos hasta el día que hace
falta. Solo que esa no es una precaución: es el RGPD art. 5.1.e, y quien
responde es la casa.

Esto vigila que sigan ahí. Un `docker-compose.yml` no lo prueba nadie, así que
es justo donde algo se cae sin que nadie lo note.
"""
import pathlib

import pytest

RAIZ = pathlib.Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def compose() -> dict:
    yaml = pytest.importorskip("yaml")
    return yaml.safe_load((RAIZ / "docker-compose.yml").read_text())


def test_the_backup_and_the_cleanup_come_with_the_program(compose):
    """Las dos tareas diarias suben solas: nadie tiene que acordarse de ponerlas."""
    servicios = compose["services"]
    for cual in ("copias", "limpieza"):
        assert cual in servicios, f"falta el contenedor «{cual}»"
        orden = "".join(servicios[cual]["command"])
        assert "while true" in orden and "sleep 86400" in orden, cual
        # Que se vea si falla: un contenedor que se calla no avisa de nada.
        assert "HA FALLADO" in orden, cual
        assert servicios[cual]["restart"] == "unless-stopped", cual


def test_the_cleanup_really_sweeps_when_it_is_run(tmp_path, capsys):
    """La orden del contenedor no puede ser un nombre que ya no existe.

    Se ejecuta de verdad, contra una base de mentira, y tiene que hacer su
    barrido y contarlo. Si alguien renombra la orden, esto se cae aquí y no
    dentro de un año, cuando alguien mire qué se estaba guardando de más.
    """
    from thegrill import cli, db

    orden = "".join(compose_command())
    assert "purgar-solicitudes" in orden

    ruta = tmp_path / "limpia.db"
    db.init_engine(f"sqlite:///{ruta}")
    db.create_all()
    assert cli.main(["--db", f"sqlite:///{ruta}", "purgar-solicitudes"]) in (None, 0)
    dicho = capsys.readouterr().out
    for palabra in ("solicitudes", "números de envío", "sesiones caducadas"):
        assert palabra in dicho, dicho


def compose_command():
    """La orden del contenedor de limpieza, leída del fichero."""
    yaml = pytest.importorskip("yaml")
    datos = yaml.safe_load((RAIZ / "docker-compose.yml").read_text())
    return datos["services"]["limpieza"]["command"]


def test_the_cleanup_never_touches_the_meat(tmp_path):
    """Lo que pide una inspección se queda, tenga la edad que tenga.

    El barrido se lleva lo personal. Las recepciones, los despieces, las
    pesadas, las mermas y el libro de firmas no: eso lo obligan el
    (UE) 931/2011 y el (CE) 852/2004, y el propio RGPD lo excluye del derecho
    de supresión en el art. 17.3.b.
    """
    from datetime import date, timedelta

    from thegrill import db
    from thegrill.meat import privacy
    from thegrill.models import Primal, PrimalWeighing
    from thegrill.web import auth

    db.init_engine(f"sqlite:///{tmp_path/'d.db'}")
    db.create_all()
    viejo = date.today() - timedelta(days=4000)
    with db.session_scope() as s:
        rest, ana = auth.create_restaurant(s, "Casa", "ana@casa.com", "Ana", "clave-larga-1")
        pieza = Primal(restaurant_id=rest.id, serial="8001", sku="Striploin",
                       weight_kg=9.4, received_date=viejo)
        s.add(pieza)
        s.flush()
        s.add(PrimalWeighing(restaurant_id=rest.id, primal_id=pieza.id,
                             serial="8001", date=viejo, previous_kg=9.4, kg=9.2))
        s.flush()

        privacy.limpiar_lo_viejo(s)

        assert s.query(Primal).count() == 1, "se llevó por delante una recepción"
        assert s.query(PrimalWeighing).count() == 1, "se llevó por delante una pesada"
