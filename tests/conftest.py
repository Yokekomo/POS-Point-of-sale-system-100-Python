import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from thegrill.web import jornada


# Dónde suele estar el navegador con el que se miden las pantallas. El primero
# es el del entorno de desarrollo; el segundo, el de las máquinas viejas.
NAVEGADORES = ("/opt/pw-browsers/chromium-1243/chrome-linux64/chrome",
               "/opt/pw-browsers/chromium-1194/chrome-linux/chrome")


def abre_navegador(pw):
    """El Chromium con el que se mide, esté donde esté.

    Y si no está en ninguna parte, dos comportamientos distintos a propósito.
    En el ordenador de alguien, la prueba se salta: no todo el mundo tiene un
    navegador de pruebas instalado y no es motivo para dejarle la suite en
    rojo. **En el servidor de integración, no**: allí se cae.

    La diferencia importa más de lo que parece. Las pruebas de maqueta son
    justo las que comprueban que la portada no se sale en alemán y que lo que
    se pulsa se puede pulsar con guante. Si el día que falte el navegador se
    saltaran calladas, el servidor daría verde sin haber medido nada y la
    guardia sería un adorno. Un verde que no ha comprobado nada es peor que un
    rojo: el rojo se arregla.
    """
    for ruta in NAVEGADORES:
        if os.path.exists(ruta):
            return pw.chromium.launch(executable_path=ruta)
    try:
        return pw.chromium.launch()      # el que se haya instalado Playwright
    except Exception as porque:
        if os.environ.get("CI"):
            raise AssertionError(
                "no hay navegador y esto es el servidor de integración: las "
                "pruebas de pantalla no pueden saltarse aquí. Falta "
                "`playwright install --with-deps chromium`. "
                f"Lo que dijo el navegador: {porque}") from porque
        pytest.skip("no hay navegador instalado en esta máquina")


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
