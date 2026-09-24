"""Maduración, congelador y venta a peso.

Una pieza entera puede estar en tres sitios, y en cada uno le pasa algo
distinto:

- **En cámara**, la pieza es la que llegó. Ni gana ni pierde.
- **Congelada**, el reloj se para. Se guarda el día en que se congeló y la
  fecha de consumo del congelador, que es la que manda desde entonces.
- **Madurando**, pierde agua todos los días. Y aquí está lo que casi nadie
  apunta: los kilos se van, pero el dinero no. Una pieza de 9 kg a 30 €/kg son
  270 €; si a los cuarenta días pesa 7,6 kg, esos 270 € siguen ahí y el kilo
  ha pasado a valer 35,53 €. Quien siga cobrando como si costara 30 está
  regalando la maduración.

Por eso cada pesada se escribe: el peso de antes, el de ahora, lo que se ha
ido y a cómo queda el kilo. El inventario mensual pesa igual que cualquier
otro día, así que también vale como pesada y la merma de maduración queda
contada como lo que es —evaporación— y no como carne que falta.

La carne madurada se corta en el momento de la venta y se cobra por kilo: no
hay gramos fijos que valgan. Eso es `sell_by_weight`, que descuenta de la
pieza los gramos que se hayan cortado y deja escrito su coste y su food cost.
La congelada es la otra manera: se despieza en porciones y se vende por pieza,
que es el camino de siempre —despiece, cámara, carta— y no necesita nada nuevo.
"""
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from sqlalchemy import case, func
from sqlalchemy.orm import Session

from thegrill.models import (Alert, AlertSeverity, AuditLog, IngredientItem, IngredientLot,
                             IngredientMovement, LossKind, MovementKind, Primal,
                             PrimalStatus, PrimalWeighing, Storage, User, WeightSale)
from thegrill.web import exacto, service, sites
from thegrill.web.i18n import t

EPSILON = 1e-9

# Una pieza que madura pierde agua, y eso es normal. Lo que no es normal es
# cuánto: por encima de estos números alguien tiene que mirarla.
SINGLE_LOSS_PCT = 10.0      # de una pesada a la siguiente
TOTAL_LOSS_PCT = 20.0       # desde que entró a madurar
CRITICAL_LOSS_PCT = 30.0    # a partir de aquí no es maduración, es un problema
GAIN_TOLERANCE_KG = 0.05    # la báscula tiene su juego; más que esto es un error
TRIM_VALUE_INDEX = 0.25     # lo que vale un recorte frente al corte del que sale
MAX_TRIM_PARTS = 3          # lo que sale aprovechable de una limpieza, en la práctica


class AgingError(ValueError):
    """Lo que se pide hacer con la pieza no se puede hacer."""


@dataclass
class MoveResult:
    serial: str
    sku: str
    was: Storage
    now: Storage
    kg: float
    target_days: int | None = None
    use_by: date | None = None


@dataclass
class WeighResult:
    serial: str
    sku: str
    storage: Storage
    days: int | None
    previous_kg: float
    kg: float
    loss_kg: float
    loss_pct: float                 # de esta pesada
    total_loss_kg: float            # desde que entró a madurar
    total_loss_pct: float
    cost_per_kg_before: float | None
    cost_per_kg: float | None
    alert: Alert | None = None

    @property
    def cost_rise_pct(self) -> float | None:
        """Cuánto sube el kilo, que es lo que hay que llevar a la carta."""
        if not self.cost_per_kg_before or not self.cost_per_kg:
            return None
        return round((self.cost_per_kg - self.cost_per_kg_before)
                     / self.cost_per_kg_before * 100, 2)


@dataclass
class TrimPart:
    """Un trozo de la limpieza que se aprovecha: adónde va y cuánto vale."""
    item_id: int
    kg: float
    value_index: float = 0.25
    serial: str | None = None      # lo rellena la limpieza al crear el lote
    cost: float = 0.0


@dataclass
class TrimResult:
    """Una limpieza: lo que se le ha quitado a la pieza y adónde ha ido."""
    serial: str
    sku: str
    storage: Storage
    days: int
    removed_kg: float
    kg: float                       # lo que queda de la pieza
    cost_per_kg_before: float | None
    cost_per_kg: float | None
    kept_kg: float = 0.0            # los recortes que se guardaron
    kept_cost: float = 0.0          # y lo que se llevaron de coste
    waste_kg: float = 0.0           # lo que se tira: costra, telilla, grasa sucia
    parts: list[TrimPart] = field(default_factory=list)

    @property
    def trim_serial(self) -> str | None:
        seriales = [p.serial for p in self.parts if p.serial]
        return ", ".join(seriales) if seriales else None

    @property
    def waste_pct(self) -> float:
        return 0.0 if self.removed_kg <= EPSILON else round(
            self.waste_kg / self.removed_kg * 100, 1)

    @property
    def removed_pct(self) -> float:
        whole = self.kg + self.removed_kg
        return 0.0 if whole <= EPSILON else round(self.removed_kg / whole * 100, 2)

    @property
    def cost_rise_pct(self) -> float | None:
        if not self.cost_per_kg_before or not self.cost_per_kg:
            return None
        return round((self.cost_per_kg - self.cost_per_kg_before)
                     / self.cost_per_kg_before * 100, 2)


@dataclass
class BoardRow:
    """Una pieza en la nevera de maduración o en el congelador."""
    serial: str
    sku: str
    storage: Storage
    since: date | None
    days: int
    target_days: int | None
    start_kg: float | None
    kg: float
    loss_kg: float                  # el agua: lo que se ha evaporado
    loss_pct: float
    trim_kg: float                  # el cuchillo: lo que se le ha quitado limpiando
    trim_kept_kg: float             # de eso, lo que se aprovechó
    trim_waste_kg: float            # y lo que se tiró
    cost_per_kg: float | None
    value: float | None
    use_by: date | None
    received_kg: float | None = None   # lo que pesaba al entrar en la casa
    sold_kg: float = 0.0            # lo que ya se ha cortado y vendido al peso
    grade: str | None = None
    origin: str | None = None
    last_weighed: date | None = None
    site: str = ""                  # en qué sede está: se madura en el local también

    @property
    def yield_pct(self) -> float | None:
        """De lo que entró, cuánto queda. Es la pregunta de la maduración."""
        if not self.received_kg:
            return None
        return round((self.kg + self.sold_kg) / self.received_kg * 100, 1)

    @property
    def ready_on(self) -> date | None:
        if self.storage != Storage.AGING or not self.since or not self.target_days:
            return None
        return self.since + timedelta(days=self.target_days)

    @property
    def ready(self) -> bool:
        return bool(self.target_days) and self.days >= (self.target_days or 0)

    @property
    def days_left(self) -> int | None:
        if not self.target_days:
            return None
        return max(0, self.target_days - self.days)


