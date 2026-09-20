"""Las sedes de una casa, y la carne que va de una a otra.

Un grupo no son tres restaurantes iguales: lo normal es **un obrador** —donde
se reciben las piezas, se maduran y se despiezan— y **unos locales** que
consumen de él. El obrador corta; el local sirve. Y lo que consume el local
puede ser una pieza entera, para cortarla allí, o cortes ya hechos.

Por eso la carne no está solo «en la casa»: está en una sede. «Quedan ocho
piezas» sin decir dónde no sirve para trabajar, porque ocho en el obrador y
ninguna en la playa no es lo mismo que cuatro en cada sitio.

Lo que viaja se lleva su número y su coste. Una pieza que sale del obrador
entra en el local valiendo lo mismo: ni se abarata por el camino ni se encarece.
Cuando lo que viaja es parte de un lote de cortes, el lote se parte y el trozo
que sale nace con su propio número colgando del de origen, para que se siga
pudiendo seguir hasta el plato.
"""
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from sqlalchemy.orm import Session

from thegrill.models import (Ingredient, IngredientLot, Primal, PrimalStatus, Site,
                             SiteKind, SitePar, Transfer, User)

EPSILON = 1e-9
PRIMAL = "PRIMAL"
CUT = "CUT"


class SiteError(ValueError):
    """El traslado no se puede hacer tal y como está."""


@dataclass
class SiteStock:
    """Lo que hay en una sede, para verlo de un vistazo."""
    site: Site
    primals: int = 0
    primal_kg: float = 0.0
    cut_kg: float = 0.0
    value: float = 0.0

    @property
    def kg(self) -> float:
        return round(self.primal_kg + self.cut_kg, 3)


@dataclass
class Move:
    """Un traslado tal y como se lee: con los nombres puestos, no con los números."""
    date: date
    kind: str
    serial: str
    label: str
    kg: float
    cost: float | None
    from_name: str
    to_name: str
    new_serial: str | None = None
    note: str | None = None


@dataclass
class Sent:
    """Un traslado, ya hecho."""
    kind: str
    serial: str
    label: str
    kg: float
    cost: float | None
    to_site: Site
    from_site: Site | None = None
    new_serial: str | None = None


# ------------------------------------------------------------------- sedes
def main(session: Session, restaurant_id: int) -> Site:
    """La sede de siempre. Si la casa no tiene ninguna, se le crea el obrador.

    Una casa que nunca ha oído hablar de sedes tiene una: donde está todo. Así
    lo de antes sigue funcionando sin que nadie tenga que configurar nada.
    """
    found = (session.query(Site)
             .filter_by(restaurant_id=restaurant_id)
             .order_by(Site.id).first())
    if found is not None:
        return found
    site = Site(restaurant_id=restaurant_id, name="Principal",
                kind=SiteKind.WAREHOUSE, created_at=datetime.utcnow())
    session.add(site)
    session.flush()
    return site


def all_sites(session: Session, restaurant_id: int, active: bool = True) -> list[Site]:
    query = session.query(Site).filter_by(restaurant_id=restaurant_id)
    if active:
        query = query.filter(Site.active.is_(True))
    rows = query.order_by(Site.kind, Site.name).all()
    return rows or [main(session, restaurant_id)]


def create(session: Session, user: User, name: str,
           kind: SiteKind = SiteKind.OUTLET, address: str | None = None) -> Site:
    name = (name or "").strip()
    if not name:
        raise SiteError("La sede necesita un nombre")
    # El obrador primero: un local consume de algún sitio, y la carne que ya
    # había en la casa estaba en la sede principal, no en el local nuevo.
    main(session, user.restaurant_id)
    if (session.query(Site)
            .filter_by(restaurant_id=user.restaurant_id, name=name).first()):
        raise SiteError(f"Ya hay una sede llamada {name}")
    site = Site(restaurant_id=user.restaurant_id, name=name, kind=kind,
                address=(address or "").strip() or None, created_at=datetime.utcnow())
    session.add(site)
    session.flush()
    return site


def where(session: Session, restaurant_id: int, obj) -> Site:
    """La sede de una pieza o de un lote. Lo que no la diga, está en la principal."""
    if getattr(obj, "site_id", None):
        site = session.get(Site, obj.site_id)
        if site is not None:
            return site
    return main(session, restaurant_id)


