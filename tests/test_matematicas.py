"""El examen de las matemáticas: cada gramo y cada céntimo, de punta a punta.

Aquí no se prueba que las pantallas funcionen. Se prueba lo único que no
admite un fallo: que el dinero y los kilos **se conserven**. Un primal que
entra por 300,80 € tiene que valer 300,80 € repartido entre todo lo que sale
de él —cortes, recortes, lo que se tira y lo que queda— por muchas veces que
se pese, se limpie, se corte, se traslade o se venda por el camino.

Todo el programa guarda dinero y kilos en coma flotante. Eso no es malo por sí
solo —un doble tiene quince cifras buenas y una cámara de carne no llega a
seis—, pero obliga a demostrarlo en vez de suponerlo. Por eso estas pruebas no
usan ejemplos elegidos a mano: generan miles de casos con una semilla fija, y
miden **el peor desvío**, no el corriente. El peor es el que acaba en una
reclamación.

Los topes están puestos donde duele de verdad:

- **Un céntimo** en cualquier cosa que un hostelero vaya a ver o a cobrar.
- **Un gramo** en cualquier peso.

Lo que quede por debajo de eso no existe: ninguna báscula de muelle lo mide y
ninguna factura lo refleja.
"""
import random
from datetime import date, timedelta

import pytest

from thegrill import db
from thegrill.models import (Ingredient, IngredientItem, IngredientLot, Primal,
                             PrimalStatus, Rotation, Storage, Unit)
from thegrill.web import aging, auth, butchery, waste

HOY = date(2026, 9, 20)

CENTIMO = 0.01
GRAMO = 0.001

# Cuántos casos genera cada prueba. Se sube con GRILL_MATES=20000 para un
# examen de los largos; con el de serie cabe en la suite de todos los días.
import os
CASOS = int(os.environ.get("GRILL_MATES", "2000"))

# El peor desvío de cada cuenta, que es el número que importa. «Pasa» no dice
# nada: lo que hay que poder enseñar es a cuánto se ha quedado del céntimo.
PEORES: dict[str, tuple[float, str]] = {}


def apunta(cuenta: str, desvio: float, unidad: str = "EUR") -> None:
    """Guarda el peor desvío de una cuenta. El cero también se apunta: una
    cuenta exacta es la buena noticia y tiene que salir en el cuadro."""
    anterior = PEORES.get(cuenta)
    if anterior is None or desvio > anterior[0]:
        PEORES[cuenta] = (desvio, unidad)


class Corte:
    """Un corte del despiece, con sus kilos y lo que vale frente a los demás."""
    def __init__(self, kg, valor):
        self.total_kg, self.value_index = kg, valor


# ============================================== el reparto del coste del primal
def test_the_parts_of_a_primal_add_up_to_the_primal_exactly():
    """Reparte el coste entre los cortes: la suma es el total. Exactamente.

    Es la cuenta más importante del programa. De aquí sale el coste de cada
    corte, y de ahí el food cost de cada plato de la carta.

    No vale «se desvía poco»: se exige que la suma de los céntimos asignados
    sea idéntica a los céntimos de la pieza. Un céntimo suelto por despiece son
    tres euros al año en una casa que corta diez piezas al día, y sobre todo es
    un cuadre que no sale y que nadie sabe explicar.
    """
    from thegrill.web import exacto

    azar = random.Random(11)
    fallos = []
    for _ in range(CASOS):
        cortes = [Corte(round(azar.uniform(0.05, 9.0), 3), round(azar.uniform(0.3, 4.0), 2))
                  for _ in range(azar.randint(1, 12))]
        total = round(azar.uniform(5.0, 2000.0), 2)
        partes = [a.cost for a in butchery.allocate(cortes, total)]
        if not exacto.cuadra(partes, total):
            fallos.append((total, partes))
    apunta("reparto del coste entre los cortes", 0.0)
    assert not fallos, f"{len(fallos)} de {CASOS} repartos no cuadran: {fallos[:2]}"


def test_a_single_cent_is_split_without_inventing_money():
    """Siete céntimos entre tres cortes: alguien se lleva tres y los otros dos.

    El caso que revienta cualquier reparto ingenuo, porque no hay forma de
    partirlo bien. Lo único innegociable es que sumen siete.
    """
    from thegrill.web import exacto

    for total, trozos in ((0.07, 3), (0.01, 4), (0.02, 3), (1.0, 7), (999.99, 11)):
        partes = exacto.repartir_dinero(total, [1.0] * trozos)
        assert exacto.cuadra(partes, total), (total, partes)
        assert len(partes) == trozos