@dataclass
class SaleResult:
    serial: str
    sku: str
    grams: float
    price: float
    cost: float
    cost_per_kg: float | None
    kg_left: float
    finished: bool = False          # la pieza se ha acabado con esta venta

    @property
    def margin(self) -> float:
        return round(self.price - self.cost, 4)

    @property
    def food_cost_pct(self) -> float | None:
        if self.price <= EPSILON:
            return None
        return round(self.cost / self.price * 100, 2)


# ------------------------------------------------------------------ piezas
@dataclass
class History:
    """Todo lo que se ha pesado en la casa, leído una sola vez.

    Antes cada pantalla lo leía tres o cuatro veces: la del tablero, la del
    resumen y la del conteo del día pedían lo mismo por separado, y con cinco
    mil pesadas dentro eso son décimas de segundo regaladas en cada visita.
    Se lee una vez y se pasa a quien lo necesite.
    """
    last: dict[str, date] = field(default_factory=dict)
    trimmed: dict[str, list] = field(default_factory=dict)
    sold: dict[str, list] = field(default_factory=dict)
    aging_rows: list = field(default_factory=list)     # las pesadas de maduración
    # Solo trae lo de las piezas que están ahora en la cámara. Vale para la
    # pizarra y para el conteo del día; no vale para los tramos de
    # rendimiento, que necesitan también las piezas que ya se gastaron.
    partial: bool = False


def history_of(session: Session, restaurant_id: int,
               in_stock_only: bool = False) -> History:
    """Lo pesado y lo vendido al peso, leído de una vez para toda la pantalla.

    Se piden las columnas, no las filas enteras: una casa con seis meses dentro
    tiene cinco mil pesadas, y montar cada una como objeto para leerle cinco
    datos cuesta más que la propia consulta.

    Y hay dos maneras de preguntar, porque hay dos preguntas distintas:

    - **La pizarra y el conteo del día** solo necesitan, de cada pieza que está
      ahora en la cámara, cuándo se pesó por última vez, lo que se le ha
      quitado limpiando y lo que se le ha cortado para vender. Eso son ciento
      cincuenta datos, no cinco mil: la última fecha la saca la propia base de
      datos y de las pesadas solo se traen las limpiezas. Así la portada tarda
      lo mismo el primer mes que el tercer año.
    - **Los tramos de rendimiento** sí necesitan la historia entera, incluidas
      las piezas que ya se gastaron: de eso va la pregunta —si los quince días
      de más salen a cuenta—, y ahí no hay atajo que valga.
    """
    out = History()
    if in_stock_only:
        out.partial = True
        # Por el número de pieza, no por una lista de seriales: una casa grande
        # tiene más piezas de las que caben en una consulta con lista.
        en_camara = (session.query(Primal.id)
                     .filter(Primal.restaurant_id == restaurant_id,
                             Primal.status == PrimalStatus.IN_STOCK))
        for serial, cuando in (session.query(PrimalWeighing.serial,
                                             func.max(PrimalWeighing.date))
                               .filter(PrimalWeighing.restaurant_id == restaurant_id,
                                       PrimalWeighing.primal_id.in_(en_camara))
                               .group_by(PrimalWeighing.serial)):
            out.last[serial] = cuando
        for row in (session.query(PrimalWeighing.serial, PrimalWeighing.date,
                                  PrimalWeighing.loss_kg, PrimalWeighing.kept_kg,
                                  PrimalWeighing.waste_kg)
                    .filter(PrimalWeighing.restaurant_id == restaurant_id,
                            PrimalWeighing.kind == LossKind.TRIM,
                            PrimalWeighing.primal_id.in_(en_camara))
                    .order_by(PrimalWeighing.date.asc(), PrimalWeighing.id.asc())):
            _add_trim(out, row)
        ventas = (session.query(WeightSale.serial, WeightSale.date, WeightSale.grams)
                  .filter(WeightSale.restaurant_id == restaurant_id,
                          WeightSale.primal_id.in_(en_camara)))
    else:
        for row in (session.query(PrimalWeighing.serial, PrimalWeighing.date,
                                  PrimalWeighing.kind, PrimalWeighing.loss_kg,
                                  PrimalWeighing.kept_kg, PrimalWeighing.waste_kg,
                                  PrimalWeighing.storage, PrimalWeighing.days,
                                  PrimalWeighing.previous_kg, PrimalWeighing.kg)
                    .filter(PrimalWeighing.restaurant_id == restaurant_id)
                    .order_by(PrimalWeighing.date.asc(), PrimalWeighing.id.asc())):
            out.last[row.serial] = row.date
            if row.kind == LossKind.TRIM:
                _add_trim(out, row)
            if row.storage == Storage.AGING:
                out.aging_rows.append(row)
        ventas = (session.query(WeightSale.serial, WeightSale.date, WeightSale.grams)
                  .filter(WeightSale.restaurant_id == restaurant_id))
    for row in ventas:
        out.sold.setdefault(row.serial, []).append((row.date, round(row.grams / 1000, 6)))
    return out


def _add_trim(out: History, row) -> None:
    """Una limpieza: lo que se quitó, lo que se guardó y lo que se tiró."""
    kept = row.kept_kg or 0.0
    out.trimmed.setdefault(row.serial, []).append(
        (row.date, row.loss_kg, kept,
         row.waste_kg if row.waste_kg is not None else round(row.loss_kg - kept, 6)))


