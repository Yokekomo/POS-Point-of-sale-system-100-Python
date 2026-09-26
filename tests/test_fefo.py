from datetime import date, timedelta
import pytest

from thegrill.engine.fefo import Lot, NoCostBasis, consume, expiring

D = date(2026, 9, 10)
LOTS = [
    Lot("beef_trim", "B", date(2026, 9, 15), 5.0, 8.0, received=date(2026, 9, 1)),
    Lot("beef_trim", "A", date(2026, 9, 12), 3.0, 10.0, received=date(2026, 9, 5)),
    Lot("beef_trim", "C", date(2026, 9, 12), 2.0, 9.0, received=date(2026, 9, 2)),   # misma caducidad que A, recibido antes
    Lot("butter", "X", date(2026, 10, 1), 10.0, 6.0),
]


def test_consumes_earliest_expiry_then_fifo():
    r = consume(LOTS, "beef_trim", 4.0)
    assert [(c.lot_id, c.kg) for c in r.consumptions] == [("C", 2.0), ("A", 2.0)]
    assert r.cost_usd == 2 * 9.0 + 2 * 10.0
    left = {l.lot_id: l.kg for l in r.remaining}
    assert left == {"A": 1.0, "B": 5.0, "X": 10.0}


def test_never_blank_cost():
    with pytest.raises(NoCostBasis):
        consume(LOTS, "saffron", 0.1)


def test_shortfall_flagged_not_silent():
    with pytest.raises(NoCostBasis):
        consume(LOTS, "beef_trim", 20.0)
    r = consume(LOTS, "beef_trim", 20.0, allow_shortfall=True)
    assert r.shortfall_kg == 10.0 and r.consumptions[-1].lot_id == "SHORTFALL"


def test_expiring_alert():
    assert [l.lot_id for l in expiring(LOTS, D, within_days=2)] == ["C", "A"]


def test_what_is_written_down_is_what_is_taken_out():
    """El papel y el almacén tienen que decir lo mismo, al gramo.

    Se apuntaba el consumo redondeado a cuatro decimales y se descontaba del
    lote con seis. Medio decigramo por lote y por salida, siempre hacia el
    mismo lado: no lo ve una báscula, pero es un libro que no cuadra con el
    almacén, y esa clase de hueco se descubre tres meses después sin poder
    explicarlo.
    """
    import random

    from thegrill.engine import fefo

    azar = random.Random(19)
    peor = 0.0
    for _ in range(3000):
        lotes = [fefo.Lot(ingredient="X", lot_id=f"L{i}",
                          expiry=date(2026, 9, 20) + timedelta(days=i),
                          # Con seis decimales a propósito: un lote que nace de
                          # partir otro —un traslado, una salida del arcón— no
                          # pesa 4,250 exactos, y es ahí donde se ve si lo que
                          # se apunta tiene los mismos decimales que lo que se
                          # saca. Con lotes de tres decimales no se nota nada.
                          kg=round(azar.uniform(0.001, 12), 6),
                          unit_cost_usd=round(azar.uniform(5, 90), 2),
                          received=date(2026, 9, 1))
                 for i in range(azar.randint(1, 6))]
        hay = sum(l.kg for l in lotes)
        pedido = round(azar.uniform(0.001, hay), 6)
        salida = fefo.consume(lotes, "X", pedido)
        # Lo apuntado suma lo pedido…
        peor = max(peor, abs(salida.kg - pedido))
        # …y lo que queda es lo que había menos lo apuntado.
        queda = sum(l.kg for l in salida.remaining)
        peor = max(peor, abs((hay - pedido) - queda))
    assert peor < 1e-6, f"el papel y el almacén se separan {peor:.10g} kg"


# --------------------------------------------- empate exacto entre dos cajas
def test_a_tie_goes_to_the_lot_that_was_entered_first():
    """Doce cajas del mismo palé: misma caducidad, misma entrada.

    El número del lote viaja como texto, y en texto el 10 va antes que el 9.
    La cola salía «1, 10, 11, 12, 2…» y se abría antes la caja que llegó
    después. La cuenta cuadra igual; lo que no cuadra es qué número acaba en
    el plato cuando alguien pregunta de dónde salió.
    """
    iguales = [Lot("beef", str(n), date(2026, 9, 20), 1.0, 7.0, received=date(2026, 9, 1))
               for n in range(1, 13)]
    from thegrill.engine.fefo import order_fefo, order_fifo
    esperado = [str(n) for n in range(1, 13)]
    assert [l.lot_id for l in order_fefo(iguales)] == esperado
    assert [l.lot_id for l in order_fifo(iguales)] == esperado
    # y quien consume saca de la primera caja, no de la décima
    assert consume(iguales, "beef", 1.0).consumptions[0].lot_id == "1"


def test_lots_with_letters_still_sort_without_blowing_up():
    """No todos los números de lote son números: el orden sigue siendo firme."""
    from thegrill.engine.fefo import order_fefo
    mezcla = [Lot("beef", x, date(2026, 9, 20), 1.0, 7.0, received=date(2026, 9, 1))
              for x in ("B-2", "10", "A-1", "9")]
    salida = [l.lot_id for l in order_fefo(mezcla)]
    assert salida == ["9", "10", "A-1", "B-2"]       # los números antes, y en orden
    assert order_fefo(list(reversed(mezcla))) == order_fefo(mezcla)   # no depende de cómo entren
