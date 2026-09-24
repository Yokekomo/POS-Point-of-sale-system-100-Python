"""Dos personas a la vez: contando la misma cámara, cortando la misma pieza.

En una casa nadie trabaja solo. Mientras uno cuenta el inventario del local,
otro despieza en el obrador y un tercero da de alta el camión. Lo que se
comprueba aquí es lo que pasa cuando los dos tocan **lo mismo** en el mismo
segundo, que es cuando los números se rompen sin que nadie se entere: la misma
pieza despiezada dos veces mete en cámara el doble de carne de la que había, y
el mismo turno cerrado dos veces saca de la cámara kilos que ya habían salido.

La regla es siempre la misma: el que escribe comprueba **en la misma orden**.
Uno de los dos se lo lleva, y al otro se le dice, con su nombre, que llegó
segundo. Nadie pisa el trabajo de nadie en silencio.
"""
import threading
from datetime import date, timedelta

import pytest

from thegrill import db
from thegrill.meat import service as meat
from thegrill.models import (CountPeriod, CountStatus, Despiece, IngredientItem,
                             IngredientLot, IngredientMovement, MeatCount, Primal,
                             PrimalStatus, SiteKind, Transfer, User)
from thegrill.web import auth, butchery, defrost, inventory, sites

HOY = date(2026, 9, 20)


@pytest.fixture
def casa(tmp_path):
    """Una casa con obrador, dos locales, una pieza y un lote de cortes."""
    db.init_engine(f"sqlite:///{tmp_path/'c.db'}")
    db.create_all()
    with db.session_scope() as s:
        rest, ana = auth.create_restaurant(s, "Grupo Marina", "ana@a.com", "Ana",
                                           "clave-larga-1", language="es")
        auth.join_restaurant(s, rest.join_code, "paco@a.com", "Paco", "clave-larga-2")
        auth.join_restaurant(s, rest.join_code, "eva@a.com", "Eva", "clave-larga-3")
        obrador = sites.main(s, rest.id)
        playa = sites.create(s, ana, "Playa", SiteKind.OUTLET)
        sierra = sites.create(s, ana, "Sierra", SiteKind.OUTLET)
        s.add(Primal(restaurant_id=rest.id, serial="8017", sku="RIBEYE_AUS",
                     weight_kg=9.0, received_kg=9.0, received_date=HOY,
                     landed_usd_per_kg=30.0, piece_cost_usd=270.0,
                     frozen_use_by=HOY + timedelta(days=60),
                     site_id=obrador.id, status=PrimalStatus.IN_STOCK))
        item, corte = _articulo(s, rest.id)
        s.add(IngredientLot(restaurant_id=rest.id, item_id=item.id,
                            ingredient_id=corte, serial="8017-01", lot_code="TG-0001",
                            expiry=HOY + timedelta(days=15), received=HOY,
                            qty=10.0, qty_remaining=10.0, unit_cost=30.0,
                            site_id=obrador.id))
        s.flush()
        datos = (rest.id, obrador.id, playa.id, sierra.id, item.id)
    yield datos


def _articulo(s, restaurant_id):
    from thegrill.models import ConsumptionMode, Ingredient, Unit
    corte = Ingredient(restaurant_id=restaurant_id, name="Entrecot", unit=Unit.KG,
                       consumption=ConsumptionMode.COUNT)
    s.add(corte); s.flush()
    item = IngredientItem(restaurant_id=restaurant_id, ingredient_id=corte.id,
                          name="Entrecot 300 g")
    s.add(item); s.flush()
    return item, corte.id


def a_la_vez(trabajo, veces=2):
    """Lanza el mismo trabajo desde varias sesiones y devuelve lo que falló.

    Cada hilo abre su propia sesión, como haría cada móvil su propia petición:
    ninguno ve lo que el otro no ha guardado todavía, que es justo la situación
    en la que los números se rompen.
    """
    # Un minuto para que los hilos coincidan. Con diez segundos, en una máquina
    # cargada —la suite entera corriendo— un hilo llegaba tarde, la barrera se
    # rompía y fallaban los dos. Eso salía luego como «no se mandó la pieza», o
    # sea, disfrazado de fallo del dinero, que es el que menos se puede
    # confundir. Ahora si se rompe lo dice con esas palabras.
    puerta = threading.Barrier(veces, timeout=60)
    fallos: list[str] = []
    rotos: list[int] = []

    def uno(i):
        try:
            with db.session_scope() as s:
                puerta.wait()
                trabajo(s, i)
        except threading.BrokenBarrierError:
            rotos.append(i)
        except Exception as e:                     # noqa: BLE001 - se mira el mensaje
            fallos.append(f"{type(e).__name__}: {e}")

    hilos = [threading.Thread(target=uno, args=(i,)) for i in range(veces)]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join()
    if rotos:
        raise RuntimeError(
            f"{len(rotos)} de {veces} hilos no llegaron a la vez en 60 s: la prueba "
            "no ha llegado a correrse. Es la máquina, no el programa.")
    # Y el otro modo de no llegar a correrse: SQLite echando a los dos. Dos
    # conexiones que ya están en transacción y las dos quieren escribir se
    # bloquean entre ellas, y ahí SQLite contesta «locked» sin esperar al
    # plazo. No es que el programa haya dejado pasar a los dos —eso sería el
    # fallo gordo— sino que no ha dejado pasar a ninguno. Se dice con esas
    # palabras para no confundir una cosa con la otra.
    if len(fallos) == veces and all("locked" in f.lower() for f in fallos):
        raise RuntimeError(
            f"SQLite rechazó a los {veces}: {fallos[0]}. La prueba no ha llegado a "
            "correrse; ninguno escribió, que no es lo mismo que escribir los dos.")
    return fallos


