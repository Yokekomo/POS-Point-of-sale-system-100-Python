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

from thegrill.web import exacto, pesos, pos_import

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


# ==================================================== el peso, en gramos
#
# Una báscula da «9,435 kg» y el teclado de un móvil no tiene coma en la fila
# de números. En gramos no hay coma que acertar: 9435.
class TestGramos:
    """Lo que se escribe son gramos; lo que se guarda siguen siendo kilos."""

    def test_the_scale_reading_goes_in_whole(self):
        assert pesos.leer("9435") == 9.435
        assert pesos.leer("250") == 0.25
        assert pesos.leer("1") == 0.001

    def test_a_thousands_separator_is_still_thousands(self):
        """Copiar una cifra de un albarán no puede cambiarla.

        En gramos no hay ambigüedad: tres dígitos detrás del separador no son
        decimales de nada, porque decimales de gramo no existen.
        """
        assert pesos.leer("9.435") == 9.435
        assert pesos.leer("9,435") == 9.435
        assert pesos.leer("9 435") == 9.435

    def test_nothing_written_is_nothing(self):
        assert pesos.leer("") is None
        assert pesos.leer(None) is None
        assert pesos.leer("", default=0.0) == 0.0

    def test_a_comma_in_grams_says_what_to_write_instead(self):
        """«9,4» no son nueve gramos: es alguien pensando todavía en kilos.

        Adivinarlo es como se guarda un peso mil veces menor sin que nadie se
        entere. Se dice qué pasa y con qué cifra se arregla.
        """
        with pytest.raises(pesos.ConDecimales) as caso:
            pesos.leer("9,4")
        assert caso.value.escrito == "9,4"
        assert caso.value.como_kilos == 9.4
        assert caso.value.gramos == 9400      # lo que hay que escribir

    def test_what_is_not_a_number_is_still_not_a_number(self):
        with pytest.raises(exacto.NoEsUnNumero):
            pesos.leer("nueve kilos")

    def test_the_box_comes_back_with_the_grams_of_what_is_stored(self):
        assert pesos.escribir(9.435) == "9435"
        assert pesos.escribir(0.25) == "250"
        assert pesos.escribir(None) == ""


class TestLaColaDelTelefonoDeAyer:
    """Un envío guardado sin cobertura llega con el nombre de antes.

    Las casillas de peso se llamaban `kg` y ahora se llaman `g`. Un teléfono
    que se quedó sin señal con la pantalla vieja abierta guarda el formulario
    tal cual y lo manda horas o días después. Si ese `kg=9,4` se leyera como
    gramos quedarían apuntados nueve gramos: mil veces menos, en silencio, y
    descubierto en el cuadre de fin de mes. Por eso el nombre cambió, y por
    eso el de antes se sigue leyendo en la unidad que tenía.
    """

    def test_the_old_name_still_means_kilos(self):
        assert pesos.de_dos(gramos="", kilos="9,4") == 9.4
        assert pesos.de_dos(gramos="", kilos="9.4") == 9.4

    def test_the_new_name_means_grams(self):
        assert pesos.de_dos(gramos="9400", kilos="") == 9.4

    def test_when_both_come_the_new_one_wins(self):
        """El navegador de hoy no manda los dos, pero si alguien los manda, el
        que dice la verdad sobre lo que hay en la pantalla es el de ahora."""
        assert pesos.de_dos(gramos="9400", kilos="1,1") == 9.4

    def test_with_neither_there_is_no_weight(self):
        assert pesos.de_dos(gramos="", kilos="") is None
        assert pesos.de_dos(gramos="", kilos="", default=0.0) == 0.0

    def test_a_whole_sheet_from_yesterday_is_read_in_kilos(self):
        """Lo mismo leyendo del formulario entero, que es como llega."""
        de_ayer = {"kg:0": "9,4", "serial:0": "8017"}
        assert pesos.del_formulario(de_ayer, "g:0", "kg:0") == 9.4
        de_hoy = {"g:0": "9400", "serial:0": "8017"}
        assert pesos.del_formulario(de_hoy, "g:0", "kg:0") == 9.4


class TestLoQuePesaUnaUnidad:
    """El número que permite escribirlo todo en gramos sin mentir.

    Un huevo pesa 55 g y un litro de aceite de oliva, 916. Con eso puesto, la
    receta se escribe pesando —que es lo único honesto cuando un huevo L pesa
    68 y uno M, 58— y la cámara se sigue contando en bandejas y garrafas, que
    es como llegan. Sin eso puesto no se convierte nada: un programa que se
    inventa cuánto pesa un huevo escandalla con un 20 % de error que nadie
    vuelve a mirar.
    """

    def casa(self, unidad, gramos=None):
        from types import SimpleNamespace
        return SimpleNamespace(unit=unidad, grams_per_unit=gramos)

    def test_what_is_already_in_kilos_needs_no_number(self):
        from thegrill.models import Unit
        assert pesos.se_pesa(self.casa(Unit.KG))

    def test_an_egg_needs_someone_to_say_what_it_weighs(self):
        from thegrill.models import Unit
        assert not pesos.se_pesa(self.casa(Unit.UNIT))
        assert pesos.se_pesa(self.casa(Unit.UNIT, 55))

    def test_a_hundred_and_ten_grams_of_egg_are_two_eggs(self):
        from thegrill.models import Unit
        huevo = self.casa(Unit.UNIT, 55)
        assert pesos.en_su_unidad(pesos.leer("110"), huevo) == 2.0

    def test_and_the_litre_of_oil_is_its_density(self):
        from thegrill.models import Unit
        aceite = self.casa(Unit.L, 916)
        assert pesos.en_su_unidad(pesos.leer("916"), aceite) == pytest.approx(1.0)
        assert pesos.en_su_unidad(pesos.leer("458"), aceite) == pytest.approx(0.5)

    def test_half_a_jug_stays_half_a_jug(self):
        """Redondear a la unidad de arriba sería inventarse medio litro."""
        from thegrill.models import Unit
        aceite = self.casa(Unit.L, 916)
        assert pesos.en_su_unidad(pesos.leer("1374"), aceite) == pytest.approx(1.5)

    def test_meat_in_kilos_goes_through_untouched(self):
        from thegrill.models import Unit
        assert pesos.en_su_unidad(pesos.leer("250"), self.casa(Unit.KG)) == 0.25

    def test_with_no_number_nothing_is_invented(self):
        """Lo escrito vale tal cual: no se adivina cuánto pesa un huevo."""
        from thegrill.models import Unit
        sin_saber = self.casa(Unit.UNIT)
        assert pesos.por_unidad(sin_saber) is None
        assert pesos.en_su_unidad(3.0, sin_saber) == 3.0

    def test_and_the_box_can_show_again_what_is_stored(self):
        from thegrill.models import Unit
        assert pesos.a_gramos(2, self.casa(Unit.UNIT, 55)) == 110.0
        assert pesos.a_gramos(1.5, self.casa(Unit.L, 916)) == 1374.0
        assert pesos.a_gramos(2, self.casa(Unit.UNIT)) is None