def find(session: Session, restaurant_id: int, serial: str) -> Primal:
    """La pieza entera con ese número, si está en la casa y en stock."""
    primal = (session.query(Primal)
              .filter_by(restaurant_id=restaurant_id, serial=(serial or "").strip()).first())
    if primal is None:
        raise AgingError(f"No hay ninguna pieza con el número {serial}")
    if primal.status != PrimalStatus.IN_STOCK:
        raise AgingError(f"La pieza {primal.serial} ya no está en stock")
    return primal


def here(session: Session, user: User, primal: Primal) -> Primal:
    """La pieza tiene que estar donde trabaja quien la va a tocar.

    La pantalla ya solo enseña la cámara de cada uno, pero el número se puede
    escribir a mano: esto es lo que cierra la puerta de verdad.
    """
    try:
        sites.guard(session, user, primal)
    except sites.SiteError as e:
        raise AgingError(str(e)) from None
    return primal


def where(primal: Primal) -> Storage:
    """Dónde está la pieza. Las de antes de esto estaban en cámara."""
    return primal.storage or Storage.CHILLED


def cost_per_kg(primal: Primal) -> float | None:
    """A cómo sale el kilo de esta pieza ahora mismo."""
    if primal.landed_usd_per_kg is not None:
        return primal.landed_usd_per_kg
    if primal.piece_cost_usd is not None and primal.weight_kg:
        return round(primal.piece_cost_usd / primal.weight_kg, 6)
    return None


def total_cost(primal: Primal) -> float | None:
    """Lo que costó la pieza entera. No cambia porque pierda agua."""
    if primal.piece_cost_usd is not None:
        return primal.piece_cost_usd
    if primal.landed_usd_per_kg is not None and primal.weight_kg:
        return round(primal.landed_usd_per_kg * primal.weight_kg, 6)
    return None


def days_in(primal: Primal, on: date | None = None) -> int:
    since = primal.storage_since
    if not since:
        return 0
    return max(0, ((on or date.today()) - since).days)


# -------------------------------------------------------------- el traslado
def move(session: Session, user: User, serial: str, storage: Storage,
         target_days: int | None = None, use_by: date | None = None,
         on: date | None = None, note: str | None = None) -> MoveResult:
    """Lleva una pieza entera a la cámara, al congelador o a madurar.

    Entrar a madurar deja escrito el peso de salida: sin él no hay forma de
    decir después cuánto ha perdido. Congelar pide la fecha de consumo del
    congelador, porque la de la etiqueta original deja de valer.
    """
    on = on or date.today()
    primal = here(session, user, find(session, user.restaurant_id, serial))
    was = where(primal)
    if was == storage:
        raise AgingError(f"La pieza {primal.serial} ya está ahí")
    if storage == Storage.AGING and not primal.weight_kg:
        raise AgingError(f"La pieza {primal.serial} no tiene peso: no se puede madurar lo que no se pesa")
    if target_days is not None and target_days <= 0:
        raise AgingError("Los días de maduración tienen que ser más de cero")

    primal.storage = storage
    primal.storage_since = on
    if storage == Storage.AGING:
        primal.aging_start_kg = primal.weight_kg
        primal.aging_target_days = target_days
    else:
        primal.aging_start_kg = None
        primal.aging_target_days = None
    if storage == Storage.FROZEN and use_by:
        primal.frozen_use_by = use_by
    if storage != Storage.FROZEN:
        # Sale del congelador: la fecha del congelador deja de mandar.
        primal.frozen_use_by = None if was == Storage.FROZEN else primal.frozen_use_by

    _audit(session, user, primal.serial, f"{was.value} → {storage.value}", note)
    session.flush()
    return MoveResult(serial=primal.serial, sku=primal.sku, was=was, now=storage,
                      kg=round(primal.weight_kg or 0.0, 6), target_days=target_days,
                      use_by=primal.frozen_use_by)


# --------------------------------------------------------------- la pesada
def weigh(session: Session, user: User, serial: str, kg: float,
          on: date | None = None, source: str = "manual", note: str | None = None,
          lang: str | None = None) -> WeighResult:
    """Vuelve a pesar una pieza entera y reparte el coste sobre lo que queda.

    Los kilos que faltan no se los ha llevado nadie: se han evaporado. Por eso
    el coste de la pieza no baja y el precio del kilo sube.
    """
    on = on or date.today()
    lang = lang or service.restaurant_language(session, user.restaurant_id)
    if kg <= 0:
        raise AgingError("El peso tiene que ser mayor que cero")

    primal = here(session, user, find(session, user.restaurant_id, serial))
    previous = round(primal.weight_kg or 0.0, 6)
    if kg > previous + GAIN_TOLERANCE_KG:
        raise AgingError(
            f"La pieza {primal.serial} pesaba {previous:.10g} kg y no puede pesar "
            f"{kg:.10g}. Una pieza no engorda en la cámara: revisa la báscula o el número.")

    # Dentro del juego de la báscula, una pieza que "engorda" no ha engordado:
    # es la balanza. Se apunta lo leído, pero el peso que se guarda es el de
    # antes; si no, el kilo de lo que madura se abarata solo, pesada a pesada,
    # y la carne madurada acaba costando menos que la fresca.
    if kg > previous:
        note = " · ".join(x for x in (note, t(lang, "m.ag.scale", kg=f"{kg:.10g}")) if x)[:512]
        kg = previous

    cost = total_cost(primal)
    before_per_kg = cost_per_kg(primal)
    # El coste de la pieza se fija aquí: a partir de ahora el kilo se calcula
    # contra el peso de hoy, no contra el del día que llegó.
    if cost is not None:
        primal.piece_cost_usd = cost
        primal.landed_usd_per_kg = round(cost / kg, 6) if kg > EPSILON else None
    primal.weight_kg = round(kg, 6)

    storage = where(primal)
    days = days_in(primal, on)
    start = primal.aging_start_kg or previous
    total_loss = round(max(0.0, start - kg), 6)
    loss = round(max(0.0, previous - kg), 6)

    session.add(PrimalWeighing(
        restaurant_id=user.restaurant_id, primal_id=primal.id, serial=primal.serial,
        date=on, storage=storage, kind=LossKind.EVAPORATION, previous_kg=previous,
        kg=round(kg, 6), loss_kg=loss, cost_per_kg=primal.landed_usd_per_kg,
        cost_per_kg_before=before_per_kg, days=days, source=source, note=note,
        created_by=user.id))
    session.flush()

    result = WeighResult(
        serial=primal.serial, sku=primal.sku, storage=storage, days=days,
        previous_kg=previous, kg=round(kg, 6), loss_kg=loss,
        loss_pct=_pct(loss, previous), total_loss_kg=total_loss,
        total_loss_pct=_pct(total_loss, start),
        cost_per_kg_before=before_per_kg, cost_per_kg=primal.landed_usd_per_kg)
    _announce(session, user, result, lang)
    return result