def se_lo_dijeron(fallos: list[str], *frases: str) -> bool:
    """Uno solo escribió, y al otro se le dijo. Con las palabras que toquen.

    Los dos caminos acaban igual, pero no con las mismas palabras. Si coinciden
    de verdad en el mismo segundo, lo que salta es la regla de la base y el
    aviso dice que **otra persona acaba de** hacerlo. Si a la primera le ha
    dado tiempo a terminar antes de que la segunda empiece —y con la máquina
    cargada le da tiempo—, la segunda se encuentra el trabajo ya hecho y lo que
    se le dice es eso: que la pieza ya consta cortada, que ya hay una hoja
    abierta, que el inventario ya estaba cerrado.

    Las dos cosas son lo mismo para quien está delante y las dos están bien.
    Exigir una de las dos frases hacía que estas pruebas fallaran una de cada
    tres veces, y solo con la máquina cargada, que es justo cuando a la primera
    le da tiempo a acabar. Lo que hay que comprobar es que **una sola** escribió
    y que a la otra **se le dijo**, no con qué palabras.
    """
    return len(fallos) == 1 and any(frase.lower() in fallos[0].lower() for frase in frases)


def _usuario(s, restaurant_id, nombre):
    return (s.query(User).filter_by(restaurant_id=restaurant_id, name=nombre).one())


# =============================================== despiezar la misma pieza
def test_the_same_piece_cannot_be_butchered_twice_at_once(casa):
    """De un lomo de nueve kilos no pueden entrar dieciocho en cámara."""
    rest_id, obrador, _playa, _sierra, item_id = casa

    def cortar(s, i):
        paco = _usuario(s, rest_id, "Paco")
        meat.post_butchery(s, paco, f"TG-A{i}", ["8017"], 9.0,
                           [meat.CutRow(name="Entrecot", item_id=item_id, pieces=27,
                                        grams=300.0)],
                           waste_kg=0.9, on=HOY)

    fallos = a_la_vez(cortar)

    with db.session_scope() as s:
        hechos = [d for d in s.query(Despiece).filter_by(restaurant_id=rest_id)
                  if any(p.serial == "8017" for p in d.primals)]
        kilos = sum(l.qty for l in s.query(IngredientLot)
                    .filter(IngredientLot.restaurant_id == rest_id,
                            IngredientLot.lot_code.like("TG-A%")))
        assert len(hechos) == 1                    # un despiece, no dos
        assert kilos == pytest.approx(8.1)         # los kilos de una pieza, no de dos
        assert s.query(Primal).filter_by(restaurant_id=rest_id,
                                         serial="8017").one().status == PrimalStatus.CUT
    assert se_lo_dijeron(fallos, "otra persona", "ya estaba CUT")


def test_the_one_who_arrives_second_is_told_and_does_not_lose_the_sheet(casa):
    """El segundo carnicero se entera, y con el número del despiece que ya está."""
    rest_id, _obrador, _playa, _sierra, item_id = casa
    with db.session_scope() as s:
        paco = _usuario(s, rest_id, "Paco")
        meat.post_butchery(s, paco, "TG-0001", ["8017"], 9.0,
                           [meat.CutRow(name="Entrecot", item_id=item_id, pieces=27,
                                        grams=300.0)], waste_kg=0.9, on=HOY)
    with db.session_scope() as s:
        eva = _usuario(s, rest_id, "Eva")
        with pytest.raises(meat.MeatError, match="ya estaba CUT|otra persona"):
            meat.post_butchery(s, eva, "TG-0002", ["8017"], 9.0,
                               [meat.CutRow(name="Entrecot", item_id=item_id, pieces=27,
                                            grams=300.0)], waste_kg=0.9, on=HOY)


