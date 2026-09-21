"""Banco de pruebas: una casa de mentira, un auditor y un martillo.

Las pruebas de siempre comprueban lo que alguien pensó comprobar. Esto es lo
otro: montar una casa entera —obrador, dos locales, su gente, su carta— y
hacerla trabajar un mes, con sus recepciones, sus despieces, su maduración, sus
traslados, sus ventas y su merma. Y después mirar si los números se sostienen.

Tres piezas:

- `build()` levanta la casa y simula los días. Es determinista: la misma
  semilla da el mismo mes, así que un fallo se repite.
- `audit()` pasa la lista de lo que nunca puede pasar —kilos negativos, carne
  que se vende estando congelada, un lote que da más de lo que tenía, un coste
  en blanco— y devuelve lo que encuentre, con su número de pieza.
- `hammer()` hace operaciones al azar, incluidas las que tienen que fallar, y
  vuelve a auditar. Lo que no salte aquí, saltará en una cocina.

Se usa desde la línea de órdenes:

    python -m thegrill.cli banco --db sqlite:///banco.db --dias 30
    python -m thegrill.cli banco --db sqlite:///banco.db --auditar
"""
import random
from dataclasses import dataclass, field
from datetime import date, timedelta

from sqlalchemy.orm import Session

from thegrill.meat import billing
from thegrill.meat import service as meat
from thegrill.models import (Billing, ConsumptionMode, Despiece, Ingredient, IngredientLot,
                             IngredientMovement, MovementKind, Primal, PrimalStatus,
                             PrimalWeighing, Role, ShiftClosure, Site, SiteKind, Storage,
                             User)
from thegrill.web import aging, auth, costing, defrost, inventory, sites, waste

EPSILON = 1e-6
TOLERANCE = 0.005          # cinco gramos: el redondeo de una balanza, no un agujero


@dataclass
class Finding:
    """Algo que no se sostiene. Con su número, que es lo que se busca luego."""
    rule: str
    what: str
    detail: str

    def __str__(self) -> str:
        return f"[{self.rule}] {self.what}: {self.detail}"


@dataclass
class Bench:
    """La casa de mentira, ya montada."""
    restaurant_id: int
    warehouse_id: int
    name: str = ""
    multisite: bool = True
    outlets: list[int] = field(default_factory=list)
    manager: int = 0
    butcher: int = 0
    outlet_staff: list[int] = field(default_factory=list)
    days: int = 0
    primals: int = 0
    sales: int = 0
    counts: int = 0                                     # inventarios cerrados
    errors: list[str] = field(default_factory=list)     # lo que el programa rechazó


@dataclass
class Population:
    """Un barrio entero de casas: unas con varias sedes y otras con una."""
    houses: list[Bench] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def multisite(self) -> int:
        return len([h for h in self.houses if h.multisite])

    @property
    def sales(self) -> int:
        return sum(h.sales for h in self.houses)

    @property
    def counts(self) -> int:
        return sum(h.counts for h in self.houses)


# ===================================================================== montar
# Las casas no se llaman todas igual, y así se leen los fallos.
NAMES = ["Marina", "Sierra", "Puerto", "Robles", "Alameda", "Duero", "Cala", "Pinar",
         "Ribera", "Faro", "Molino", "Encina", "Lagar", "Dehesa", "Muelle", "Olmo",
         "Vega", "Torre", "Barrio", "Carmen", "Abadía", "Cortijo", "Soto", "Pradera",
         "Almadraba"]


def population(session: Session, houses: int = 50, days: int = 30, seed: int = 1,
               until: date | None = None) -> Population:
    """Cincuenta casas distintas trabajando un mes, la mitad con varias sedes.

    Un fallo que no sale en una casa sale en la número treinta y siete: cada
    una recibe otras piezas, corta otros días y cuenta a otras horas, porque la
    semilla cambia con ella. Las de una sola sede son la mitad a propósito: lo
    que se ha montado para los grupos no puede estropearle el día a quien tiene
    un solo local.
    """
    out = Population()
    for index in range(houses):
        casa = build(session, days=days, seed=seed * 1000 + index, until=until,
                     multisite=index % 2 == 0, index=index)
        out.houses.append(casa)
        out.errors.extend(casa.errors)
        out.findings.extend(audit(session, casa.restaurant_id))
    return out


PASSWORD = "clave-larga-1"       # en el banco toda la casa comparte contraseña
DEMO_PASSWORD = "demo-2026"      # para probar, no para trabajar


@dataclass
class Account:
    """Una cuenta de la demo, tal y como hay que escribirla para entrar."""
    who: str
    email: str
    password: str
    sees: str