def of_user(session: Session, user: User) -> Site | None:
    """Dónde trabaja esa persona. Sin sede, ve la casa entera."""
    return session.get(Site, user.site_id) if user.site_id else None


def guard(session: Session, user: User, obj) -> Site:
    """Comprueba que eso está donde trabaja quien lo va a tocar.

    Las pantallas ya enseñan solo lo de cada sede, pero una barra sin enlace no
    es una puerta cerrada: escribiendo el número a mano se llega igual. Esto es
    la puerta. Quien no tiene sede —el manager— trabaja con la casa entera.
    """
    donde = where(session, user.restaurant_id, obj)
    mia = of_user(session, user)
    if mia is not None and mia.id != donde.id:
        raise SiteError(f"Eso está en {donde.name}, y tú trabajas en {mia.name}: "
                        f"primero hay que traerlo.")
    return donde


# ----------------------------------------------------------------- mínimos
@dataclass
class Pars:
    """Los mínimos de una sede: los cortes en kilos y los primales en piezas."""
    cuts: dict[int, float] = field(default_factory=dict)
    primals: dict[str, int] = field(default_factory=dict)


def pars_of(session: Session, restaurant_id: int, site_id: int | None) -> Pars:
    """Lo que esa sede ha puesto. Lo que no esté aquí lo manda la casa."""
    out = Pars()
    if not site_id:
        return out
    for row in (session.query(SitePar)
                .filter_by(restaurant_id=restaurant_id, site_id=site_id)):
        if row.ingredient_id and row.min_stock is not None:
            out.cuts[row.ingredient_id] = row.min_stock
        elif row.sku and row.min_pieces is not None:
            out.primals[row.sku] = row.min_pieces
    return out


def set_par(session: Session, user: User, site_id: int, *, ingredient_id: int | None = None,
            sku: str | None = None, minimum: float | None = None) -> SitePar | None:
    """Pone —o quita— el mínimo de un corte o de un primal en esa sede.

    Sin número se borra la fila: esa sede vuelve a regirse por el mínimo de la
    casa, que es lo que quiere decir «no tengo nada especial aquí».
    """
    site = _site(session, user, site_id)
    if not ingredient_id and not (sku or "").strip():
        raise SiteError("Hay que decir de qué corte o de qué pieza es el mínimo")
    query = session.query(SitePar).filter_by(restaurant_id=user.restaurant_id, site_id=site.id)
    row = (query.filter_by(ingredient_id=ingredient_id).first() if ingredient_id
           else query.filter_by(sku=(sku or "").strip()).first())
    if minimum is None or minimum < 0:
        if row is not None:
            session.delete(row)
            session.flush()
        return None
    if row is None:
        row = SitePar(restaurant_id=user.restaurant_id, site_id=site.id,
                      ingredient_id=ingredient_id, sku=(sku or "").strip() or None)
        session.add(row)
    if ingredient_id:
        row.min_stock = round(float(minimum), 6)
    else:
        row.min_pieces = int(minimum)
    session.flush()
    return row


# --------------------------------------------------------------- traslados
def send_primal(session: Session, user: User, serial: str, to_site_id: int,
                on: date | None = None, note: str | None = None) -> Sent:
    """Manda una pieza entera a otra sede. Va entera: no se parte por el camino."""
    on = on or date.today()
    primal = (session.query(Primal)
              .filter_by(restaurant_id=user.restaurant_id, serial=(serial or "").strip())
              .first())
    if primal is None:
        raise SiteError(f"No hay ninguna pieza con el número {serial}")
    if primal.status != PrimalStatus.IN_STOCK:
        raise SiteError(f"La pieza {primal.serial} ya no está en stock")
    destino = _site(session, user, to_site_id)
    origen = guard(session, user, primal)      # no se manda lo que no es tuyo
    if origen.id == destino.id:
        raise SiteError(f"La pieza {primal.serial} ya está en {destino.name}")

    coste = (primal.piece_cost_usd if primal.piece_cost_usd is not None
             else (primal.landed_usd_per_kg or 0) * (primal.weight_kg or 0) or None)
    primal.site_id = destino.id
    session.add(Transfer(restaurant_id=user.restaurant_id, date=on, kind=PRIMAL,
                         serial=primal.serial, label=primal.sku,
                         kg=round(primal.weight_kg or 0.0, 6), cost=coste,
                         from_site_id=origen.id, to_site_id=destino.id,
                         note=(note or "").strip() or None, created_by=user.id))
    session.flush()
    return Sent(kind=PRIMAL, serial=primal.serial, label=primal.sku,
                kg=round(primal.weight_kg or 0.0, 6), cost=coste,
                to_site=destino, from_site=origen)