def test_two_butchers_opening_the_screen_at_once_do_not_fight_over_the_number(casa):
    """El número de despiece lo propone la pantalla: si chocan, se aparta solo.

    Los dos abren la hoja y los dos traen el TG-0001. Perder el despiece entero
    —las piezas, los pesos, los cortes escritos a mano— por un número que puso
    la máquina no tiene ningún sentido.
    """
    rest_id, obrador, _playa, _sierra, item_id = casa
    with db.session_scope() as s:
        s.add(Primal(restaurant_id=rest_id, serial="8018", sku="RIBEYE_AUS",
                     weight_kg=9.0, received_kg=9.0, received_date=HOY,
                     landed_usd_per_kg=30.0, piece_cost_usd=270.0,
                     frozen_use_by=HOY + timedelta(days=60), site_id=obrador,
                     status=PrimalStatus.IN_STOCK))

    def cortar(s, i):
        quien = _usuario(s, rest_id, "Paco" if i == 0 else "Eva")
        meat.post_butchery(s, quien, "TG-0001", ["8017" if i == 0 else "8018"], 9.0,
                           [meat.CutRow(name="Entrecot", item_id=item_id, pieces=27,
                                        grams=300.0)], waste_kg=0.9, on=HOY)

    fallos = a_la_vez(cortar)

    with db.session_scope() as s:
        numeros = sorted(d.tg for d in s.query(Despiece).filter_by(restaurant_id=rest_id))
    assert fallos == []
    assert numeros == ["TG-0001", "TG-0002"]       # el segundo se corrió solo


# ================================================= contar la misma cámara
def test_two_people_counting_the_same_piece_leave_it_in_writing(casa):
    """Manda el último, pero queda dicho quién contó qué y que no cuadran."""
    rest_id, obrador, _playa, _sierra, _item = casa
    with db.session_scope() as s:
        ana = _usuario(s, rest_id, "Ana")
        hoja = inventory.open_count(s, ana, CountPeriod.MONTHLY, site_id=obrador)
        inventory.record(s, ana, hoja, "8017", 8.8, lang="es")
        hoja_id = hoja.id
    with db.session_scope() as s:
        eva = _usuario(s, rest_id, "Eva")
        hoja = s.get(MeatCount, hoja_id)
        inventory.record(s, eva, hoja, "8017", 8.2, lang="es")
    with db.session_scope() as s:
        linea = next(l for l in s.get(MeatCount, hoja_id).lines if l.serial == "8017")
        assert linea.counted_kg == 8.2             # vale el que está delante ahora
        assert linea.disputed
        # La nota va en el idioma de la casa, y en español los kilos llevan coma.
        assert "Ana" in linea.note and "8,8" in linea.note
        assert linea.counted_by == _usuario(s, rest_id, "Eva").id


def test_the_same_person_counting_twice_is_not_a_fight(casa):
    """Corregir lo que uno mismo acaba de escribir es normal, no una discusión."""
    rest_id, obrador, _playa, _sierra, _item = casa
    with db.session_scope() as s:
        ana = _usuario(s, rest_id, "Ana")
        hoja = inventory.open_count(s, ana, CountPeriod.MONTHLY, site_id=obrador)
        inventory.record(s, ana, hoja, "8017", 8.8, lang="es")
        inventory.record(s, ana, hoja, "8017", 8.2, lang="es")
        linea = next(l for l in hoja.lines if l.serial == "8017")
        assert linea.counted_kg == 8.2 and not linea.disputed


def test_the_close_says_which_pieces_were_counted_twice(casa):
    rest_id, obrador, _playa, _sierra, _item = casa
    with db.session_scope() as s:
        ana = _usuario(s, rest_id, "Ana")
        eva = _usuario(s, rest_id, "Eva")
        hoja = inventory.open_count(s, ana, CountPeriod.MONTHLY, site_id=obrador)
        for linea in list(hoja.lines):
            inventory.record(s, ana, hoja, linea.serial, linea.expected_kg, lang="es")
        inventory.record(s, eva, hoja, "8017", 7.5, lang="es")

        cierre = inventory.close_count(s, ana, hoja, lang="es")
        avisos = [a.code for a in cierre.alerts]
        assert "count.disputed" in avisos
        assert "8017" in next(a for a in cierre.alerts
                              if a.code == "count.disputed").message