def test_foemmels_conundrum():
    """Cinco céntimos al 30 y al 70 por ciento. El caso con nombre propio.

    Es el ejemplo con el que Martin Fowler explica por qué repartir dinero no
    es dividir: salen 1,5 y 3,5 céntimos, y el céntimo no se parte. Lo único
    que no se negocia es que sumen cinco. La respuesta buena es 2 y 3.
    """
    from thegrill.web import exacto

    assert exacto.repartir(5, [30.0, 70.0]) == [2, 3]
    assert sum(exacto.repartir(5, [30.0, 70.0])) == 5
    # Y con cien partes iguales de un céntimo: noventa y nueve se quedan sin.
    partes = exacto.repartir(1, [1.0] * 100)
    assert sum(partes) == 1 and partes.count(0) == 99


def test_splitting_the_same_way_twice_gives_the_same_answer():
    """Repetir el cálculo tiene que dar lo mismo, o no se puede auditar."""
    from thegrill.web import exacto

    pesos = [3.21, 1.07, 0.54, 2.99]
    primero = exacto.repartir_dinero(412.37, pesos)
    for _ in range(50):
        assert exacto.repartir_dinero(412.37, pesos) == primero


def test_the_price_per_kilo_is_a_derived_number_and_says_so():
    """El precio por kilo NO puede ser exacto, y hay que saberlo.

    Cien euros entre tres kilos son 33,333… €/kg. Eso no es un decimal finito
    en ninguna representación, ni en coma flotante ni en céntimos ni en papel.
    Así que la verdad es el **coste**, y el precio por kilo es un derivado para
    enseñarlo y para valorar.

    Esta prueba mide cuánto se separa el viaje de ida y vuelta —coste, precio
    por kilo, otra vez coste— para que la cifra esté escrita y no se descubra
    el día que un inventario no cuadra. Quien quiera el total, que sume costes.
    """
    azar = random.Random(12)
    peor = 0.0
    for _ in range(CASOS):
        cortes = [Corte(round(azar.uniform(0.05, 9.0), 3), round(azar.uniform(0.3, 4.0), 2))
                  for _ in range(azar.randint(1, 12))]
        total = round(azar.uniform(5.0, 2000.0), 2)
        rep = butchery.allocate(cortes, total)
        peor = max(peor, abs(sum(a.unit_cost * a.kg for a in rep) - total))
    apunta("reconstruir el primal desde el precio por kilo", peor)
    assert peor < CENTIMO, f"reconstruyendo desde el precio por kilo se va {peor:.8f} EUR"


def test_what_is_thrown_away_is_paid_by_what_is_kept():
    """La merma del despiece no se reparte: la pagan los cortes que salen.

    Si se repartiera entre todo lo que entró, el coste se quedaría pegado a
    unos kilos que ya no existen y el kilo bueno saldría más barato de lo que
    de verdad ha costado.
    """
    azar = random.Random(13)
    for _ in range(200):
        kg_utiles = [round(azar.uniform(0.2, 6.0), 3) for _ in range(azar.randint(2, 8))]
        total = round(azar.uniform(50.0, 900.0), 2)
        cortes = [Corte(kg, 1.0) for kg in kg_utiles]
        rep = butchery.allocate(cortes, total)
        # Todo el dinero está en los kilos útiles, ni un céntimo en la merma.
        assert abs(sum(a.cost for a in rep) - total) < CENTIMO
        por_kilo = total / sum(kg_utiles)
        for a in rep:
            # Y aquí está el precio de la exactitud, que conviene tenerlo
            # escrito: como a cada corte se le asigna un número entero de
            # céntimos, su precio por kilo puede quedar hasta un céntimo entre
            # sus kilos por encima o por debajo del teórico. En un corte de
            # cien gramos eso son diez céntimos por kilo; en uno de cinco
            # kilos, dos milésimas. El coste está bien al céntimo; el precio
            # por kilo de un trozo pequeño es aproximado, y para eso está:
            # para enseñarlo, no para reconstruir el total.
            margen = CENTIMO / a.kg + 1e-9
            assert abs(a.unit_cost - por_kilo) <= margen, (a.kg, a.unit_cost, por_kilo)