def _pct(part: float, whole: float) -> float:
    if whole <= EPSILON:
        return 0.0
    return round(part / whole * 100, 2)


def _announce(session: Session, user: User, result: WeighResult, lang: str) -> None:
    """Avisa cuando la pieza pierde más de lo que una maduración explica."""
    if result.storage != Storage.AGING:
        return
    severity = None
    if result.total_loss_pct >= CRITICAL_LOSS_PCT:
        severity = AlertSeverity.CRITICAL
    elif result.total_loss_pct >= TOTAL_LOSS_PCT or result.loss_pct >= SINGLE_LOSS_PCT:
        severity = AlertSeverity.WARNING
    if severity is None:
        return

    now = datetime.utcnow()
    alert = Alert(restaurant_id=user.restaurant_id, code="aging.loss",
                  message=t(lang, "alert.aging_loss", serial=result.serial,
                            sku=result.sku, pct=f"{result.total_loss_pct:.10g}",
                            kg=f"{result.total_loss_kg:.10g}", days=result.days or 0),
                  severity=severity, created_at=now)
    session.add(alert)
    session.flush()
    result.alert = alert
    targets = [uid for uid in service.manager_ids(session, user.restaurant_id) if uid != user.id]
    service.notify(session, user.restaurant_id, targets, title=t(lang, "alert.aging_title"),
                   body=alert.message, severity=severity, alert_id=alert.id, now=now)


# -------------------------------------------------------------- limpieza
def trim(session: Session, user: User, serial: str, removed_kg: float | None = None,
         new_kg: float | None = None, parts: list[TrimPart] | None = None,
         waste_kg: float | None = None, use_by: date | None = None,
         on: date | None = None, note: str | None = None,
         lang: str | None = None) -> TrimResult:
    """Limpia una pieza entera: lo que se le quita deja de estar en ella.

    Se limpia dos veces, y no son la misma. Antes de madurar se le quita lo
    que sobra —grasa suelta, telillas— para que entre limpia. Y cuando lleva
    semanas hay que quitarle la costra seca, que es mucha más: cuanto más
    tiempo, más costra. Las dos se apuntan igual y las dos suben el precio del
    kilo que queda, porque el dinero de la pieza no se va con el recorte.

    De una limpieza salen siempre las dos cosas: lo que se tira —costra seca,
    telilla, grasa sucia— y lo que se aprovecha —recortes para picada, grasa
    para fondo—. Por eso se dicen los kilos que se quitan y, de esos, cuáles
    entran en cámara y a qué artículo. Lo que no se reparte es lo que se tira.

    Lo aprovechado sale con su propio lote colgando de la pieza y se lleva solo
    lo que vale, según su índice: un recorte no vale lo que un lomo. Lo tirado
    no se lleva nada, así que su coste se queda en los kilos que quedan, igual
    que la merma de cámara de toda la vida.
    """
    on = on or date.today()
    primal = here(session, user, find(session, user.restaurant_id, serial))
    previous = round(primal.weight_kg or 0.0, 6)

    if removed_kg is None and new_kg is None:
        raise AgingError("Hay que decir cuánto se ha quitado, o cuánto pesa ya limpia")
    if removed_kg is None:
        removed_kg = round(previous - (new_kg or 0.0), 6)
    if removed_kg <= 0:
        raise AgingError("Lo que se quita limpiando tiene que ser más de cero")
    if removed_kg >= previous:
        raise AgingError(
            f"Se quieren quitar {removed_kg:.10g} kg de la pieza {primal.serial}, que pesa "
            f"{previous:.10g}. Una limpieza no puede dejar la pieza en nada.")

    parts = [p for p in (parts or []) if p.kg and p.kg > 0]
    # Una pieza sin precio no se limpia guardando recortes, por lo mismo que no
    # se despieza: el recorte entraría en cámara a cero euros, se guardaría como
    # `last_cost` del artículo, y de ahí saldría el coste de todos los platos
    # que lo lleven. Carne gratis en el escandallo. El despiece tiene esa
    # puerta desde siempre; a la limpieza se le olvidó, y la limpieza la hace
    # el carnicero, que es justo quien no ve dinero y no puede darse cuenta.
    if parts and total_cost(primal) is None:
        raise AgingError(t(lang, "m.ag.trim_needs_price", serial=primal.serial))
    kept_total = round(sum(p.kg for p in parts), 6)
    if kept_total > removed_kg + 0.001:
        raise AgingError(
            f"Se han quitado {removed_kg:.10g} kg y se quieren guardar {kept_total:.10g}. "
            "De una limpieza no sale más de lo que se ha cortado.")
    thrown = round(removed_kg - kept_total, 6)
    if waste_kg is not None and abs(round(waste_kg, 6) - thrown) > 0.01:
        raise AgingError(
            f"No cuadra: {removed_kg:.10g} kg quitados, {kept_total:.10g} guardados y "
            f"{waste_kg:.10g} tirados. Lo que se quita es lo que se guarda más lo que se tira.")

    before_per_kg = cost_per_kg(primal)
    whole = total_cost(primal)
    kept_cost = 0.0
    lotes = []
    for part in parts:
        lot = _keep_trim(session, user, primal, part.kg, part.item_id,
                         value_index=part.value_index, use_by=use_by, on=on)
        part.serial = lot.serial
        lotes.append(lot)

    # Lo que se llevan los recortes se reparte en céntimos enteros, y lo que
    # queda en la pieza es el resto exacto. Calculando cada parte por su lado
    # —kilos por precio por índice— y restando, la pieza se quedaba con unas
    # milésimas de más o de menos que ya no cuadraban con nada.
    if whole is not None and lotes:
        objetivo = min(round(whole, 2),
                       round(sum(l.qty * l.unit_cost for l in lotes), 2))
        trozos = exacto.repartir_dinero(objetivo, [l.qty * l.unit_cost for l in lotes])
        for part, lot, cost in zip(parts, lotes, trozos):
            part.cost = cost
            lot.unit_cost = round(cost / lot.qty, 6) if lot.qty > EPSILON else 0.0
            for mv in session.query(IngredientMovement).filter_by(lot_id=lot.id):
                mv.cost = cost
        kept_cost = round(sum(trozos), 2)
    else:
        for part, lot in zip(parts, lotes):
            part.cost = round(lot.qty * lot.unit_cost, 6)
            kept_cost = round(kept_cost + part.cost, 6)

    primal.weight_kg = round(previous - removed_kg, 6)
    if whole is not None:
        primal.piece_cost_usd = round(max(0.0, round(whole, 2) - kept_cost), 2)
        primal.landed_usd_per_kg = (round(primal.piece_cost_usd / primal.weight_kg, 6)
                                    if primal.weight_kg > EPSILON else None)
    session.add(PrimalWeighing(
        restaurant_id=user.restaurant_id, primal_id=primal.id, serial=primal.serial,
        date=on, storage=where(primal), kind=LossKind.TRIM, previous_kg=previous,
        kg=primal.weight_kg, loss_kg=round(removed_kg, 6), kept_kg=kept_total,
        waste_kg=thrown, cost_per_kg=primal.landed_usd_per_kg,
        cost_per_kg_before=before_per_kg, days=days_in(primal, on),
        source="trim", trim_serial=", ".join(p.serial for p in parts if p.serial)[:96] or None,
        note=note, created_by=user.id))
    _audit(session, user, primal.serial,
           f"limpieza -{removed_kg:.10g} kg ({kept_total:.10g} a cámara, {thrown:.10g} tirados)",
           note)
    session.flush()

    return TrimResult(
        serial=primal.serial, sku=primal.sku, storage=where(primal),
        days=days_in(primal, on), removed_kg=round(removed_kg, 6), kg=primal.weight_kg,
        cost_per_kg_before=before_per_kg, cost_per_kg=primal.landed_usd_per_kg,
        kept_kg=kept_total, kept_cost=kept_cost, waste_kg=thrown, parts=parts)