def test_only_one_of_two_closes_writes_the_adjustment(casa):
    """Dos veces cerrado, un solo ajuste: el libro no se lleva el dinero dos veces."""
    rest_id, obrador, _playa, _sierra, _item = casa
    with db.session_scope() as s:
        ana = _usuario(s, rest_id, "Ana")
        hoja = inventory.open_count(s, ana, CountPeriod.MONTHLY, site_id=obrador)
        for linea in list(hoja.lines):
            inventory.record(s, ana, hoja, linea.serial,
                             round(linea.expected_kg * 0.9, 3), lang="es")
        hoja_id = hoja.id

    def cerrar(s, i):
        quien = _usuario(s, rest_id, "Ana" if i == 0 else "Eva")
        inventory.close_count(s, quien, s.get(MeatCount, hoja_id), lang="es")

    fallos = a_la_vez(cerrar)

    with db.session_scope() as s:
        ajustes = (s.query(IngredientMovement)
                   .filter(IngredientMovement.restaurant_id == rest_id,
                           IngredientMovement.source == "count").all())
        refs = [m.source_ref for m in ajustes]
        assert len(refs) == len(set(refs))         # ningún serial ajustado dos veces
        assert s.get(MeatCount, hoja_id).status == CountStatus.CLOSED
    assert se_lo_dijeron(fallos, "otra persona", "ya estaba cerrado")


def test_two_managers_opening_the_count_at_once_open_one_sheet(casa):
    """Una cámara, una hoja: si no, cada uno cuenta en la suya y no cuadra nada."""
    rest_id, obrador, _playa, _sierra, _item = casa

    def abrir(s, i):
        quien = _usuario(s, rest_id, "Ana" if i == 0 else "Eva")
        inventory.open_count(s, quien, CountPeriod.MONTHLY, site_id=obrador)

    fallos = a_la_vez(abrir)

    with db.session_scope() as s:
        abiertas = (s.query(MeatCount)
                    .filter_by(restaurant_id=rest_id, status=CountStatus.OPEN).all())
        assert len(abiertas) == 1
    assert se_lo_dijeron(fallos, "otra persona", "ya hay un inventario abierto")


def test_a_chiller_can_be_counted_again_after_closing_the_sheet(casa):
    """La regla es «una hoja abierta», no «una hoja»: al cerrar se vuelve a abrir."""
    rest_id, obrador, _playa, _sierra, _item = casa
    with db.session_scope() as s:
        ana = _usuario(s, rest_id, "Ana")
        primera = inventory.open_count(s, ana, CountPeriod.MONTHLY, site_id=obrador)
        inventory.close_count(s, ana, primera, lang="es")
        segunda = inventory.open_count(s, ana, CountPeriod.MONTHLY, site_id=obrador)
        assert segunda.id != primera.id and segunda.status == CountStatus.OPEN


def test_each_chiller_has_its_own_sheet_and_they_do_not_get_mixed(casa):
    """El del local escribe en su hoja, no en la primera que aparezca."""
    rest_id, obrador, playa, _sierra, _item = casa
    with db.session_scope() as s:
        ana = _usuario(s, rest_id, "Ana")
        inventory.open_count(s, ana, CountPeriod.MONTHLY, site_id=playa)
        inventory.open_count(s, ana, CountPeriod.MONTHLY, site_id=obrador)
        abiertas = (s.query(MeatCount)
                    .filter_by(restaurant_id=rest_id, status=CountStatus.OPEN).all())
        assert len(abiertas) == 2
        assert inventory.open_now(s, rest_id, obrador).site_id == obrador
        assert inventory.open_now(s, rest_id, playa).site_id == playa


def test_four_people_counting_the_same_chiller_lose_nothing(casa):
    """Cuatro móviles en la misma hoja, cada uno con sus piezas.

    Es lo normal en una cámara grande: se reparten los estantes y cada uno va
    apuntando lo suyo. Lo que no puede pasar es que el que guarda el último
    borre lo que apuntaron los otros tres.
    """
    rest_id, obrador, _playa, _sierra, item_id = casa
    with db.session_scope() as s:
        ana = _usuario(s, rest_id, "Ana")
        for n in range(8):
            s.add(IngredientLot(restaurant_id=rest_id, item_id=item_id,
                                ingredient_id=s.get(IngredientItem,
                                                     item_id).ingredient_id,
                                serial=f"9{n:03d}", lot_code="TG-0001",
                                expiry=HOY + timedelta(days=15), received=HOY,
                                qty=4.0, qty_remaining=4.0, unit_cost=30.0,
                                site_id=obrador))
        s.flush()
        hoja = inventory.open_count(s, ana, CountPeriod.MONTHLY, site_id=obrador)
        hoja_id = hoja.id
        mios = {i: [f"9{n:03d}" for n in range(8) if n % 4 == i] for i in range(4)}

    def contar(s, i):
        quien = _usuario(s, rest_id, ["Ana", "Paco", "Eva", "Ana"][i])
        hoja = s.get(MeatCount, hoja_id)
        for serial in mios[i]:
            inventory.record(s, quien, hoja, serial, 3.5, lang="es")

    fallos = a_la_vez(contar, veces=4)

    with db.session_scope() as s:
        lineas = {l.serial: l for l in s.get(MeatCount, hoja_id).lines}
        assert fallos == []
        for n in range(8):
            assert lineas[f"9{n:03d}"].counted_kg == 3.5      # no se perdió ninguna
            assert not lineas[f"9{n:03d}"].disputed