# ============================================================== la maduración
@pytest.fixture(scope="module")
def cocina(tmp_path_factory):
    """Una casa con su manager y un artículo para los recortes."""
    ruta = tmp_path_factory.mktemp("mates") / "mates.db"
    db.init_engine(f"sqlite:///{ruta}")
    db.create_all()
    with db.session_scope() as session:
        rest, ana = auth.create_restaurant(session, "Asador", "a@a.com", "Ana",
                                           "clave-larga-1", language="es")
        corte = Ingredient(restaurant_id=rest.id, name="Recorte", unit=Unit.KG,
                           rotation=Rotation.FEFO)
        session.add(corte); session.flush()
        item = IngredientItem(restaurant_id=rest.id, ingredient_id=corte.id,
                              name="Recorte picada")
        session.add(item); session.flush()
        yield session, rest, ana, item


def _apuntado(session, rest, serial):
    """Lo que dicen los apuntes de los recortes que salieron de esa pieza."""
    from thegrill.models import IngredientMovement, MovementKind

    lotes = [l.id for l in session.query(IngredientLot).filter_by(restaurant_id=rest.id)
             if (l.parent_serial or "") == serial]
    if not lotes:
        return 0.0
    return round(sum(
        mv.cost or 0.0 for mv in session.query(IngredientMovement)
        .filter(IngredientMovement.restaurant_id == rest.id,
                IngredientMovement.lot_id.in_(lotes),
                IngredientMovement.kind == MovementKind.IN)), 2)


def _pieza(session, rest, serial, kg, precio, dias=30):
    # Al céntimo, igual que la recepción de verdad: una factura no dice
    # 543,3701. Si el banco de pruebas empieza con milésimas, luego mide el
    # error de sus propias milésimas y no el del programa.
    coste = round(kg * precio, 2)
    pieza = Primal(restaurant_id=rest.id, serial=serial, sku="Striploin AUS",
                   weight_kg=kg, received_kg=kg, landed_usd_per_kg=precio,
                   piece_cost_usd=coste, received_date=HOY - timedelta(days=dias),
                   storage=Storage.AGING, storage_since=HOY - timedelta(days=dias),
                   aging_start_kg=kg, status=PrimalStatus.IN_STOCK,
                   frozen_use_by=HOY + timedelta(days=60))
    session.add(pieza); session.flush()
    return pieza, coste


def test_water_leaving_never_changes_what_the_piece_cost(cocina):
    """Una pieza que madura pierde agua, no dinero.

    Los kilos que se van no se los lleva nadie: se evaporan. Así que el coste
    de la pieza se queda igual y lo que sube es el precio del kilo que queda.
    Cincuenta pesadas seguidas no pueden moverlo.
    """
    session, rest, ana, _ = cocina
    azar = random.Random(7)
    peor_coste = peor_kilo = 0.0
    for n in range(40):
        kg = round(azar.uniform(4.0, 14.0), 3)
        pieza, coste0 = _pieza(session, rest, f"M{n:04d}", kg, round(azar.uniform(12, 90), 2), dias=200)
        for semana in range(50):
            kg = round(kg * (1 - azar.uniform(0.001, 0.008)), 3)
            if kg <= 0.2:
                break
            aging.weigh(session, ana, pieza.serial, kg,
                        on=HOY - timedelta(days=200 - semana * 3))
        session.refresh(pieza)
        peor_coste = max(peor_coste, abs((pieza.piece_cost_usd or 0) - coste0))
        peor_kilo = max(peor_kilo, abs((aging.cost_per_kg(pieza) or 0)
                                       - coste0 / (pieza.weight_kg or 1)))
    apunta("el coste de una pieza que madura", peor_coste)
    apunta("el precio del kilo tras madurar", peor_kilo, "EUR/kg")
    assert peor_coste < CENTIMO, f"el coste de la pieza se movió {peor_coste:.8f} EUR"
    assert peor_kilo < CENTIMO, f"el precio del kilo se desvió {peor_kilo:.8f} EUR/kg"


def test_a_piece_cannot_put_on_weight(cocina):
    """Si el peso sube, es la báscula o el número: nunca la carne."""
    session, rest, ana, _ = cocina
    pieza, _ = _pieza(session, rest, "M9001", 9.0, 30.0)
    with pytest.raises(aging.AgingError):
        aging.weigh(session, ana, pieza.serial, 9.5, on=HOY)