def _keep_trim(session: Session, user: User, primal: Primal, kg: float, item_id: int,
               value_index: float, use_by: date | None, on: date) -> IngredientLot:
    """Mete los recortes en cámara con su propio lote y su parte del coste."""
    item = session.get(IngredientItem, item_id)
    if item is None or item.restaurant_id != user.restaurant_id:
        raise AgingError("Ese artículo no es de este restaurante")
    expiry = use_by or primal.frozen_use_by or primal.expiry_label
    if expiry is None:
        raise AgingError(
            "Los recortes necesitan fecha de consumo y la pieza no trae ninguna. "
            "Una caducidad no se inventa.")
    per_kg = cost_per_kg(primal) or 0.0
    unit_cost = round(per_kg * max(0.0, value_index), 6)

    from thegrill.web.butchery import next_serial      # el mismo contador de siempre
    serial = next_serial(session, user.restaurant_id, primal.serial, 90)
    lot = IngredientLot(restaurant_id=user.restaurant_id, item_id=item.id,
                        ingredient_id=item.ingredient_id, lot_code=f"LIMP-{primal.serial}",
                        serial=serial, parent_serial=primal.serial, parent_lot=primal.lot,
                        expiry=expiry, received=on, qty=round(kg, 6),
                        qty_remaining=round(kg, 6), unit_cost=unit_cost,
                        grade=primal.grade, origin=primal.origin)
    session.add(lot)
    session.flush()
    # Un cero no es un precio: si se guarda como referencia del artículo, el
    # escandallo de los platos que lo lleven sale gratis y nadie lo ve.
    if unit_cost > 0:
        item.last_cost = unit_cost
    session.add(IngredientMovement(
        restaurant_id=user.restaurant_id, ingredient_id=item.ingredient_id, lot_id=lot.id,
        date=on, kind=MovementKind.IN, qty=lot.qty, cost=round(lot.qty * unit_cost, 6),
        source="trim", source_ref=serial, created_by=user.id))
    session.flush()
    return lot


# --------------------------------------------------------- venta a peso
def sell_by_weight(session: Session, user: User, serial: str, grams: float,
                   price: float = 0.0, dish: str | None = None,
                   on: date | None = None, note: str | None = None) -> SaleResult:
    """Corta y cobra por kilo: lo que se hace con una pieza madurada.

    Se descuentan los gramos cortados y se lleva con ellos su parte del coste,
    de manera que el kilo de lo que queda sigue valiendo lo mismo.
    """
    on = on or date.today()
    if grams <= 0:
        raise AgingError("Los gramos vendidos tienen que ser más de cero")
    if price < 0:
        raise AgingError("El precio no puede ser negativo")

    primal = here(session, user, find(session, user.restaurant_id, serial))
    if where(primal) == Storage.FROZEN:
        # Lo congelado está en espera: no se corta al peso ni se cobra. Primero
        # sale del arcón, y cuando esté descongelado se vende.
        raise AgingError(
            f"La pieza {primal.serial} está congelada: hay que sacarla a descongelar "
            f"antes de venderla al corte.")
    kg = round(grams / 1000, 6)
    available = round(primal.weight_kg or 0.0, 6)
    if kg > available + EPSILON:
        raise AgingError(
            f"Se quieren cortar {kg:.10g} kg de la pieza {primal.serial} y solo quedan "
            f"{available:.10g}. Una venta no puede dejar la pieza en negativo.")

    per_kg = cost_per_kg(primal)
    cost = round(kg * per_kg, 6) if per_kg is not None else 0.0
    whole = total_cost(primal)
    primal.weight_kg = round(available - kg, 6)
    if whole is not None:
        # El trozo se lleva su parte: el kilo de lo que queda no se mueve.
        primal.piece_cost_usd = round(max(0.0, whole - cost), 6)

    finished = primal.weight_kg <= EPSILON
    if finished:
        primal.status = PrimalStatus.CUT
        primal.status_ref = "PESO"
        primal.status_date = on
        primal.weight_kg = 0.0
        primal.piece_cost_usd = 0.0

    session.add(WeightSale(
        restaurant_id=user.restaurant_id, primal_id=primal.id, serial=primal.serial,
        date=on, grams=round(grams, 4), price=round(price, 4), cost=cost,
        cost_per_kg=per_kg, dish=(dish or None), note=note, created_by=user.id))
    session.flush()
    return SaleResult(serial=primal.serial, sku=primal.sku, grams=round(grams, 4),
                      price=round(price, 4), cost=cost, cost_per_kg=per_kg,
                      kg_left=primal.weight_kg, finished=finished)