def test_saving_the_sheet_does_not_steal_what_others_counted(casa):
    """Guardar manda toda la pantalla: repetir el número de otro no es contarlo.

    Los cuatro tienen la hoja abierta. Cuando el tercero guarda lo suyo, su
    formulario lleva también lo que ya habían escrito los otros dos. Eso no
    puede convertirle a él en quien contó aquellas piezas, ni marcar una
    discusión donde los dos números son el mismo.
    """
    rest_id, obrador, _playa, _sierra, _item = casa
    with db.session_scope() as s:
        ana = _usuario(s, rest_id, "Ana")
        eva = _usuario(s, rest_id, "Eva")
        hoja = inventory.open_count(s, ana, CountPeriod.MONTHLY, site_id=obrador)
        inventory.record(s, ana, hoja, "8017", 8.6, lang="es")
        inventory.record(s, eva, hoja, "8017", 8.6, lang="es")     # reenvía lo mismo
        linea = next(l for l in hoja.lines if l.serial == "8017")
        assert linea.counted_by == ana.id and not linea.disputed


def test_four_people_counting_the_same_piece_keep_the_last_one(casa):
    """Y si los cuatro cuentan la misma pieza, vale una y se dice que hubo lío."""
    rest_id, obrador, _playa, _sierra, _item = casa
    with db.session_scope() as s:
        ana = _usuario(s, rest_id, "Ana")
        hoja = inventory.open_count(s, ana, CountPeriod.MONTHLY, site_id=obrador)
        hoja_id = hoja.id

    def contar(s, i):
        quien = _usuario(s, rest_id, ["Ana", "Paco", "Eva", "Ana"][i])
        inventory.record(s, quien, s.get(MeatCount, hoja_id), "8017", 8.0 + i * 0.1,
                         lang="es")

    fallos = a_la_vez(contar, veces=4)

    with db.session_scope() as s:
        linea = next(l for l in s.get(MeatCount, hoja_id).lines if l.serial == "8017")
        assert fallos == []
        assert linea.counted_kg in (8.0, 8.1, 8.2, 8.3)       # una de las cuatro
        assert linea.disputed                                 # y queda dicho


def test_nobody_loses_their_count_when_all_four_read_before_anyone_writes(casa):
    """El caso de verdad: los cuatro miran la hoja y luego escriben.

    La prueba de arriba deja que los hilos caigan donde caigan, y casi siempre
    uno guarda antes de que el siguiente mire: entonces el segundo ve a alguien
    con quien discutir y se marca el lío. Pero una de cada cinco veces los
    cuatro miraban la línea **sin contar** —que es lo que pasa cuando dos
    móviles le dan a guardar en el mismo segundo dentro de la cámara— y
    entonces ninguno veía a nadie: se guardaban los cuatro números encima del
    anterior y la hoja quedaba diciendo que la contó uno solo y limpio.

    No es que se perdiera un aviso: es que el inventario mentía justo en el
    caso para el que se escribió el aviso. Aquí se fuerza ese solape con una
    barrera, para que no dependa de cómo vaya de cargada la máquina.
    """
    rest_id, obrador, _playa, _sierra, _item = casa
    with db.session_scope() as s:
        ana = _usuario(s, rest_id, "Ana")
        hoja_id = inventory.open_count(s, ana, CountPeriod.MONTHLY, site_id=obrador).id

    leido = threading.Barrier(3, timeout=60)
    nombres = ["Ana", "Paco", "Eva"]

    def contar(s, i):
        quien = _usuario(s, rest_id, nombres[i])
        hoja = s.get(MeatCount, hoja_id)
        list(hoja.lines)                    # se lee la hoja, como al pintarla
        leido.wait()                        # y solo entonces escribe nadie
        inventory.record(s, quien, hoja, "8017", 8.0 + i * 0.1, lang="es")

    fallos = a_la_vez(contar, veces=3)

    with db.session_scope() as s:
        linea = next(l for l in s.get(MeatCount, hoja_id).lines if l.serial == "8017")
        assert fallos == []                             # nadie se queda fuera
        assert linea.counted_kg in (8.0, 8.1, 8.2)      # manda uno, el último
        assert linea.disputed, "tres recuentos distintos y la hoja no dice nada"
        # Y con los números de los otros dos escritos: quien cierre el
        # inventario tiene que saber a quién preguntarle, no solo que hubo lío.
        assert linea.note and linea.note.count("kg") >= 2, linea.note
        assert sum(n in linea.note for n in nombres) >= 2, linea.note