def send_cut(session: Session, user: User, serial: str, kg: float, to_site_id: int,
             on: date | None = None, note: str | None = None) -> Sent:
    """Manda cortes a otra sede: el lote entero o unos kilos de él.

    Si va entero, viaja el lote con su número. Si van unos kilos, el lote se
    parte y lo que sale nace con su propio número colgando del de origen: lo
    que llega al local se puede seguir hasta el plato igual que lo que queda.
    """
    on = on or date.today()
    if kg <= 0:
        raise SiteError("Los kilos que se mandan tienen que ser más de cero")
    lot = (session.query(IngredientLot)
           .filter_by(restaurant_id=user.restaurant_id, serial=(serial or "").strip())
           .first())
    if lot is None:
        raise SiteError(f"No hay ningún corte con el número {serial}")
    if kg > lot.qty_remaining + EPSILON:
        raise SiteError(
            f"Se quieren mandar {kg:.10g} kg y del lote {lot.serial} solo quedan "
            f"{lot.qty_remaining:.10g}.")
    destino = _site(session, user, to_site_id)
    origen = guard(session, user, lot)         # no se manda lo que no es tuyo
    if origen.id == destino.id:
        raise SiteError(f"El corte {lot.serial} ya está en {destino.name}")

    ingredient = session.get(Ingredient, lot.ingredient_id)
    etiqueta = ingredient.name if ingredient else lot.serial
    entero = kg >= lot.qty_remaining - EPSILON
    nuevo_serial = None
    if entero:
        lot.site_id = destino.id
        movido = lot.qty_remaining
    else:
        movido = round(kg, 6)
        nuevo_serial = _child_serial(session, user.restaurant_id, lot.serial)
        piezas = None
        if lot.pieces and lot.qty_remaining > EPSILON:
            piezas = max(1, int(round(lot.pieces * movido / lot.qty_remaining)))
            lot.pieces = max(0, lot.pieces - piezas)
        session.add(IngredientLot(
            restaurant_id=user.restaurant_id, item_id=lot.item_id,
            ingredient_id=lot.ingredient_id, lot_code=lot.lot_code, serial=nuevo_serial,
            parent_serial=lot.parent_serial or lot.serial, parent_lot=lot.parent_lot,
            expiry=lot.expiry, received=lot.received, qty=movido, qty_remaining=movido,
            unit_cost=lot.unit_cost, pieces=piezas, piece_weight_g=lot.piece_weight_g,
            nominal_piece_g=lot.nominal_piece_g, grade=lot.grade, origin=lot.origin,
            frozen=lot.frozen, site_id=destino.id))
        lot.qty_remaining = round(lot.qty_remaining - movido, 6)

    coste = round(movido * (lot.unit_cost or 0.0), 6)
    session.add(Transfer(restaurant_id=user.restaurant_id, date=on, kind=CUT,
                         serial=lot.serial, label=etiqueta, kg=round(movido, 6),
                         cost=coste, from_site_id=origen.id, to_site_id=destino.id,
                         new_serial=nuevo_serial, note=(note or "").strip() or None,
                         created_by=user.id))
    session.flush()
    return Sent(kind=CUT, serial=lot.serial, label=etiqueta, kg=round(movido, 6),
                cost=coste, to_site=destino, from_site=origen, new_serial=nuevo_serial)


def _child_serial(session: Session, restaurant_id: int, base: str) -> str:
    """Un número nuevo para lo que se parte: «8017-01·T1», «·T2»…"""
    for n in range(1, 100):
        candidate = f"{base}·T{n}"
        if not (session.query(IngredientLot)
                .filter_by(restaurant_id=restaurant_id, serial=candidate).first()):
            return candidate
    raise SiteError(f"Demasiados traslados del lote {base}")