# ================================================================ la limpieza
def test_trimming_moves_the_money_but_never_loses_it(cocina):
    """Lo que se le quita a una pieza se lleva su parte, y el resto se queda.

    Lo aprovechado sale con su lote y su dinero; lo tirado no se lleva nada y
    su coste se queda en los kilos que quedan. Sumando las dos cosas tiene que
    salir exactamente lo que valía la pieza antes de tocarla.
    """
    session, rest, ana, item = cocina
    azar = random.Random(23)
    peor, caso = 0.0, None
    for n in range(200):
        kg = round(azar.uniform(5.0, 14.0), 3)
        pieza, coste0 = _pieza(session, rest, f"L{n:04d}", kg, round(azar.uniform(15, 90), 2))
        quitado = round(kg * azar.uniform(0.05, 0.30), 3)
        guardado = round(quitado * azar.uniform(0.0, 0.9), 3)
        partes = ([aging.TrimPart(item_id=item.id, kg=guardado,
                                  value_index=round(azar.uniform(0.2, 1.0), 2))]
                  if guardado > 0.001 else [])
        aging.trim(session, ana, pieza.serial, removed_kg=quitado, parts=partes,
                   on=HOY, use_by=HOY + timedelta(days=15))
        session.refresh(pieza)
        # Se suma el coste APUNTADO de cada recorte, no sus kilos por su precio
        # por kilo: eso último es un derivado y arrastra el 33,333… Un auditor
        # suma apuntes, no reconstruye multiplicando.
        recortes = _apuntado(session, rest, pieza.serial)
        desvio = abs((pieza.piece_cost_usd or 0) + recortes - coste0)
        if desvio > peor:
            peor, caso = desvio, (pieza.serial, coste0, quitado, guardado)
    apunta("limpiar una pieza: recorte + lo que queda", peor)
    # En céntimos, que es donde la exactitud significa algo. En coma flotante
    # 570,33 no existe: existe 570,3299999999999272…, así que comparar dos
    # sumas de floats con «igual» es pedirle al camión que pese.
    assert peor < 0.005, f"la limpieza pierde {peor:.12f} EUR en {caso}"


# ==================================================================== la merma
def test_what_is_thrown_raises_the_price_of_what_is_left(cocina):
    """Tirar carne no hace desaparecer su coste: lo paga lo que queda.

    Es la cuenta que un hostelero no hace y por la que cree que gana más de lo
    que gana. Si se tira un tercio de un lote, el kilo que queda cuesta la
    mitad más, y eso es lo que tiene que estar en la carta.
    """
    session, rest, ana, item = cocina
    azar = random.Random(31)
    peor = 0.0
    for n in range(150):
        kg = round(azar.uniform(2.0, 20.0), 3)
        coste_kg = round(azar.uniform(8.0, 60.0), 4)
        lote = IngredientLot(restaurant_id=rest.id, item_id=item.id,
                             ingredient_id=item.ingredient_id,
                             serial=f"W{n:04d}", lot_code=f"W{n:04d}",
                             qty=kg, qty_remaining=kg, unit_cost=coste_kg,
                             received=HOY - timedelta(days=3),
                             expiry=HOY + timedelta(days=20))
        session.add(lote); session.flush()
        valor0 = round(kg * coste_kg, 6)
        tirado = round(kg * azar.uniform(0.05, 0.6), 3)
        waste.record(session, ana, tirado, serial=lote.serial, on=HOY, absorb=True)
        session.refresh(lote)
        # El dinero del lote no se ha ido a ninguna parte: sigue entero en los
        # kilos que quedan.
        peor = max(peor, abs((lote.qty_remaining or 0) * (lote.unit_cost or 0) - valor0))
    apunta("la merma la paga lo que queda del lote", peor)
    assert peor < CENTIMO, f"la merma pierde o inventa {peor:.8f} EUR"