def test_a_piece_that_turns_up_is_never_lost_when_two_write_it_at_once(casa):
    """Dos apuntan a la vez una pieza que no estaba en la lista.

    La hoja no admite dos líneas con el mismo número, así que la segunda
    reventaba con un fallo de la base —un 500 en la cara, y su recuento a la
    basura— en vez de entender lo único que había pasado: que llegó segunda.
    """
    rest_id, obrador, _playa, _sierra, _item = casa
    with db.session_scope() as s:
        ana = _usuario(s, rest_id, "Ana")
        hoja_id = inventory.open_count(s, ana, CountPeriod.MONTHLY, site_id=obrador).id

    leido = threading.Barrier(3, timeout=60)
    nombres = ["Ana", "Paco", "Eva"]

    def apuntar(s, i):
        quien = _usuario(s, rest_id, nombres[i])
        hoja = s.get(MeatCount, hoja_id)
        list(hoja.lines)
        leido.wait()
        inventory.record(s, quien, hoja, "APARECIDA", 2.0 + i, lang="es")

    fallos = a_la_vez(apuntar, veces=3)

    with db.session_scope() as s:
        lineas = [l for l in s.get(MeatCount, hoja_id).lines if l.serial == "APARECIDA"]
        assert fallos == [], fallos          # ninguno revienta
        assert len(lineas) == 1              # y una sola línea, no tres
        assert lineas[0].counted_kg in (2.0, 3.0, 4.0)
        assert lineas[0].disputed            # con lo que contaron los otros


def test_four_people_writing_down_the_same_piece_that_was_not_on_the_list(casa):
    """Aparece una pieza que no estaba en la hoja y la apuntan cuatro a la vez."""
    rest_id, obrador, _playa, _sierra, _item = casa
    with db.session_scope() as s:
        ana = _usuario(s, rest_id, "Ana")
        hoja_id = inventory.open_count(s, ana, CountPeriod.MONTHLY, site_id=obrador).id

    def apuntar(s, i):
        quien = _usuario(s, rest_id, ["Ana", "Paco", "Eva", "Ana"][i])
        inventory.record(s, quien, s.get(MeatCount, hoja_id), "APARECIDA", 2.0, lang="es")

    a_la_vez(apuntar, veces=4)

    with db.session_scope() as s:
        lineas = [l for l in s.get(MeatCount, hoja_id).lines if l.serial == "APARECIDA"]
        assert len(lineas) == 1 and lineas[0].counted_kg == 2.0


# ==================================================== cerrar el turno dos veces
def test_closing_the_shift_twice_does_not_take_the_meat_out_twice(casa):
    rest_id, _obrador, _playa, _sierra, _item = casa
    with db.session_scope() as s:
        ana = _usuario(s, rest_id, "Ana")
        defrost.intake(s, ana, "8017-01", pieces=20, total_kg=6.0, on=HOY, shift="noche")
        defrost.count(s, ana, "8017-01", pieces=4, total_kg=1.2, on=HOY, shift="noche")
        defrost.close(s, ana, on=HOY, shift="noche", lang="es")
        una = s.query(IngredientLot).filter_by(restaurant_id=rest_id,
                                               serial="8017-01").one().qty_remaining
        segundo = defrost.close(s, ana, on=HOY, shift="noche", lang="es")
        dos = s.query(IngredientLot).filter_by(restaurant_id=rest_id,
                                               serial="8017-01").one().qty_remaining
    assert una == dos                              # la carne sale una vez
    assert "8017-01" in segundo.already            # y se dice que ya había salido


def test_two_people_closing_the_shift_at_once_only_close_it_once(casa):
    rest_id, _obrador, _playa, _sierra, _item = casa
    with db.session_scope() as s:
        ana = _usuario(s, rest_id, "Ana")
        defrost.intake(s, ana, "8017-01", pieces=20, total_kg=6.0, on=HOY, shift="noche")
        defrost.count(s, ana, "8017-01", pieces=4, total_kg=1.2, on=HOY, shift="noche")

    def cerrar(s, i):
        quien = _usuario(s, rest_id, "Ana" if i == 0 else "Eva")
        defrost.close(s, quien, on=HOY, shift="noche", lang="es")

    fallos = a_la_vez(cerrar)

    with db.session_scope() as s:
        from thegrill.models import ShiftClosure
        assert s.query(ShiftClosure).filter_by(restaurant_id=rest_id).count() == 1
        lote = s.query(IngredientLot).filter_by(restaurant_id=rest_id,
                                                serial="8017-01").one()
        assert lote.qty_remaining == pytest.approx(5.2)     # 10 − 4.8, una vez
    # A la segunda le puede pasar una de dos cosas, y las dos valen: o se le
    # dice que la otra está cerrando en este momento, o rehace el cuadre sin
    # volver a sacar carne. Lo que no vale es que salga dos veces.
    assert len(fallos) <= 1
    assert not fallos or "en este momento" in fallos[0]