# ------------------------------------------------------------ conteo diario
@dataclass
class DailyLine:
    """Una pieza en el conteo del día."""
    serial: str
    sku: str
    days: int
    yesterday_kg: float          # lo que pesaba la última vez que se pesó
    last_weighed: date | None
    kg: float | None = None      # lo de hoy, cuando ya se ha pesado
    loss_kg: float = 0.0
    cost: float | None = None    # lo que valían esos kilos que ya no se venden
    site: str = ""               # la cámara donde está, que cada sede cuenta la suya


@dataclass
class DailyCount:
    """Lo que ha dejado el conteo de hoy en la nevera de maduración."""
    date: date
    lines: list[DailyLine] = field(default_factory=list)
    alerts: list[Alert] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)      # las que no se han pesado

    @property
    def loss_kg(self) -> float:
        return round(sum(l.loss_kg for l in self.lines), 6)

    @property
    def cost(self) -> float:
        return round(sum(l.cost or 0.0 for l in self.lines), 4)

    @property
    def counted(self) -> int:
        return len([l for l in self.lines if l.kg is not None])


def to_count(session: Session, restaurant_id: int, on: date | None = None,
             site_id: int | None = None, rows: list[BoardRow] | None = None,
             history: History | None = None) -> list[DailyLine]:
    """Lo que hay que pesar hoy: todo lo que madura, que es producto fresco.

    Una pieza madurando no está congelada: está en una cámara a dos grados
    perdiendo agua todos los días. Se cuenta como se cuenta lo descongelado,
    porque es lo mismo —carne fresca abierta— y porque así la merma se sabe
    cada día y no cuando alguien se acuerda.

    Y se cuenta en cada sede: las piezas que maduran en el local las pesa el
    local todas las noches, que es quien tiene la cámara delante.
    """
    on = on or date.today()
    history = history or history_of(session, restaurant_id, in_stock_only=True)
    ultimas = history.last
    out = []
    if rows is None:
        rows = board(session, restaurant_id, storage=Storage.AGING, on=on,
                     site_id=site_id, history=history)
    for row in [r for r in rows if r.storage == Storage.AGING]:
        cuando = ultimas.get(row.serial)
        out.append(DailyLine(serial=row.serial, sku=row.sku, days=row.days,
                             yesterday_kg=row.kg, last_weighed=cuando,
                             kg=row.kg if cuando == on else None, site=row.site))
    return sorted(out, key=lambda l: (l.kg is not None, l.site, l.serial))


def pending_today(session: Session, restaurant_id: int, on: date | None = None,
                  site_id: int | None = None,
                  rows: list[BoardRow] | None = None) -> list[str]:
    """Las piezas que madurando se han quedado hoy sin pesar, sede a sede."""
    on = on or date.today()
    return [l.serial for l in to_count(session, restaurant_id, on, site_id, rows=rows)
            if l.kg is None]


def count_day(session: Session, user: User, readings: list[tuple[str, float]],
              on: date | None = None, lang: str | None = None,
              site_id: int | None = None) -> DailyCount:
    """Pesa de una vez todas las piezas que maduran, que es el conteo del día.

    Devuelve lo que se ha ido hoy en kilos y en dinero: esos kilos no se van a
    vender, aunque su coste se quede en los que quedan. Y dice cuáles no se han
    pesado, porque un conteo a medias no cuadra nada.
    """
    on = on or date.today()
    lang = lang or service.restaurant_language(session, user.restaurant_id)
    out = DailyCount(date=on)
    pesadas = {s.strip(): kg for s, kg in readings if s and s.strip() and kg and kg > 0}
    if site_id is None:
        mia = sites.of_user(session, user)
        site_id = mia.id if mia else None

    for line in to_count(session, user.restaurant_id, on, site_id):
        kg = pesadas.get(line.serial)
        if kg is None:
            out.missing.append(line.serial)
            out.lines.append(line)
            continue
        pesada = weigh(session, user, line.serial, kg, on=on, source="daily", lang=lang)
        line.kg = pesada.kg
        line.loss_kg = pesada.loss_kg
        line.cost = (round(pesada.loss_kg * pesada.cost_per_kg_before, 4)
                     if pesada.cost_per_kg_before else None)
        out.lines.append(line)
        if pesada.alert is not None:
            out.alerts.append(pesada.alert)
    return out