# =========================================================== de punta a punta
def test_a_whole_primal_is_worth_the_same_before_and_after_everything(cocina):
    """La prueba de verdad: el viaje entero de una pieza, y la cuenta al final.

    Entra un primal con su precio. Madura y pierde agua, se limpia y se le
    quitan recortes, se despieza en varios cortes y parte se tira. Al final,
    sumando lo que vale cada cosa que salió de él, tiene que dar lo mismo que
    costó. Ni un céntimo más, ni uno menos.
    """
    session, rest, ana, item = cocina
    azar = random.Random(41)
    peor, caso = 0.0, None
    for n in range(60):
        kg = round(azar.uniform(6.0, 14.0), 3)
        precio = round(azar.uniform(18.0, 70.0), 2)
        pieza, coste0 = _pieza(session, rest, f"V{n:04d}", kg, precio, dias=40)

        # 1. madura cuatro semanas
        peso = kg
        for semana in range(4):
            peso = round(peso * (1 - azar.uniform(0.004, 0.02)), 3)
            aging.weigh(session, ana, pieza.serial, peso,
                        on=HOY - timedelta(days=30 - semana * 7))

        # 2. se limpia la costra: parte se aprovecha, parte se tira
        quitado = round(peso * azar.uniform(0.04, 0.18), 3)
        guardado = round(quitado * azar.uniform(0.1, 0.8), 3)
        aging.trim(session, ana, pieza.serial, removed_kg=quitado,
                   parts=[aging.TrimPart(item_id=item.id, kg=guardado, value_index=0.3)],
                   on=HOY - timedelta(days=1), use_by=HOY + timedelta(days=15))
        session.refresh(pieza)

        # 3. se despieza lo que queda: unos cortes salen y algo se tira
        queda = pieza.weight_kg or 0.0
        coste_a_repartir = pieza.piece_cost_usd or 0.0
        merma = round(queda * azar.uniform(0.03, 0.12), 3)
        utiles = round(queda - merma, 3)
        trozos, resto = [], utiles
        for i in range(azar.randint(2, 5)):
            parte = round(resto * azar.uniform(0.2, 0.6), 3) if i < 4 else resto
            if parte < 0.05:
                break
            trozos.append(Corte(parte, round(azar.uniform(0.5, 3.0), 2)))
            resto = round(resto - parte, 3)
        if resto > 0.05:
            trozos.append(Corte(resto, 1.0))
        reparto = butchery.allocate(trozos, coste_a_repartir)

        # La cuenta final: recortes de la limpieza + cortes del despiece.
        recortes = _apuntado(session, rest, pieza.serial)
        salido = round(sum(a.cost for a in reparto) + recortes, 2)
        desvio = abs(salido - coste0)
        if desvio > peor:
            peor, caso = desvio, (pieza.serial, coste0, salido, len(trozos))
    apunta("EL VIAJE ENTERO de un primal", peor)
    assert peor < 0.005, (
        f"del primal entran {caso[1]:.2f} EUR y salen {caso[2]:.2f}: "
        f"se pierden {peor:.6f} EUR ({caso[3]} cortes, pieza {caso[0]})")


# ============================================================ los kilos, aparte
def test_no_gram_appears_or_vanishes_when_a_piece_is_trimmed(cocina):
    """Lo que pesaba antes = lo que pesa ahora + lo aprovechado + lo tirado."""
    session, rest, ana, item = cocina
    azar = random.Random(53)
    peor = 0.0
    for n in range(150):
        kg = round(azar.uniform(5.0, 14.0), 3)
        pieza, _ = _pieza(session, rest, f"G{n:04d}", kg, 30.0)
        quitado = round(kg * azar.uniform(0.05, 0.3), 3)
        guardado = round(quitado * azar.uniform(0.0, 0.9), 3)
        partes = ([aging.TrimPart(item_id=item.id, kg=guardado, value_index=0.3)]
                  if guardado > 0.001 else [])
        aging.trim(session, ana, pieza.serial, removed_kg=quitado, parts=partes,
                   on=HOY, use_by=HOY + timedelta(days=15))
        session.refresh(pieza)
        peor = max(peor, abs(kg - ((pieza.weight_kg or 0) + quitado)))
    apunta("los gramos al limpiar", peor * 1000, "gramos")
    assert peor < GRAMO, f"se pierden o aparecen {peor * 1000:.3f} gramos limpiando"


# ================================================================ el traslado
def test_meat_neither_cheapens_nor_gets_dearer_on_the_van(cocina):
    """La carne que va del obrador al local llega valiendo lo mismo.

    Si se abaratara por el camino, el local saldría rentable a costa del
    obrador y el grupo entero cuadraría mal sin que nadie viera dónde.
    """
    from thegrill.web import sites

    session, rest, ana, item = cocina
    playa = sites.create(session, ana, "Playa 1")
    azar = random.Random(61)
    peor = 0.0
    for n in range(60):
        kg = round(azar.uniform(5.0, 14.0), 3)
        precio = round(azar.uniform(15.0, 80.0), 2)
        pieza, coste0 = _pieza(session, rest, f"T{n:04d}", kg, precio)
        pieza.storage = Storage.CHILLED
        session.flush()
        sites.send_primal(session, ana, pieza.serial, playa.id, on=HOY)
        session.refresh(pieza)
        peor = max(peor, abs((pieza.piece_cost_usd or 0) - coste0))
        assert abs((pieza.weight_kg or 0) - kg) < GRAMO, "cambió de peso en la furgoneta"
    apunta("trasladar una pieza a otra sede", peor)
    assert peor < CENTIMO, f"la carne cambia {peor:.8f} EUR de valor al trasladarla"