# ======================================================== mandar y recibir
def test_a_piece_cannot_be_sent_to_two_outlets_at_once(casa):
    """Las dos la ven en el obrador y las dos la mandan: solo sale una.

    Las dos mandan «desde el obrador», que es donde la tienen en la pantalla, y
    ahí se cierran las dos puertas: si coinciden en el mismo segundo, la orden
    de escribir lleva dentro dónde estaba y una de las dos no encuentra la
    pieza donde la dejó; y si una acaba antes de que la otra empiece, la
    segunda ve que ya no está en el obrador y tampoco la manda. Sin lo
    segundo quedaba escrito un viaje que no existió —«de Playa a Sierra» de una
    pieza que en Playa no estuvo nunca— y en Playa esperando una pieza que no
    iba.
    """
    rest_id, obrador, playa, sierra, _item = casa

    def mandar(s, i):
        ana = _usuario(s, rest_id, "Ana")
        sites.send_primal(s, ana, "8017", playa if i == 0 else sierra, on=HOY,
                          desde=obrador)

    fallos = a_la_vez(mandar)

    with db.session_scope() as s:
        albaranes = s.query(Transfer).filter_by(restaurant_id=rest_id).all()
        pieza = s.query(Primal).filter_by(restaurant_id=rest_id, serial="8017").one()
        assert len(albaranes) == 1
        assert pieza.site_id == albaranes[0].to_site_id      # está donde dice el papel
    assert len(fallos) == 1


def test_a_lot_never_sends_more_kilos_than_it_has(casa):
    """Dos envíos de seis kilos de un lote de diez: no salen doce."""
    rest_id, _obrador, playa, sierra, _item = casa

    def mandar(s, i):
        ana = _usuario(s, rest_id, "Ana")
        sites.send_cut(s, ana, "8017-01", 6.0, playa if i == 0 else sierra, on=HOY)

    a_la_vez(mandar)

    with db.session_scope() as s:
        lotes = s.query(IngredientLot).filter_by(restaurant_id=rest_id).all()
        assert all(l.qty_remaining >= -1e-9 for l in lotes)
        assert sum(l.qty_remaining for l in lotes) == pytest.approx(10.0)


def test_two_receptions_at_once_do_not_collide_over_the_number(casa):
    """La numeración la pone la casa: si dos muelles coinciden, se renumera."""
    rest_id, _obrador, _playa, _sierra, _item = casa

    def recibir(s, i):
        ana = _usuario(s, rest_id, "Ana")
        meat.receive_primals(s, ana, "", [meat.PrimalRow(serial="", kg=10.0,
                                                         price_kg=12.0, sku="Lomo")],
                             received=HOY)

    fallos = a_la_vez(recibir, veces=3)

    with db.session_scope() as s:
        seriales = [p.serial for p in s.query(Primal).filter_by(restaurant_id=rest_id)]
    assert fallos == []
    assert len(seriales) == len(set(seriales)) == 4       # la de la casa y las tres


def test_many_writing_at_once_do_not_get_a_locked_database(casa):
    """Ocho guardados a la vez: hacen cola, no rebotan."""
    rest_id, _obrador, _playa, _sierra, _item = casa

    def recibir(s, i):
        ana = _usuario(s, rest_id, "Ana")
        meat.receive_primals(s, ana, "", [meat.PrimalRow(serial=f"Z{i}", kg=5.0,
                                                         price_kg=9.0, sku="Aguja")],
                             received=HOY)

    assert a_la_vez(recibir, veces=8) == []