# ---------------------------------------------------------------- la pizarra
def board(session: Session, restaurant_id: int, storage: Storage | None = None,
          on: date | None = None, site_id: int | None = None,
          history: History | None = None) -> list[BoardRow]:
    """Lo que hay madurando y lo que hay congelado, pieza a pieza.

    Se madura donde se sirve: una pieza puesta a madurar en el local es del
    local, y es el local el que la pesa. Con `site_id` sale solo su cámara.
    """
    on = on or date.today()
    query = (session.query(Primal)
             .filter_by(restaurant_id=restaurant_id, status=PrimalStatus.IN_STOCK))
    principal = sites.main(session, restaurant_id)
    nombres = {x.id: x.name for x in sites.all_sites(session, restaurant_id, active=False)}
    history = history or history_of(session, restaurant_id, in_stock_only=True)
    last, sold, trimmed = history.last, history.sold, history.trimmed

    rows: list[BoardRow] = []
    for primal in query:
        donde = primal.site_id or principal.id
        if site_id and donde != site_id:
            continue
        place = where(primal)
        if storage is not None and place != storage:
            continue
        if storage is None and place == Storage.CHILLED:
            continue
        kg = round(primal.weight_kg or 0.0, 6)
        start = primal.aging_start_kg or kg
        # Lo vendido al corte no es merma: sale de la pieza porque se ha
        # cobrado. Lo que se ha evaporado es lo otro.
        cut = round(sum(k for when, k in sold.get(primal.serial, [])
                        if not primal.storage_since or when >= primal.storage_since), 6)
        # Ni lo vendido ni lo limpiado son agua: cada cosa en su columna, que
        # si no la merma de maduración sale inflada y no se parece a nada. El
        # porcentaje de agua se mide contra el peso con el que entró a madurar,
        # que es lo honesto: la costra no evaporó, la cortó alguien.
        cortes = [row for row in trimmed.get(primal.serial, [])
                  if not primal.storage_since or row[0] >= primal.storage_since]
        trim = round(sum(row[1] for row in cortes), 6)
        kept = round(sum(row[2] for row in cortes), 6)
        thrown = round(sum(row[3] for row in cortes), 6)
        loss = round(max(0.0, start - kg - cut - trim), 6)
        per_kg = cost_per_kg(primal)
        rows.append(BoardRow(
            serial=primal.serial, sku=primal.sku, storage=place,
            since=primal.storage_since, days=days_in(primal, on),
            target_days=primal.aging_target_days,
            start_kg=round(start, 6) if primal.aging_start_kg else None,
            kg=kg, loss_kg=loss, loss_pct=_pct(loss, start), trim_kg=trim,
            trim_kept_kg=kept, trim_waste_kg=thrown, cost_per_kg=per_kg,
            value=round(kg * per_kg, 2) if per_kg is not None else None,
            use_by=primal.frozen_use_by or primal.expiry_label, sold_kg=cut,
            received_kg=primal.received_kg,
            grade=primal.grade, origin=primal.origin,
            last_weighed=last.get(primal.serial),
            site=nombres.get(donde, "")))
    return sorted(rows, key=lambda r: (r.storage.value, -r.days, r.serial))


def _trimmed_kg(session: Session, restaurant_id: int
                ) -> dict[str, list[tuple[date, float, float, float]]]:
    """Las limpiezas de cada pieza: lo quitado, lo aprovechado y lo tirado."""
    found: dict[str, list[tuple[date, float, float, float]]] = {}
    for row in (session.query(PrimalWeighing)
                .filter_by(restaurant_id=restaurant_id, kind=LossKind.TRIM)):
        kept = row.kept_kg or 0.0
        found.setdefault(row.serial, []).append(
            (row.date, row.loss_kg, kept, row.waste_kg if row.waste_kg is not None
             else round(row.loss_kg - kept, 6)))
    return found


def _sold_kg(session: Session, restaurant_id: int) -> dict[str, list[tuple[date, float]]]:
    """Los kilos vendidos al corte de cada pieza, con su día."""
    found: dict[str, list[tuple[date, float]]] = {}
    for row in (session.query(WeightSale).filter_by(restaurant_id=restaurant_id)):
        found.setdefault(row.serial, []).append((row.date, round(row.grams / 1000, 6)))
    return found


def _last_weighings(session: Session, restaurant_id: int) -> dict[str, date]:
    found: dict[str, date] = {}
    for row in (session.query(PrimalWeighing)
                .filter_by(restaurant_id=restaurant_id)
                .order_by(PrimalWeighing.date.asc(), PrimalWeighing.id.asc())):
        found[row.serial] = row.date
    return found


def history(session: Session, restaurant_id: int, serial: str) -> list[PrimalWeighing]:
    """Todas las pesadas de una pieza, de la primera a la última."""
    return (session.query(PrimalWeighing)
            .filter_by(restaurant_id=restaurant_id, serial=(serial or "").strip())
            .order_by(PrimalWeighing.date.asc(), PrimalWeighing.id.asc()).all())


def sales(session: Session, restaurant_id: int, serial: str | None = None,
          days: int = 60) -> list[WeightSale]:
    """Las ventas a peso recientes, o todas las de una pieza."""
    query = session.query(WeightSale).filter_by(restaurant_id=restaurant_id)
    if serial:
        query = query.filter(WeightSale.serial == serial.strip())
    else:
        query = query.filter(WeightSale.date >= date.today() - timedelta(days=days))
    return query.order_by(WeightSale.date.desc(), WeightSale.id.desc()).limit(200).all()


@dataclass
class Summary:
    """Lo que la maduración y el congelador dejan en números."""
    aging_pieces: int = 0
    aging_kg: float = 0.0
    aging_value: float = 0.0
    frozen_pieces: int = 0
    frozen_kg: float = 0.0
    frozen_value: float = 0.0
    lost_kg: float = 0.0           # agua evaporada, la que ya no se vende
    trimmed_kg: float = 0.0        # lo quitado limpiando, todo junto
    kept_kg: float = 0.0           # de eso, lo que volvió a cámara
    thrown_kg: float = 0.0         # y lo que se fue a la basura
    ready: list[str] = field(default_factory=list)


def summary(session: Session, restaurant_id: int, on: date | None = None,
            site_id: int | None = None, rows: list[BoardRow] | None = None) -> Summary:
    out = Summary()
    if rows is None:
        rows = board(session, restaurant_id, on=on, site_id=site_id)
    for row in rows:
        if row.storage == Storage.AGING:
            out.aging_pieces += 1
            out.aging_kg = round(out.aging_kg + row.kg, 6)
            out.aging_value = round(out.aging_value + (row.value or 0.0), 2)
            out.lost_kg = round(out.lost_kg + row.loss_kg, 6)
            out.trimmed_kg = round(out.trimmed_kg + row.trim_kg, 6)
            out.kept_kg = round(out.kept_kg + row.trim_kept_kg, 6)
            out.thrown_kg = round(out.thrown_kg + row.trim_waste_kg, 6)
            if row.ready:
                out.ready.append(row.serial)
        elif row.storage == Storage.FROZEN:
            out.frozen_pieces += 1
            out.frozen_kg = round(out.frozen_kg + row.kg, 6)
            out.frozen_value = round(out.frozen_value + (row.value or 0.0), 2)
    return out