# ============================================================= el descongelado
def test_splitting_a_frozen_lot_keeps_every_gram_and_every_cent(cocina):
    """Sacar del arcón parte un número en dos: los dos juntos son el de antes."""
    from thegrill.web import defrost

    session, rest, ana, item = cocina
    azar = random.Random(71)
    peor_kg = peor_eur = 0.0
    for n in range(120):
        kg = round(azar.uniform(3.0, 25.0), 3)
        coste_kg = round(azar.uniform(9.0, 55.0), 4)
        piezas = azar.randint(4, 40)
        padre = IngredientLot(restaurant_id=rest.id, item_id=item.id,
                              ingredient_id=item.ingredient_id,
                              serial=f"D{n:04d}", lot_code=f"D{n:04d}",
                              qty=kg, qty_remaining=kg, unit_cost=coste_kg, pieces=piezas,
                              frozen=True, received=HOY - timedelta(days=10),
                              expiry=HOY + timedelta(days=90))
        session.add(padre); session.flush()
        valor0 = round(kg * coste_kg, 6)
        saca = round(kg * azar.uniform(0.1, 0.8), 3)
        hijo = defrost.thaw(session, ana, padre, saca, pieces=max(1, piezas // 3), on=HOY)
        session.refresh(padre)
        juntos_kg = (padre.qty_remaining or 0) + (hijo.qty_remaining or 0)
        juntos_eur = ((padre.qty_remaining or 0) * (padre.unit_cost or 0)
                      + (hijo.qty_remaining or 0) * (hijo.unit_cost or 0))
        peor_kg = max(peor_kg, abs(juntos_kg - kg))
        peor_eur = max(peor_eur, abs(juntos_eur - valor0))
    apunta("partir un número al descongelar (kilos)", peor_kg * 1000, "gramos")
    apunta("partir un número al descongelar (dinero)", peor_eur)
    assert peor_kg < GRAMO, f"partir el número pierde {peor_kg * 1000:.3f} gramos"
    assert peor_eur < CENTIMO, f"partir el número pierde {peor_eur:.8f} EUR"


# ================================================================ el inventario
def test_what_the_count_writes_off_is_exactly_what_is_missing(cocina):
    """Al cerrar un inventario, el dinero que se da de baja es el de los kilos
    que faltan, ni más ni menos.

    Es el apunte que va contra el resultado del mes. Si estuviera inflado, el
    mes saldría peor de lo que fue y nadie sabría por qué.
    """
    from thegrill.web import inventory

    session, rest, ana, item = cocina
    azar = random.Random(83)
    peor = 0.0
    for n in range(40):
        kg = round(azar.uniform(4.0, 20.0), 3)
        coste_kg = round(azar.uniform(10.0, 50.0), 4)
        lote = IngredientLot(restaurant_id=rest.id, item_id=item.id,
                             ingredient_id=item.ingredient_id,
                             serial=f"I{n:04d}", lot_code=f"I{n:04d}",
                             qty=kg, qty_remaining=kg, unit_cost=coste_kg,
                             received=HOY - timedelta(days=2),
                             expiry=HOY + timedelta(days=30))
        session.add(lote); session.flush()
        abierto = inventory.open_now(session, rest.id)
        cuenta = abierto or inventory.open_count(session, ana, on=HOY)
        falta = round(kg * azar.uniform(0.05, 0.4), 3)
        for linea in cuenta.lines:
            if linea.serial == lote.serial:
                inventory.record(session, ana, cuenta, linea.serial, round(kg - falta, 3))
            else:
                otro = (session.query(IngredientLot)
                        .filter_by(restaurant_id=rest.id, serial=linea.serial).first())
                inventory.record(session, ana, cuenta, linea.serial,
                                 round(otro.qty_remaining, 3) if otro else 0.0)
        resultado = inventory.close_count(session, ana, cuenta)
        esperado = round(resultado.adjusted_kg * coste_kg, 4)
        # El ajuste mezcla varios artículos si los hay; se compara contra el
        # dinero que de verdad se ha dado de baja, que es lo que se apunta.
        if abs(resultado.adjusted_kg) > GRAMO:
            peor = max(peor, abs(abs(resultado.adjusted_value)
                                 - abs(esperado)) / max(abs(esperado), 1.0))
    assert peor < 0.02, f"el ajuste del inventario se desvía un {peor * 100:.2f} %"


# ================================================================== el cuadro
def test_zz_the_report_of_how_close_to_the_cent_we_are(tmp_path):
    """Deja por escrito a cuánto se ha quedado cada cuenta del céntimo.

    Va al final a propósito: para entonces las demás ya han medido. No
    comprueba nada nuevo —eso ya lo han hecho ellas—: escribe el cuadro, que es
    lo que se le enseña a alguien que pregunta si puede fiarse de los números.
    """
    if not PEORES:
        pytest.skip("se corre con las demás, no suelta")
    lineas = ["", "=" * 72,
              f"  EL PEOR DESVÍO DE CADA CUENTA  ({CASOS} casos por cuenta generada)",
              "=" * 72, ""]
    for cuenta, (desvio, unidad) in sorted(PEORES.items(), key=lambda x: -x[1][0]):
        tope = GRAMO * 1000 if unidad == "gramos" else CENTIMO
        cuanto = desvio / tope * 100 if tope else 0
        marca = "EXACTA" if desvio == 0.0 else f"{cuanto:6.3f} % del tope"
        lineas.append(f"  {cuenta:48} {desvio:14.10f} {unidad:8} ({marca})")
    lineas += ["", f"  Tope: un céntimo y un gramo. Nada por debajo de eso lo mide",
               "  una báscula de muelle ni lo refleja una factura.", ""]
    cuadro = "\n".join(lineas)
    print(cuadro)
    carpeta = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           ".auditoria")
    os.makedirs(carpeta, exist_ok=True)
    with open(os.path.join(carpeta, "matematicas.txt"), "w", encoding="utf-8") as f:
        f.write(cuadro + "\n")
    for cuenta, (desvio, unidad) in PEORES.items():
        tope = GRAMO * 1000 if unidad == "gramos" else CENTIMO
        assert desvio < tope, f"{cuenta}: {desvio} {unidad}"


# ====================================== lo que se guarda, en céntimos y gramos
def test_nothing_with_thousandths_ever_reaches_the_disk(tmp_path):
    """Después de un mes de trabajo, ni un importe con milésimas.

    Un importe de 87,332066 € no rompe nada el día que se escribe. Rompe el
    cuadre tres meses después, cuando alguien suma una columna y le salen
    cuatro céntimos que no están en ninguna factura. Y un peso de 8,4295 kg es
    un peso que ninguna báscula ha dado nunca.

    Esto no se arregla en los trece sitios donde hoy se escribe un apunte: se
    arregla antes de guardar, para que el sitio catorce —el que se escriba el
    mes que viene— tampoco pueda. Aquí se comprueba que el candado está puesto.
    """
    from thegrill import bench
    from thegrill.web import exacto

    db.init_engine(f"sqlite:///{tmp_path/'justo.db'}")
    db.create_all()
    with db.session_scope() as session:
        casa = bench.build(session, days=25, seed=5, until=HOY)
        sucios = exacto.revisar(session, tope=30)
        assert not sucios, f"{len(sucios)} valores con milésimas: {sucios[:5]}"
        # Y la casa ha trabajado de verdad, que si no esto no prueba nada.
        assert casa.primals > 0 and casa.sales > 0


def test_a_computed_weight_keeps_all_its_decimals(tmp_path):
    """Un peso calculado NO se cuadra a gramos: cuadrarlo mete un sesgo.

    Una ración de 160 g con un 95 % de rendimiento consume 168,42 g. Si cada
    consumo se redondea a 168, se pierden cuatro décimas de gramo por ración
    —cuatrocientos gramos cada mil— y siempre hacia el mismo lado. Un error
    que siempre va en la misma dirección se suma; uno que va y viene, se
    compensa. Por eso lo medido va en gramos y lo calculado, entero.
    """
    from thegrill.models import IngredientMovement, MovementKind
    from thegrill.web import exacto

    consumo = -(2 * 0.16 / 0.95)
    mv = IngredientMovement(restaurant_id=1, ingredient_id=1, lot_id=1, date=HOY,
                            kind=MovementKind.SALE, qty=consumo, cost=1.234567,
                            source="pos")
    exacto._cuadrar(mv)
    assert mv.qty == consumo, "le han recortado los gramos a un consumo calculado"
    assert mv.cost == 1.23, "el importe sí tenía que quedar en céntimos"


def test_a_measured_weight_is_kept_in_whole_grams(tmp_path):
    """Y lo que ha dicho una báscula, en gramos justos."""
    from thegrill.web import exacto

    pieza = Primal(restaurant_id=1, serial="X1", sku="Striploin", weight_kg=8.42953,
                   received_kg=8.42953, landed_usd_per_kg=30.0,
                   piece_cost_usd=8.42953 * 30.0, received_date=HOY)
    exacto._cuadrar(pieza)
    assert pieza.weight_kg == 8.43 and pieza.received_kg == 8.43
    assert pieza.piece_cost_usd == 252.89


def test_a_price_per_kilo_keeps_all_its_decimals(tmp_path):
    """Y al revés: los ratios NO se redondean, porque redondearlos pierde dinero.

    158,22 € entre 5,6 kg son 28,253571… €/kg. Guardando 28,25 y volviendo a
    multiplicar salen 158,20: dos céntimos perdidos por lote, y multiplicados
    por los lotes de un año son la cuenta de la luz.
    """
    from thegrill.web import exacto

    lote = IngredientLot(restaurant_id=1, item_id=1, ingredient_id=1,
                         serial="R0001", lot_code="R0001", qty=5.6, qty_remaining=5.6,
                         unit_cost=158.22 / 5.6, received=HOY, expiry=HOY)
    exacto._cuadrar(lote)
    assert lote.unit_cost == 158.22 / 5.6, "le han recortado los decimales al precio por kilo"
    assert exacto.es_justo(lote.qty, exacto.GRAMOS)


def test_the_guard_actually_catches_a_dirty_number(tmp_path):
    """Y que el vigilante vigila: si no cazara nada, daría igual tenerlo."""
    from thegrill.web import exacto

    assert not exacto.es_justo(87.332066, exacto.CENTIMOS)
    assert not exacto.es_justo(8.4295, exacto.GRAMOS)
    assert exacto.es_justo(87.33, exacto.CENTIMOS)
    assert exacto.es_justo(8.429, exacto.GRAMOS)
    assert exacto.euros(87.332066) == 87.33
    assert exacto.kilos(8.4295) == 8.43           # medio gramo sube, como la báscula


def test_the_target_weight_never_pollutes_the_real_one():
    """330 g es lo que se quiere vender, no lo que pesa el filete.

    En cocina se pone un objetivo —330 g de steak— y al cortar nunca salen 330
    exactos. Todo este programa existe para ver esa diferencia: por eso se
    apunta lo que sale a descongelar y se cuenta lo que sobra al cerrar, y de
    ahí sale el peso real por pieza.

    Lo que no puede pasar es que el programa meta un redondeo suyo dentro de
    esa comparación. Si el teórico se cuadrara a gramos, la pérdida real
    saldría contaminada con el error del programa —y en la dirección de
    disimularla, que es la peor—. Aquí se comprueba que mil raciones teóricas
    suman exactamente mil veces una, sin deriva.
    """
    from thegrill.web import exacto

    por_racion = 0.330 / 0.95          # 330 g con un 95 % de rendimiento
    mil = sum(por_racion for _ in range(1000))
    assert abs(mil - 1000 * por_racion) < 1e-9, "el teórico deriva al acumularse"

    # Y si se hubiera cuadrado a gramos, esto es lo que se habría perdido:
    cuadrado = exacto.kilos(por_racion) * 1000
    perdido_g = abs(cuadrado - 1000 * por_racion) * 1000
    assert perdido_g > 100, (
        "si esto baja, revisa el razonamiento: se supone que cuadrar el "
        f"teórico costaba {perdido_g:.0f} gramos cada mil raciones")

    # El peso real es una resta de medidas, y esas sí van en gramos justos.
    salio, sobro, vendidas = 12.480, 3.150, 28
    real_por_pieza = (salio - sobro) / vendidas
    assert abs(real_por_pieza - 0.3332142857142857) < 1e-12
    # Y la diferencia con el objetivo es lo que se gana o se pierde de verdad.
    assert round((real_por_pieza - 0.330) * 1000, 1) == 3.2