def _site(session: Session, user: User, site_id: int) -> Site:
    site = session.get(Site, site_id or 0)
    if site is None or site.restaurant_id != user.restaurant_id:
        raise SiteError("Esa sede no es de esta casa")
    if not site.active:
        raise SiteError(f"La sede {site.name} está cerrada")
    return site


def assign(session: Session, user: User, person: User, site_id: int | None) -> User:
    """A qué sede pertenece una persona. Sin sede, ve la casa entera.

    Quien trabaja en un local no quiere ver las ocho piezas del obrador cuando
    busca las suyas, y quien recibe la mercancía necesita que lo que da de alta
    entre donde está él y no en otro sitio.
    """
    if person.restaurant_id != user.restaurant_id:
        raise SiteError("Esa persona no es de esta casa")
    person.site_id = _site(session, user, site_id).id if site_id else None
    session.flush()
    return person


def set_active(session: Session, user: User, site_id: int, active: bool) -> Site:
    """Cierra o reabre una sede. Cerrada no recibe carne, pero lo suyo no se borra."""
    site = session.get(Site, site_id or 0)
    if site is None or site.restaurant_id != user.restaurant_id:
        raise SiteError("Esa sede no es de esta casa")
    if not active and site.id == main(session, user.restaurant_id).id:
        raise SiteError(f"{site.name} es la sede principal: no se puede cerrar")
    site.active = bool(active)
    session.flush()
    return site


def people(session: Session, restaurant_id: int) -> list[User]:
    return (session.query(User).filter_by(restaurant_id=restaurant_id, active=True)
            .order_by(User.name).all())


def recent(session: Session, restaurant_id: int, days: int = 30,
           site_id: int | None = None) -> list[Move]:
    """Los últimos traslados, o los de una sede —lo que entra y lo que sale—."""
    query = (session.query(Transfer)
             .filter(Transfer.restaurant_id == restaurant_id,
                     Transfer.date >= date.today() - timedelta(days=days)))
    if site_id:
        query = query.filter((Transfer.to_site_id == site_id)
                             | (Transfer.from_site_id == site_id))
    rows = query.order_by(Transfer.date.desc(), Transfer.id.desc()).limit(200).all()
    nombres = {s.id: s.name for s in session.query(Site).filter_by(restaurant_id=restaurant_id)}
    return [Move(date=row.date, kind=row.kind, serial=row.serial, label=row.label,
                 kg=row.kg, cost=row.cost, from_name=nombres.get(row.from_site_id, "—"),
                 to_name=nombres.get(row.to_site_id, "—"), new_serial=row.new_serial,
                 note=row.note)
            for row in rows]


# ------------------------------------------------------------ lo que hay
def stock(session: Session, restaurant_id: int) -> list[SiteStock]:
    """Cuánta carne hay en cada sede, en piezas, kilos y dinero."""
    sedes = {s.id: SiteStock(site=s) for s in all_sites(session, restaurant_id)}
    principal = main(session, restaurant_id)

    for primal in (session.query(Primal)
                   .filter_by(restaurant_id=restaurant_id, status=PrimalStatus.IN_STOCK)):
        fila = sedes.get(primal.site_id or principal.id)
        if fila is None:
            continue
        fila.primals += 1
        fila.primal_kg = round(fila.primal_kg + (primal.weight_kg or 0.0), 6)
        coste = (primal.piece_cost_usd if primal.piece_cost_usd is not None
                 else (primal.landed_usd_per_kg or 0.0) * (primal.weight_kg or 0.0))
        fila.value = round(fila.value + (coste or 0.0), 2)

    for lot in (session.query(IngredientLot)
                .filter(IngredientLot.restaurant_id == restaurant_id,
                        IngredientLot.qty_remaining > EPSILON)):
        fila = sedes.get(lot.site_id or principal.id)
        if fila is None:
            continue
        fila.cut_kg = round(fila.cut_kg + lot.qty_remaining, 6)
        fila.value = round(fila.value + lot.qty_remaining * (lot.unit_cost or 0.0), 2)

    return sorted(sedes.values(), key=lambda f: (f.site.kind.value, f.site.name))