def demo(session: Session, days: int = 30, seed: int = 21,
         until: date | None = None) -> list[Account]:
    """Dos casas con un mes de trabajo dentro y las claves para entrar.

    Una con obrador y dos locales, para ver los traslados y el conteo de cada
    sede; otra de un solo local, que es como trabaja la mayoría. Las
    contraseñas son iguales para todos y se dicen en voz alta: esto es para
    probar, no para trabajar.
    """
    grupo = build(session, days=days, seed=seed, until=until, multisite=True, index=0,
                  password=DEMO_PASSWORD, domain="demo")
    asador = build(session, days=days, seed=seed + 1, until=until, multisite=False,
                   index=1, password=DEMO_PASSWORD, domain="demo")
    gente = []
    for casa, etiqueta in ((grupo, "grupo"), (asador, "asador")):
        for user in (session.query(User).filter_by(restaurant_id=casa.restaurant_id)
                     .order_by(User.id)):
            sede = of_site(session, user)
            gente.append(Account(
                who=f"{user.name} · {user.role.value.lower()} · {casa.name}",
                email=user.email, password=DEMO_PASSWORD,
                sees=(f"{etiqueta}: {sede}" if sede else f"{etiqueta}: la casa entera")))
    # El camión de esta mañana: entra con la etiqueta del proveedor y sin
    # precio, que es como entra de verdad. Así la demo abre con algo que hacer
    # —piezas esperando que dirección las active— y con una ficha que se puede
    # abrir para ver de dónde viene la carne.
    for casa in (grupo, asador):
        _camion_de_hoy(session, casa, until or date.today())

    dueno = session.query(User).filter_by(role=Role.OWNER).first()
    if dueno is not None:
        dueno.password_hash = auth.hash_password(DEMO_PASSWORD)
        gente.insert(0, Account(who="Dueño de la plataforma", email=dueno.email,
                                password=DEMO_PASSWORD,
                                sees="las casas, el recibo y los fallos contados"))
    session.flush()
    return gente


# Etiquetas de proveedor verosímiles: cada pieza de un sitio, con su número de
# canal y su día de sacrificio, que es lo que de verdad llega en una caja.
ETIQUETAS = [
    ("Teys Biloela", "AUS 1234", "AUS", "Angus", "CUBE ROLL GF YG", "Ribeye AUS MB7", "MB7"),
    ("Rangers Valley", "AUS 5512", "AUS", "Black Angus", "STRIPLOIN F1", "Striploin AUS MB9+", "MB9+"),
    ("Discarlux", "ES 10.00123/L", "ESP", "Rubia Gallega", "LOMO ALTO MADURADO", "Lomo alto ESP", "Extra"),
]


def _camion_de_hoy(session: Session, casa: "Bench", hoy: date) -> None:
    """Una recepción de hoy, con etiqueta y sin precio. Como en el muelle."""
    quien = (session.query(User)
             .filter_by(restaurant_id=casa.restaurant_id, role=Role.BUTCHER)
             .order_by(User.id).first())
    if quien is None:
        return
    siguiente = meat.next_serials(session, casa.restaurant_id, len(ETIQUETAS) + 1)
    filas = []
    for i, (planta, registro, pais, raza, etiqueta, sku, calidad) in enumerate(ETIQUETAS):
        filas.append(meat.PrimalRow(
            serial=siguiente[i], kg=round(8.6 + i * 0.7, 2), price_kg=None,
            sku=sku, grade=calidad, origin=pais, use_by=hoy + timedelta(days=45),
            supplier_lot=f"L-88{213 + i}", producer_plant=planta, est_code=registro,
            breed=raza, label_product=etiqueta, halal=(i == 0),
            slaughter_date=hoy - timedelta(days=24 + i),
            pack_date=hoy - timedelta(days=21 + i)))
    try:
        meat.receive_primals(session, quien, f"L-{hoy:%y%m%d}-9", filas, received=hoy)
    except meat.MeatError:
        pass            # la demo no se cae por un número cogido: es de mentira


def demo_accounts(session: Session) -> list[Account]:
    """Las cuentas de una demo ya montada, para volver a decir las claves."""
    gente = []
    dueno = session.query(User).filter_by(role=Role.OWNER).first()
    if dueno is not None:
        gente.append(Account(who="Dueño de la plataforma", email=dueno.email,
                             password=DEMO_PASSWORD,
                             sees="las casas, el recibo y los fallos contados"))
    from thegrill.models import Restaurant
    for casa in (session.query(Restaurant).filter(Restaurant.platform.isnot(True))
                 .order_by(Restaurant.id)):
        etiqueta = "grupo" if casa.name.startswith("Grupo") else "asador"
        for user in (session.query(User).filter_by(restaurant_id=casa.id)
                     .order_by(User.id)):
            sede = of_site(session, user)
            gente.append(Account(
                who=f"{user.name} · {user.role.value.lower()} · {casa.name}",
                email=user.email, password=DEMO_PASSWORD,
                sees=(f"{etiqueta}: {sede}" if sede else f"{etiqueta}: la casa entera")))
    return gente


def of_site(session: Session, user: User) -> str:
    sede = sites.of_user(session, user)
    return sede.name if sede else ""


