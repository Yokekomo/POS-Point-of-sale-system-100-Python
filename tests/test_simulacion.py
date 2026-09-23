"""Que la simulación siga siendo una prueba de verdad y no un teatro.

Esto no comprueba el programa: comprueba el aparato que lo comprueba. Una
equivocación que no se llega a meter —porque cambió una firma, porque la casa
de mentira ya no trae piezas madurando— sale como «no cabía» y deja de vigilar
nada, sin que nadie se entere. Eso es lo que se guarda aquí.
"""
from datetime import date

import pytest

from simulacion import torpeza
from thegrill import bench, db

HOY = date(2026, 9, 20)


@pytest.fixture(scope="module")
def casas(tmp_path_factory):
    """Dos casas, una con varias sedes y otra sin ellas."""
    ruta = tmp_path_factory.mktemp("sim") / "sim.db"
    db.init_engine(f"sqlite:///{ruta}")
    db.create_all()
    montadas = []
    with db.session_scope() as session:
        for i in range(2):
            montadas.append(bench.build(session, days=8, seed=41 + i, until=HOY,
                                        multisite=i == 0, index=i))
        yield session, montadas


def test_every_mistake_can_actually_be_made(casas):
    """Si una equivocación ya no se puede meter, ha dejado de vigilar."""
    session, montadas = casas
    sin_meter = []
    for equivocacion in torpeza.CATALOGO:
        entro = False
        for casa in montadas:
            try:
                metida = equivocacion.mete(session, casa, HOY)
            except Exception as e:                           # noqa: BLE001
                sin_meter.append(f"{equivocacion.clave}: reventó · {type(e).__name__}: {e}")
                break
            if metida is not None:
                entro = True
                break
        if not entro and not any(equivocacion.clave in s for s in sin_meter):
            sin_meter.append(f"{equivocacion.clave}: no cabía en ninguna casa")
    assert not sin_meter, sin_meter


def test_looking_for_the_mistake_never_blows_up(casas):
    """Mirar si el manager lo ve no puede fallar: si falla, el informe miente."""
    session, montadas = casas
    rotas = []
    for equivocacion in torpeza.CATALOGO:
        for casa in montadas:
            metida = equivocacion.mete(session, casa, HOY)
            if metida is None or metida.rechazada:
                continue
            try:
                visto = equivocacion.ve(session, casa, metida, HOY)
            except Exception as e:                           # noqa: BLE001
                rotas.append(f"{equivocacion.clave}: {type(e).__name__}: {e}")
                break
            assert isinstance(visto.visto, bool)
            break
    assert not rotas, rotas


def test_the_catalogue_says_who_is_at_fault_and_what_it_costs(casas):
    """Torpeza y dejadez se arreglan distinto: la una con la pantalla, la otra
    con el aviso. Si el catálogo no lo dice, el informe no sirve."""
    for equivocacion in torpeza.CATALOGO:
        assert equivocacion.quien in ("torpeza", "dejadez"), equivocacion.clave
        assert equivocacion.cuenta and len(equivocacion.cuenta) > 20, equivocacion.clave
    assert len({e.clave for e in torpeza.CATALOGO}) == len(torpeza.CATALOGO)