@dataclass
class YieldBand:
    """El rendimiento medio de las piezas que se maduraron tantos días."""
    days: int                    # el tramo: 30, 45, 60…
    pieces: int
    yield_pct: float             # cuánto queda de lo que entró, de media
    water_pct: float             # cuánto se fue en agua
    trim_pct: float              # y cuánto por el cuchillo
    kg: float = 0.0              # kilos que han pasado por ahí


# Los tramos de rendimiento miran hacia atrás, pero no hasta el principio de
# los tiempos: lo que dejó una pieza hace tres años no dice nada de la decisión
# de hoy —han cambiado el proveedor, la cámara y hasta el carnicero— y en
# cambio obliga a sumar toda la historia de la casa cada vez que alguien abre
# la pantalla. Un año y medio es memoria de sobra y no crece nunca.
MESES_DE_TRAMOS = 18


def _desde_cuando(on: date | None = None) -> date:
    """El principio de la ventana de los tramos."""
    hoy = on or date.today()
    return hoy - timedelta(days=int(MESES_DE_TRAMOS * 30.44))


def _aging_totals(session: Session, restaurant_id: int, desde: date):
    """Por pieza: los días que llegó a madurar y lo que perdió, en dos sumas."""
    cuchillo = case((PrimalWeighing.kind == LossKind.TRIM, PrimalWeighing.loss_kg),
                    else_=0.0)
    return (session.query(PrimalWeighing.serial,
                          func.max(PrimalWeighing.days),
                          func.sum(cuchillo),
                          func.sum(PrimalWeighing.loss_kg))
            .filter(PrimalWeighing.restaurant_id == restaurant_id,
                    PrimalWeighing.storage == Storage.AGING,
                    PrimalWeighing.date >= desde)
            .group_by(PrimalWeighing.serial)).all()


def _aging_starts(session: Session, restaurant_id: int, desde: date):
    """Por pieza: lo que pesaba cuando entró a madurar, que es contra lo que se mide."""
    primeras = (session.query(PrimalWeighing.serial.label("serial"),
                              func.min(PrimalWeighing.id).label("primera"))
                .filter(PrimalWeighing.restaurant_id == restaurant_id,
                        PrimalWeighing.storage == Storage.AGING,
                        PrimalWeighing.date >= desde)
                .group_by(PrimalWeighing.serial).subquery())
    return (session.query(primeras.c.serial, PrimalWeighing.previous_kg)
            .join(PrimalWeighing, PrimalWeighing.id == primeras.c.primera)).all()


def yield_by_days(session: Session, restaurant_id: int, minimum: int = 3,
                  band: int = 15, history: History | None = None,
                  on: date | None = None) -> list[YieldBand]:
    """Qué rendimiento deja cada tramo de días, para decidir cuántos madurar.

    La pregunta que se hace un asador no es cuánto pierde una pieza, sino si
    los quince días de más le salen a cuenta: la costra crece con el tiempo y
    llega un punto en que los días cuestan más de lo que pagan. Esto lo dice
    con las piezas que ya han pasado por la casa, agrupadas por tramos.

    Con menos de `minimum` piezas en un tramo no se dice nada: una media de dos
    piezas no es una media, es una anécdota.
    """
    # Las cuentas las hace la base de datos: aquí solo llega una fila por pieza.
    # Recorrer en Python las cinco mil pesadas de la casa para acabar con
    # cuarenta medias era lo que hacía que esta pantalla fuera a peor cada mes.
    piezas: dict[str, dict] = {}
    desde = _desde_cuando(on)
    if history is not None and history.aging_rows and not history.partial:
        for row in history.aging_rows:       # ya estaba leído: no se pide otra vez
            if row.date < desde:
                continue                     # la misma ventana por los dos caminos
            dato = piezas.setdefault(row.serial, {"days": 0, "water": 0.0, "trim": 0.0,
                                                  "start": row.previous_kg})
            dato["days"] = max(dato["days"], row.days or 0)
            if row.kind == LossKind.TRIM:
                dato["trim"] = round(dato["trim"] + (row.loss_kg or 0.0), 6)
            else:
                dato["water"] = round(dato["water"] + (row.loss_kg or 0.0), 6)
    else:
        for serial, dias, cuchillo, todo in _aging_totals(session, restaurant_id, desde):
            piezas[serial] = {"days": dias or 0, "trim": round(cuchillo or 0.0, 6),
                              "water": round((todo or 0.0) - (cuchillo or 0.0), 6),
                              "start": 0.0}
        for serial, entrada in _aging_starts(session, restaurant_id, desde):
            if serial in piezas:
                piezas[serial]["start"] = entrada or 0.0

    bandas: dict[int, list[dict]] = {}
    for serial, dato in piezas.items():
        if dato["days"] <= 0 or dato["start"] <= EPSILON:
            continue
        tramo = max(band, int(round(dato["days"] / band)) * band)
        bandas.setdefault(tramo, []).append(dato)

    out = []
    for tramo, datos in sorted(bandas.items()):
        if len(datos) < minimum:
            continue
        entrada = sum(d["start"] for d in datos)
        agua = sum(d["water"] for d in datos)
        cuchillo = sum(d["trim"] for d in datos)
        out.append(YieldBand(
            days=tramo, pieces=len(datos),
            yield_pct=round((entrada - agua - cuchillo) / entrada * 100, 1),
            water_pct=round(agua / entrada * 100, 1),
            trim_pct=round(cuchillo / entrada * 100, 1),
            kg=round(entrada, 3)))
    return out


def _audit(session: Session, user: User, serial: str, move: str,
           note: str | None = None) -> None:
    """Un traslado se firma: quién movió la pieza, de dónde a dónde y por qué."""
    detail = move if not note else f"{move} · {note}"
    session.add(AuditLog(restaurant_id=user.restaurant_id, actor=user.name,
                         table="primals", key=serial, action="move",
                         detail=detail[:255]))