def build(session: Session, days: int = 30, seed: int = 7, until: date | None = None,
          multisite: bool = True, index: int = 0, password: str = PASSWORD,
          domain: str = "banco") -> Bench:
    """Levanta una casa y la hace trabajar. Misma semilla, mismo mes.

    Cinco personas, como en una casa de verdad: dos managers —el que abre la
    cuenta y el segundo, que cierra los domingos— y tres más, el carnicero y
    dos de barra. Con varias sedes, esos dos están en los locales; con una
    sola, los tres trabajan la misma cámara.
    """
    rnd = random.Random(seed)
    until = until or date.today()
    start = until - timedelta(days=days - 1)
    marca = NAMES[index % len(NAMES)]
    nombre = f"{'Grupo' if multisite else 'Asador'} {marca}" + (f" {index}" if index >= len(NAMES) else "")
    correo = lambda quien: f"{quien}{index}@{domain}.com"      # noqa: E731

    if not billing.owner_exists(session):
        billing.bootstrap_owner(session, "dueno@plataforma.com", "Dueño", "clave-plataforma-1")
    restaurant, manager = billing.create_account(
        session, name=nombre, manager_name="Ana", manager_email=correo("ana"),
        password=password, language="es")
    restaurant.billing = Billing.ACTIVE
    session.flush()

    obrador = sites.main(session, restaurant.id)
    obrador.name = "Obrador" if multisite else "Principal"
    sedes = ([sites.create(session, manager, "Playa"),
              sites.create(session, manager, "Sierra")] if multisite else [])

    # El segundo manager: la casa no la lleva una sola persona.
    segundo = billing.create_user(session, manager, name="Marta", email=correo("marta"),
                                  password=password, role=Role.BUTCHER)
    segundo.role = Role.MANAGER
    carnicero = billing.create_user(session, manager, name="Paco", email=correo("paco"),
                                    password=password, role=Role.BUTCHER)
    gente = []
    for puesto, (nombre_p, papel) in enumerate((("Eva", Role.BUTCHER),
                                                ("Leo", Role.EMPLOYEE))):
        persona = billing.create_user(session, manager, name=nombre_p,
                                      email=correo(nombre_p.lower()),
                                      password=password, role=papel)
        if sedes:
            sites.assign(session, manager, persona, sedes[puesto].id)
        gente.append(persona)
    session.flush()

    bench = Bench(restaurant_id=restaurant.id, warehouse_id=obrador.id, name=nombre,
                  multisite=multisite, outlets=[x.id for x in sedes], manager=manager.id,
                  butcher=carnicero.id, outlet_staff=[p.id for p in gente], days=days)

    cortes = _catalogue(session, manager)
    numero = 8000
    for paso in range(days):
        hoy = start + timedelta(days=paso)
        # El segundo manager cierra los domingos, que es como se reparte.
        quien_manda = segundo if hoy.weekday() == 6 else manager
        numero = _one_day(session, bench, quien_manda, carnicero, gente, cortes, hoy, rnd,
                          numero, last=paso == days - 1)
    session.flush()
    bench.primals = session.query(Primal).filter_by(restaurant_id=restaurant.id).count()
    return bench


def _catalogue(session: Session, manager: User) -> list:
    """Los cortes, sus artículos y los platos que los venden."""
    salida = []
    for nombre, gramos, precio, peso in (("Entrecot", 300, 28.0, False),
                                         ("Solomillo", 220, 32.0, False),
                                         ("Lomo madurado", 300, 0.0, True)):
        corte = meat.create_cut(session, manager, nombre, min_stock=4.0,
                                consumption=ConsumptionMode.RECIPE, sold_by_weight=peso)
        articulo = meat.add_article(session, manager, corte, f"{nombre} AUS")
        meat.add_dish(session, manager, f"{nombre} a la brasa", corte.id, gramos,
                      sale_price=precio if not peso else None, by_weight=peso,
                      price_per_kg=129.0 if peso else None,
                      pos_code=f"{1000 + len(salida)}", pos_name=nombre.upper())
        salida.append((corte, articulo, peso))
    return salida