# ====================================== cuatro móviles de verdad, por la web
def test_four_phones_saving_the_same_sheet_over_the_web(tmp_path, monkeypatch):
    """Lo mismo, pero por la pantalla: cuatro sesiones distintas a la vez.

    Cada uno guarda **toda** la hoja, que es lo que manda el navegador, y cada
    uno trae lo suyo escrito. Al final tienen que estar las cuatro cosas.
    """
    from fastapi.testclient import TestClient

    from thegrill.meat import app as meatapp
    from thegrill.models import IngredientItem
    from tests.meat_helpers import add_user, csrf_from, signup

    monkeypatch.setenv("GRILL_INSECURE_COOKIE", "1")
    db.init_engine(f"sqlite:///{tmp_path/'web.db'}")
    db.create_all()
    cabeceras = {"accept-language": "es"}

    with TestClient(meatapp.app, follow_redirects=False, headers=cabeceras) as jefe:
        signup(jefe, "Hotel Marina", "albano@marina.com", "Albano", "es")
        with db.session_scope() as s:
            rest_id = s.query(User).filter_by(email="albano@marina.com").one().restaurant_id
            item, corte = _articulo(s, rest_id)
            for n in range(8):
                s.add(IngredientLot(restaurant_id=rest_id, item_id=item.id,
                                    ingredient_id=corte, serial=f"9{n:03d}",
                                    lot_code="TG-0001", expiry=HOY + timedelta(days=15),
                                    received=HOY, qty=4.0, qty_remaining=4.0,
                                    unit_cost=30.0))
            s.flush()

        pantalla = jefe.get("/inventario").text
        jefe.post("/inventario/abrir",
                  data={"csrf": csrf_from(pantalla), "period": "MONTHLY"})

        gente = [jefe] + [add_user(jefe, email=f"p{i}@marina.com", name=f"Persona {i}")
                          for i in range(3)]
        hojas = [c.get("/inventario").text for c in gente]

        def guardar(i):
            datos = {"csrf": csrf_from(hojas[i]), "hoja": "1"}
            for n in range(8):
                if n % 4 == i:
                    datos[f"kg:9{n:03d}"] = "3.5"
            gente[i].post("/inventario/contar", data=datos)

        hilos = [threading.Thread(target=guardar, args=(i,)) for i in range(4)]
        for h in hilos:
            h.start()
        for h in hilos:
            h.join()

    with db.session_scope() as s:
        hoja = s.query(MeatCount).filter_by(restaurant_id=rest_id).one()
        contadas = {l.serial: l.counted_kg for l in hoja.lines if l.counted_kg is not None}
        assert len(contadas) == 8              # las ocho, de los cuatro móviles
        assert set(contadas.values()) == {3.5}
        assert len({l.counted_by for l in hoja.lines if l.counted_by}) == 4


def test_a_butchery_of_three_that_loses_one_gives_the_others_back(casa, monkeypatch):
    """O se cogen las tres piezas, o no se coge ninguna.

    La comprobación de antes mira cómo están las piezas; el problema es lo que
    pasa **entre** esa mirada y la orden de cogerlas, que es donde vive la
    carrera de verdad. Con tres en la mesa, si la segunda se la llevaba otra
    persona en ese hueco, la primera se quedaba marcada como cortada **sin un
    solo corte detrás**: en la cámara había una pieza entera que el programa
    daba por despiezada, y el de fuera no tenía manera de enterarse hasta el
    recuento del mes.
    """
    rest_id, obrador, _playa, _sierra, item_id = casa
    with db.session_scope() as s:
        for serial in ("8018", "8019"):
            s.add(Primal(restaurant_id=rest_id, serial=serial, sku="RIBEYE_AUS",
                         weight_kg=9.0, received_kg=9.0, received_date=HOY,
                         landed_usd_per_kg=30.0, piece_cost_usd=270.0,
                         frozen_use_by=HOY + timedelta(days=60),
                         site_id=obrador, status=PrimalStatus.IN_STOCK))

    # La segunda pieza se la lleva otra persona justo entre la comprobación y
    # la orden de cogerla. Es el único hueco en el que puede pasar.
    de_verdad = butchery.locking.claim
    cogidas = {"n": 0}

    def a_medias(session, model, ident, expected, values):
        if values.get("status") == PrimalStatus.CUT:
            cogidas["n"] += 1
            if cogidas["n"] == 2:
                return False
        return de_verdad(session, model, ident, expected, values)

    monkeypatch.setattr(butchery.locking, "claim", a_medias)

    with db.session_scope() as s:
        paco = _usuario(s, rest_id, "Paco")
        with pytest.raises(meat.MeatError, match="otra persona"):
            meat.post_butchery(s, paco, "TG-TRES", ["8017", "8018", "8019"], 27.0,
                               [meat.CutRow(name="Entrecot", item_id=item_id,
                                            pieces=81, grams=300.0)],
                               waste_kg=2.7, on=HOY)
        # La pantalla lo enseña y contesta: la petición termina bien y la
        # sesión se guarda. Eso es lo que dejaba las piezas marcadas.

    with db.session_scope() as s:
        estados = {p.serial: p.status for p in s.query(Primal)
                   .filter(Primal.restaurant_id == rest_id,
                           Primal.serial.in_(["8017", "8018", "8019"]))}
        # Ninguna se queda cortada: o las tres, o ninguna.
        assert set(estados.values()) == {PrimalStatus.IN_STOCK}
        # Y ni un despiece ni un corte de un despiece que no llegó a existir.
        assert not [d for d in s.query(Despiece).filter_by(restaurant_id=rest_id)
                    if d.tg == "TG-TRES"]
        assert s.query(IngredientLot).filter(
            IngredientLot.lot_code == "TG-TRES").count() == 0
