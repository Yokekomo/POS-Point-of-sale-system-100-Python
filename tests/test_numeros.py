"""Leer el número que quiso escribir quien lo escribió.

El mismo peso se teclea «1,250» en Madrid, «1.250» en Londres y «1 250» en
Budapest, y el teclado del móvil pone el separador que le da la gana. Un
`replace(",", ".")` funciona hasta el día que alguien escribe los miles, y ese
día el parte entra multiplicado por mil sin que salte nada.

Lo único de verdad dudoso es un separador solo, con tres cifras detrás:
«1.250» son kilo y cuarto o son mil doscientos cincuenta, y el número por sí
mismo no lo dice. Lo dice el campo: una báscula de cocina da tres decimales y
el dinero da dos. Por eso cada sitio que lee un número dice qué mide.
"""
import pytest

from thegrill.web import exacto, pos_import

KILOS = exacto.GRAMOS_DECIMALES
DINERO = exacto.CENTIMOS_DECIMALES
UNIDADES = 0


# ------------------------------------------------------- lo que no es dudoso
@pytest.mark.parametrize("escrito,valor", [
    ("1,5", 1.5), ("1.5", 1.5), ("330", 330.0), ("0,5", 0.5),
    ("1.234,56", 1234.56),          # España: punto de miles, coma decimal
    ("1,234.56", 1234.56),          # inglés: al revés, y el último manda
    ("1.234.567", 1234567.0),       # repetido: son los miles, no hay decimales
    ("1 250,5", 1250.5),            # Hungría y Francia separan con espacio
    ("1 250,5", 1250.5),       # y el navegador manda el espacio duro
    ("-2,75", -2.75), ("0,824", 0.824),
])
def test_a_number_written_by_a_person(escrito, valor):
    assert exacto.leer(escrito) == valor


@pytest.mark.parametrize("basura", ["4 C", "abc", "kg", "-", ",", "9,4,4,4kg"])
def test_what_is_not_a_number_is_not_guessed(basura):
    with pytest.raises(ValueError):
        exacto.leer(basura)


def test_nothing_written_is_nothing(client=None):
    assert exacto.leer("") is None
    assert exacto.leer(None) is None
    assert exacto.leer("", 0.0) == 0.0


# --------------------------------------------------------- lo que sí es dudoso
def test_three_figures_on_a_scale_are_grams():
    """«1,250 kg» en una báscula de cocina es kilo y cuarto. Siempre."""
    assert exacto.leer("1,250", decimales=KILOS) == 1.25
    assert exacto.leer("1.250", decimales=KILOS) == 1.25


def test_three_figures_on_a_price_are_thousands():
    """«1.250 €» son mil doscientos cincuenta. El dinero no tiene milésimas."""
    assert exacto.leer("1,250", decimales=DINERO) == 1250.0
    assert exacto.leer("1.250", decimales=DINERO) == 1250.0
    assert exacto.leer("1.250", decimales=UNIDADES) == 1250.0


def test_behind_a_zero_there_are_no_thousands():
    """Nadie escribe «0.500» por quinientos, ni siquiera en dinero."""
    assert exacto.leer("0,500", decimales=DINERO) == 0.5
    assert exacto.leer("0.500", decimales=DINERO) == 0.5


def test_four_figures_in_front_are_not_thousands_either():
    """Quien separa los miles los separa todos: «1.234.567», no «1234.567»."""
    assert exacto.leer("1234.567", decimales=DINERO) == 1234.567


def test_two_figures_are_always_decimals():
    assert exacto.leer("1.25", decimales=DINERO) == 1.25
    assert exacto.leer("12,50", decimales=DINERO) == 12.5


# ------------------------------------------------------- el parte del POS
def test_a_number_excel_already_read_is_not_read_again():
    """Pasar un número de Excel por `str()` lo multiplicaba por diez.

    Excel devuelve 12.5 como número. Antes se convertía en la cadena «12.5», y
    si el resto del parte escribía los decimales con coma, ese punto se leía
    después como separador de miles: 12,5 kg entraban como 125 y el escandallo
    del mes salía diez veces peor sin que nadie viera nada raro.
    """
    filas = [["Articulo", "Unidades", "Kg", "Importe"],
             ["Solomillo", 3.0, 12.5, "1.234,56"],
             ["Entrecot", 2.0, 0.75, "98,10"]]
    leido = pos_import._understand(filas)
    assert [r.kg for r in leido.rows] == [12.5, 0.75]
    assert [r.units for r in leido.rows] == [3.0, 2.0]
    assert [r.amount for r in leido.rows] == [1234.56, 98.1]


def test_a_numeric_cell_does_not_vote_on_the_separator():
    """Un número que ya venía leído no opina: no tiene separador que mirar."""
    celdas = pos_import._numeric_cells([["Solomillo", 3.0, "12,5"]],
                                       {"units": 1, "kg": 2})
    assert celdas == ["12,5"]


def test_a_numeric_product_code_still_reads_as_text():
    filas = [["Codigo", "Unidades"], [4711.0, 3.0]]
    leido = pos_import._understand(filas)
    assert leido.rows[0].code == "4711"


def test_a_file_that_does_not_say_how_it_writes_decimals_says_so():
    """Antes se daba por seguro el punto y el parte entraba dividido entre mil."""
    filas = [["Articulo", "Unidades", "Kg"],
             ["Solomillo", "3", "1.250"],
             ["Entrecot", "2", "2.500"]]
    leido = pos_import._understand(filas)
    assert leido.warnings and "1.250" in leido.warnings[0]


def test_a_file_with_no_separators_at_all_is_not_doubtful():
    filas = [["Articulo", "Unidades"], ["Solomillo", "3"], ["Entrecot", "2"]]
    assert pos_import._understand(filas).warnings == []
