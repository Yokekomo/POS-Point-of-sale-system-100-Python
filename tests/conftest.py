import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from thegrill.web import jornada


@pytest.fixture(autouse=True)
def jornada_a_medianoche():
    """Durante las pruebas el día de trabajo acaba a medianoche.

    Una casa de verdad cierra a las tres de la mañana, y eso está bien: lo que
    se apunta a las dos y media es del servicio de anoche. Pero la suite usa
    `date.today()` para saber qué día es, y entre medianoche y las tres esas
    dos cosas no son la misma: la mitad de las pruebas pasarían por la tarde y
    fallarían de madrugada, que es la peor clase de prueba que hay.

    Así que aquí el corte es cero y el día es el del reloj, como antes. El
    corte de verdad —y el de la casa que elige otro— se prueba con el reloj en
    la mano en `tests/test_jornada.py`, que es donde se puede hacer bien.
    """
    antes = jornada.POR_DEFECTO
    jornada.POR_DEFECTO = 0
    yield
    jornada.POR_DEFECTO = antes