def _one_day(session: Session, bench: Bench, manager: User, carnicero: User, gente: list,
             cortes: list, hoy: date, rnd: random.Random, numero: int,
             last: bool = False) -> int:
    """Un día de trabajo, con lo que pasa de verdad y en desorden."""
    def intenta(fn, *args, **kwargs):
        """Lo que el programa rechace se apunta, no se traga."""
        try:
            return fn(*args, **kwargs)
        except Exception as e:                       # noqa: BLE001 — es lo que se quiere ver
            bench.errors.append(f"{hoy} {fn.__name__}: {e}")
            return None

    # 1. Llega mercancía al obrador, dos de cada tres días. Una de cada tres
    # descargas la recibe el carnicero y entra **sin precio**, como en el
    # muelle de verdad: se queda esperando a que dirección la active, y el
    # banco la activa un día de estos. Así el mes de mentira pasa también por
    # ese camino y no solo por el del manager, que lo pone todo de una vez.
    if rnd.random() < 0.7:
        del_muelle = rnd.random() < 0.34
        quien = (carnicero_de(session, bench.restaurant_id) or manager) if del_muelle else manager
        llega = Storage.FROZEN if rnd.random() < 0.15 else Storage.CHILLED
        al_arcon = llega == Storage.CHILLED and rnd.random() < 0.12
        filas = []
        for _ in range(rnd.randint(1, 3)):
            numero += 1
            filas.append(meat.PrimalRow(
                serial=str(numero), kg=round(rnd.uniform(6, 12), 2),
                price_kg=None if del_muelle else round(rnd.uniform(18, 34), 2),
                sku=rnd.choice(["STRIPLOIN_AUS", "RIBEYE_AUS"]),
                use_by=hoy + timedelta(days=rnd.randint(20, 40)),
                supplier_lot=f"L-{rnd.randint(80000, 89999)}",
                producer_plant=rnd.choice(["Teys Biloela", "Rangers Valley", "Discarlux"]),
                est_code=rnd.choice(["AUS 1234", "ES 10.00123/L"]),
                breed=rnd.choice(["Angus", "Rubia Gallega", None]),
                slaughter_date=hoy - timedelta(days=rnd.randint(12, 35)),
                arrival=llega, frozen_on_arrival=al_arcon,
                arrival_c=round(rnd.uniform(-22, -16) if llega == Storage.FROZEN
                                else rnd.uniform(0.5, 4.5), 1)))
        intenta(meat.receive_primals, session, quien, lot=f"L-{hoy:%m%d}", rows=filas,
                received=hoy, chamber=rnd.choice(["Cámara 1", "Cámara 2", ""]))

    # 1.b Dirección activa lo que lleve esperando precio: es lo que desatasca
    # el muelle, y si no se hace la carne se queda parada y no se despieza.
    for pieza in meat.awaiting_price(session, bench.restaurant_id)[:4]:
        intenta(meat.set_price, session, manager, pieza.serial,
                round(rnd.uniform(18, 34), 2))

    frescas = [p for p in meat.primals_in_stock(session, bench.restaurant_id,
                                                site_id=bench.warehouse_id)
               if aging.where(p) == Storage.CHILLED]

    # 2. Unas a madurar, otras al congelador.
    for pieza in frescas[:2]:
        if rnd.random() < 0.35:
            intenta(aging.move, session, manager, pieza.serial,
                    rnd.choice([Storage.AGING, Storage.FROZEN]), target_days=45,
                    use_by=hoy + timedelta(days=180), on=hoy)

    # 3. Se despieza lo que queda fresco.
    frescas = [p for p in meat.primals_in_stock(session, bench.restaurant_id,
                                                site_id=bench.warehouse_id)
               if aging.where(p) == Storage.CHILLED]
    if frescas and rnd.random() < 0.6:
        pieza = frescas[0]
        kilos = round(pieza.weight_kg or 0.0, 3)
        corte, articulo, _ = cortes[rnd.randrange(2)]
        piezas = max(1, int(kilos * 1000 // 320))
        merma = round(kilos * rnd.uniform(0.05, 0.12), 3)
        intenta(meat.post_butchery, session, manager,
                tg=meat.next_tg(session, bench.restaurant_id), serials=[pieza.serial],
                before_kg=kilos, waste_kg=merma, on=hoy,
                rows=[meat.CutRow(name=corte.name, item_id=articulo.id, pieces=piezas,
                                  grams=round((kilos - merma) * 1000 / piezas, 1))])

    # Dónde se sirve: los locales, o la misma casa cuando no hay más que una.
    barras = (list(zip(bench.outlets, gente)) if bench.outlets
              else [(bench.warehouse_id, gente[0]), (bench.warehouse_id, gente[1])])
    camaras = ([(bench.warehouse_id, manager), *zip(bench.outlets, gente)]
               if bench.outlets else [(bench.warehouse_id, manager)])

    # 4. El conteo diario de la maduración, sede a sede.
    for sede, quien in camaras:
        lecturas = [(l.serial, round(l.yesterday_kg * rnd.uniform(0.993, 0.999), 3))
                    for l in aging.to_count(session, bench.restaurant_id, hoy, sede)
                    if l.kg is None]
        if lecturas and rnd.random() < 0.9:          # algún día se olvidan, como en la vida
            intenta(aging.count_day, session, quien, lecturas, on=hoy, lang="es",
                    site_id=sede)

    # 5. Se limpia alguna pieza madurada.
    tabla = aging.board(session, bench.restaurant_id, storage=Storage.AGING, on=hoy)
    if tabla and rnd.random() < 0.2:
        fila = rnd.choice(tabla)
        quita = round(fila.kg * rnd.uniform(0.03, 0.08), 3)
        corte, articulo, _ = cortes[2]
        intenta(aging.trim, session, manager, fila.serial, removed_kg=quita,
                parts=[aging.TrimPart(item_id=articulo.id, kg=round(quita * 0.4, 3))],
                waste_kg=round(quita * 0.6, 3), on=hoy, lang="es")

    # 6. Traslados a los locales: piezas enteras y cortes.
    for sede in bench.outlets:
        if rnd.random() < 0.3:
            candidatas = meat.primals_in_stock(session, bench.restaurant_id,
                                               site_id=bench.warehouse_id)
            if candidatas:
                intenta(sites.send_primal, session, manager,
                        rnd.choice(candidatas).serial, sede, on=hoy)
        if rnd.random() < 0.5:
            lotes = [l for l in session.query(IngredientLot)
                     .filter(IngredientLot.restaurant_id == bench.restaurant_id,
                             IngredientLot.qty_remaining > 0.5)
                     if (l.site_id or bench.warehouse_id) == bench.warehouse_id]
            if lotes:
                lote = rnd.choice(lotes)
                intenta(sites.send_cut, session, manager, lote.serial,
                        round(lote.qty_remaining * rnd.uniform(0.2, 1.0), 3), sede, on=hoy)

    # 7. En cada barra: sacar del arcón, vender, contar y cerrar el turno.
    for sede, quien in barras:
        congelados = [l for l in session.query(IngredientLot)
                      .filter(IngredientLot.restaurant_id == bench.restaurant_id,
                              IngredientLot.frozen.is_(True),
                              IngredientLot.qty_remaining > 0.5)
                      if (l.site_id or bench.warehouse_id) == sede]
        if congelados:
            lote = congelados[0]
            intenta(defrost.intake, session, quien, lote.serial, 4,
                    round(min(lote.qty_remaining, rnd.uniform(1.0, 3.0)), 3), on=hoy)

        ventas = [(corte.name.upper(), rnd.randint(1, 6),
                   round(rnd.uniform(0.2, 0.5), 3) if peso else None)
                  for corte, _, peso in cortes if rnd.random() < 0.8]
        if ventas:
            bench.sales += len(ventas)
            intenta(costing.consume_sales, session, quien, ventas, on=hoy, lang="es",
                    site_id=sede if bench.outlets else None)

        if rnd.random() < 0.25:
            lotes = [l for l in session.query(IngredientLot)
                     .filter(IngredientLot.restaurant_id == bench.restaurant_id,
                             IngredientLot.qty_remaining > 0.3)
                     if (l.site_id or bench.warehouse_id) == sede]
            if lotes:
                lote = rnd.choice(lotes)
                intenta(waste.record, session, quien, kg=round(rnd.uniform(0.1, 0.3), 3),
                        serial=lote.serial, reason="se cayó", on=hoy, lang="es")

        intenta(defrost.close, session, quien, on=hoy, shift="noche", lang="es")

    # 8. El inventario del mes, cada sede el suyo. Y si el mes se acaba sin
    #    haberlo hecho, se hace el último día: la obligación es mensual.
    pendiente = not inventory.monthly_status(session, bench.restaurant_id, on=hoy).done
    if hoy.day == 28 or (last and pendiente):
        for sede, quien in camaras:
            hoja = intenta(inventory.open_count, session, quien, on=hoy, site_id=sede)
            if hoja is None:
                continue
            for linea in list(hoja.lines):
                intenta(inventory.record, session, quien, hoja, linea.serial,
                        round(linea.expected_kg * rnd.uniform(0.98, 1.0), 3))
            if intenta(inventory.close_count, session, quien, hoja, lang="es"):
                bench.counts += 1
    return numero


# ==================================================================== auditar
def carnicero_de(session: Session, restaurant_id: int) -> User | None:
    """El que descarga. El precio no es suyo y no lo pone."""
    return (session.query(User)
            .filter_by(restaurant_id=restaurant_id, role=Role.BUTCHER, active=True)
            .order_by(User.id).first())


def audit(session: Session, restaurant_id: int) -> list[Finding]:
    """Lo que nunca puede pasar. Si pasa, sale con su número y su cifra."""
    out: list[Finding] = []
    principal = sites.main(session, restaurant_id).id
    lotes = (session.query(IngredientLot)
             .filter_by(restaurant_id=restaurant_id).all())
    piezas = session.query(Primal).filter_by(restaurant_id=restaurant_id).all()

    # --- kilos
    for lote in lotes:
        if lote.qty_remaining < -EPSILON:
            out.append(Finding("kilos_negativos", lote.serial or str(lote.id),
                               f"quedan {lote.qty_remaining:.6g} kg"))
        if lote.qty_remaining > (lote.qty or 0.0) + TOLERANCE:
            out.append(Finding("lote_crecido", lote.serial or str(lote.id),
                               f"quedan {lote.qty_remaining:.6g} de {lote.qty:.6g}"))
        if lote.pieces is not None and lote.pieces < 0:
            out.append(Finding("piezas_negativas", lote.serial or str(lote.id),
                               f"{lote.pieces} piezas"))
    for pieza in piezas:
        if (pieza.weight_kg or 0.0) < -EPSILON:
            out.append(Finding("kilos_negativos", pieza.serial,
                               f"pesa {pieza.weight_kg:.6g} kg"))

    # --- dinero: nunca en blanco, y el kilo de lo que madura no baja
    #
    # Una pieza recién descargada **puede** estar sin precio: el muelle apunta
    # lo que llega y dirección le pone el suyo con la factura delante. Eso no
    # es un fallo, es el camino normal. Lo que sí lo es: que se quede esperando
    # una semana —alguien se olvidó y la carne está parada, porque sin precio
    # no se puede despiezar— o que una pieza haya salido de la cámara sin haber
    # tenido nunca coste, que es el dinero perdiéndose sin que salte nada.
    hoy = date.today()
    for pieza in piezas:
        sin_dinero = not pieza.landed_usd_per_kg and not pieza.piece_cost_usd
        if not sin_dinero:
            continue
        if pieza.status == PrimalStatus.IN_STOCK and (pieza.weight_kg or 0) > EPSILON:
            esperando = (hoy - pieza.received_date).days if pieza.received_date else 0
            if esperando > 7:
                out.append(Finding("precio_olvidado", pieza.serial,
                                   f"{esperando} días esperando precio"))
        elif pieza.status == PrimalStatus.CUT:
            out.append(Finding("cortada_sin_precio", pieza.serial,
                               "despiezada sin haber tenido coste"))

    # --- lo que llegó congelado, y lo que se congeló al entrar, está en el arcón
    for pieza in piezas:
        al_arcon = pieza.arrival == Storage.FROZEN or pieza.frozen_on_arrival
        if al_arcon and pieza.status == PrimalStatus.IN_STOCK and pieza.storage is None:
            out.append(Finding("arcon_perdido", pieza.serial,
                               "llegó al congelador y no consta dónde está"))
    for pesada in (session.query(PrimalWeighing)
                   .filter_by(restaurant_id=restaurant_id)):
        if pesada.kind and pesada.kind.value == "EVAPORATION" and pesada.cost_per_kg_before:
            if (pesada.cost_per_kg or 0) + TOLERANCE < pesada.cost_per_kg_before:
                out.append(Finding("kilo_abaratado", pesada.serial,
                                   f"{pesada.cost_per_kg_before:.4f} → {pesada.cost_per_kg:.4f}"))

    # --- la carne congelada no se vende
    por_id = {l.id: l for l in lotes}
    for mv in (session.query(IngredientMovement)
               .filter_by(restaurant_id=restaurant_id, kind=MovementKind.SALE)):
        lote = por_id.get(mv.lot_id)
        if lote is not None and lote.frozen and mv.source == "pos":
            out.append(Finding("venta_congelada", lote.serial or str(lote.id),
                               f"{-mv.qty:.6g} kg vendidos estando en el arcón"))

    # --- cada lote cuadra con su libro: lo que queda es lo que entró menos lo apuntado
    movidos: dict[int, float] = {}
    for mv in (session.query(IngredientMovement).filter_by(restaurant_id=restaurant_id)):
        if mv.kind != MovementKind.IN and mv.lot_id:
            movidos[mv.lot_id] = round(movidos.get(mv.lot_id, 0.0) + (mv.qty or 0.0), 6)
    for lote in lotes:
        esperado = round((lote.qty or 0.0) + movidos.get(lote.id, 0.0), 6)
        if abs(esperado - (lote.qty_remaining or 0.0)) > TOLERANCE:
            out.append(Finding("libro_descuadrado", lote.serial or str(lote.id),
                               f"quedan {lote.qty_remaining:.6g} y el libro dice "
                               f"{esperado:.6g}"))

    # --- todo lo que está en stock está en una sede que existe
    sedes = {s.id for s in sites.all_sites(session, restaurant_id, active=False)}
    for lote in lotes:
        if lote.qty_remaining > EPSILON and (lote.site_id or principal) not in sedes:
            out.append(Finding("sede_fantasma", lote.serial or str(lote.id),
                               f"sede {lote.site_id}"))
    for pieza in piezas:
        if pieza.status == PrimalStatus.IN_STOCK and (pieza.site_id or principal) not in sedes:
            out.append(Finding("sede_fantasma", pieza.serial, f"sede {pieza.site_id}"))

    # --- dos números iguales en la misma casa: la trazabilidad mentiría
    vistos: dict[str, int] = {}
    for lote in lotes:
        if lote.serial:
            vistos[lote.serial] = vistos.get(lote.serial, 0) + 1
    for serial, veces in vistos.items():
        if veces > 1:
            out.append(Finding("numero_repetido", serial, f"{veces} lotes con el mismo número"))

    # --- la casa de al lado no existe: ni un lote, ni un artículo, ni una sede
    ajenos = {i.id for i in session.query(Ingredient)
              .filter(Ingredient.restaurant_id != restaurant_id)}
    for lote in lotes:
        if lote.ingredient_id in ajenos:
            out.append(Finding("casa_ajena", lote.serial or str(lote.id),
                               f"corte {lote.ingredient_id} de otra casa"))
    for sede in session.query(Site).filter(Site.restaurant_id != restaurant_id):
        if sede.id in {l.site_id for l in lotes if l.site_id}:
            out.append(Finding("casa_ajena", f"sede {sede.id}",
                               "hay carne de esta casa en la sede de otra"))

    # --- lo que sale de un lote sale valorado. Lo que se vende sin haberlo
    #     tenido nunca —el faltante— se apunta aparte y ya tiene su aviso: ahí
    #     no hay precio que poner, y decir uno sería inventarlo.
    for mv in (session.query(IngredientMovement)
               .filter_by(restaurant_id=restaurant_id, kind=MovementKind.SALE)):
        if mv.cost is None and mv.lot_id:
            out.append(Finding("venta_sin_coste", mv.source_ref or str(mv.id),
                               f"{-mv.qty:.6g} kg de un lote, sin valorar"))

    # --- lo que salió de una pieza vale lo que valía la pieza. Los lotes
    #     partidos —un traslado, una salida del arcón— no son carne nueva: son
    #     el mismo kilo con otro número, y contarlos otra vez duplicaría.
    for pieza in piezas:
        salidos = [l for l in lotes if l.parent_serial == pieza.serial
                   and "·T" not in (l.serial or "") and "·D" not in (l.serial or "")]
        if not salidos or pieza.piece_cost_usd is None:
            continue
        # Lo que valían al nacer, no lo que valen hoy: el kilo de un lote sube
        # cuando se tira parte de él, y eso no es dinero nuevo.
        nacidos = {l.id for l in salidos}
        repartido = round(sum(mv.cost or 0.0 for mv in
                              session.query(IngredientMovement)
                              .filter_by(restaurant_id=restaurant_id, kind=MovementKind.IN)
                              if mv.lot_id in nacidos), 2)
        if repartido > round(pieza.piece_cost_usd, 2) + 1.0:
            out.append(Finding("dinero_inventado", pieza.serial,
                               f"la pieza costó {pieza.piece_cost_usd:.2f} y sus cortes "
                               f"suman {repartido:.2f}"))

    # --- una pieza se despieza una vez: si sale en dos, alguien la cortó dos
    # veces y en cámara hay kilos que nunca existieron
    de_quien: dict[str, list[str]] = {}
    for despiece in session.query(Despiece).filter_by(restaurant_id=restaurant_id):
        for link in despiece.primals:
            if link.serial:
                de_quien.setdefault(link.serial, []).append(despiece.tg)
    for serial, cuales in de_quien.items():
        if len(cuales) > 1:
            out.append(Finding("pieza_despiezada_dos_veces", serial,
                               f"sale en {', '.join(sorted(cuales))}"))

    # --- un turno, un cuadre: dos filas del mismo turno doblan el mes
    turnos: dict[tuple, int] = {}
    for fila in session.query(ShiftClosure).filter_by(restaurant_id=restaurant_id):
        clave = (fila.date, fila.shift or "", fila.site_id or 0)
        turnos[clave] = turnos.get(clave, 0) + 1
    for (dia, turno, sede), veces in turnos.items():
        if veces > 1:
            out.append(Finding("turno_cerrado_dos_veces", f"{dia} {turno}".strip(),
                               f"{veces} cuadres en la sede {sede}"))

    # --- lo que madura tiene contra qué medirse
    for pieza in piezas:
        if pieza.status == PrimalStatus.IN_STOCK and aging.where(pieza) == Storage.AGING:
            if not pieza.aging_start_kg or not pieza.storage_since:
                out.append(Finding("maduracion_sin_origen", pieza.serial,
                                   "sin peso de entrada o sin fecha"))
    return out


# =================================================================== la ronda
# Lo que se mira en cada pantalla: que abra, que no se quede en inglés de
# claves y que el dinero no se le escape a quien no puede verlo.
KEY_PATTERN = r"(?<![\w.])(?:m\.[a-z]{2,6}|alert|inv|waste|trace|sale|acct|team|pass|tfa)" \
              r"\.[a-z_0-9]{3,}(?![\w])"
MONEY_KEYS = ("m.home.stock_value", "trace.cost", "rec.food_cost", "m.df.loss")


def crawl(client, lang: str = "es", money: bool = True, extra: dict | None = None
          ) -> list[Finding]:
    """Pasa por todas las pantallas con la sesión que se le dé.

    Una pantalla que revienta, una clave sin traducir o un coste enseñado a
    quien no puede verlo son errores que no salen en las pruebas de motor,
    porque el motor no abre pantallas.
    """
    import re

    from thegrill.meat import app as meatapp
    from thegrill.web.i18n import t

    out: list[Finding] = []
    vistas = _paths(meatapp.app, extra or {})
    for ruta in vistas:
        try:
            response = client.get(ruta)
        except Exception as e:                        # noqa: BLE001
            out.append(Finding("pantalla_rota", ruta, str(e)[:200]))
            continue
        if response.status_code >= 500:
            out.append(Finding("pantalla_rota", ruta, f"HTTP {response.status_code}"))
            continue
        if response.status_code != 200 or "text/html" not in response.headers.get(
                "content-type", ""):
            continue
        cuerpo = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", response.text, flags=re.S)
        visible = re.sub(r"<[^>]+>", " ", cuerpo)
        sueltas = sorted(set(re.findall(KEY_PATTERN, visible)))
        if sueltas:
            out.append(Finding("clave_sin_traducir", ruta, ", ".join(sueltas[:6])))
        if not money:
            filtradas = [k for k in MONEY_KEYS if t(lang, k) in visible]
            if filtradas:
                out.append(Finding("dinero_a_la_vista", ruta,
                                   ", ".join(t(lang, k) for k in filtradas)))
        # El móvil es el sitio donde más se usa esto: cada pantalla tiene que
        # declararse para pantalla pequeña y no puede prohibir el zoom, que es
        # lo que necesita quien no ve de cerca.
        meta = re.search(r'<meta name="viewport" content="([^"]+)"', response.text)
        if meta is None:
            out.append(Finding("sin_movil", ruta, "la pantalla no declara viewport"))
        elif "user-scalable=no" in meta.group(1) or "maximum-scale" in meta.group(1):
            out.append(Finding("zoom_prohibido", ruta, meta.group(1)))
    return out


def _paths(app, extra: dict) -> list[str]:
    """Las direcciones que se pueden abrir de un tirón, con sus huecos rellenos."""
    import re

    rutas = []
    for route in app.routes:
        path = getattr(route, "path", "")
        if "GET" not in getattr(route, "methods", set()) or not path:
            continue
        if path.startswith(("/static", "/healthz", "/api", "/idioma", "/descargas/")):
            continue
        huecos = re.findall(r"{([^}]+)}", path)
        if huecos and not all(h in extra for h in huecos):
            continue
        for hueco in huecos:
            path = path.replace("{%s}" % hueco, str(extra[hueco]))
        rutas.append(path)
    return sorted(set(rutas))


# ==================================================================== martillo
def hammer(session: Session, bench: Bench, rounds: int = 200, seed: int = 11) -> list[str]:
    """Operaciones al azar, incluidas las que tienen que fallar.

    Lo interesante no es que funcione: es que lo que el programa rechace lo
    rechace por su motivo y deje la casa igual que estaba.
    """
    rnd = random.Random(seed)
    rechazos: list[str] = []
    gente = [session.get(User, bench.manager), session.get(User, bench.butcher),
             *[session.get(User, uid) for uid in bench.outlet_staff]]
    hoy = date.today()

    for _ in range(rounds):
        quien = rnd.choice(gente)
        sede = sites.of_user(session, quien)
        lotes = session.query(IngredientLot).filter_by(restaurant_id=bench.restaurant_id).all()
        piezas = (session.query(Primal)
                  .filter_by(restaurant_id=bench.restaurant_id,
                             status=PrimalStatus.IN_STOCK).all())
        try:
            tirada = rnd.random()
            if tirada < 0.2 and piezas:
                pieza = rnd.choice(piezas)
                aging.weigh(session, quien, pieza.serial,
                            round((pieza.weight_kg or 1) * rnd.uniform(0.9, 1.05), 3), on=hoy)
            elif tirada < 0.35 and piezas:
                pieza = rnd.choice(piezas)
                aging.move(session, quien, pieza.serial,
                           rnd.choice(list(Storage)), on=hoy)
            elif tirada < 0.5 and lotes:
                lote = rnd.choice(lotes)
                sites.send_cut(session, quien, lote.serial,
                               round(rnd.uniform(-1, 8), 3),
                               rnd.choice([bench.warehouse_id, *bench.outlets]), on=hoy)
            elif tirada < 0.62 and piezas:
                sites.send_primal(session, quien, rnd.choice(piezas).serial,
                                  rnd.choice([bench.warehouse_id, *bench.outlets]), on=hoy)
            elif tirada < 0.74 and lotes:
                lote = rnd.choice(lotes)
                waste.record(session, quien, kg=round(rnd.uniform(-1, 4), 3),
                             serial=lote.serial, on=hoy, lang="es")
            elif tirada < 0.86 and lotes:
                lote = rnd.choice(lotes)
                defrost.intake(session, quien, lote.serial, rnd.randint(0, 5),
                               round(rnd.uniform(0, 4), 3), on=hoy)
            elif piezas:
                pieza = rnd.choice(piezas)
                aging.sell_by_weight(session, quien, pieza.serial,
                                     round(rnd.uniform(-100, 900), 1), price=60.0, on=hoy)
        except Exception as e:                        # noqa: BLE001
            rechazos.append(f"{quien.name}: {e}")
            session.flush()
        _ = sede
    return rechazos
