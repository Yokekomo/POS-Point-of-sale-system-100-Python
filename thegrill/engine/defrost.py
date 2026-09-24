"""[00091] Descongelado y recuento de cierre.

La carne que se corta al momento no se puede descontar por escandallo: un
entrecot no pesa siempre lo mismo. Se mide por conteo físico.

    consumido = lo que había + lo que se sacó a descongelar − lo que queda

Todo por número de serie, así que se sabe exactamente qué pieza se gastó. Y
cruzándolo con las unidades vendidas en el POS sale el **peso real por pieza**,
que es el número que dice si se está cortando de más.

El motor es puro: recibe los apuntes y devuelve las cuentas.
"""
from dataclasses import dataclass, field

EPSILON = 1e-6


@dataclass
class SerialState:
    """[00092] Lo que se sabe de una pieza en un turno."""
    serial: str
    ingredient: str = ""
    opening_kg: float = 0.0
    opening_pieces: int = 0
    intake_kg: float = 0.0
    intake_pieces: int = 0
    closing_kg: float | None = None      # None = no se ha contado
    closing_pieces: int | None = None
    sold_pieces: int = 0                 # lo que el POS dice que se ha vendido

    @property
    def out_pieces(self) -> int:
        """[00095] Piezas que han estado descongeladas en el turno."""
        return self.opening_pieces + self.intake_pieces

    @property
    def expected_pieces(self) -> int:
        """[00096] Las que deberían quedar: lo que salió menos lo que se ha vendido.

        Es el número contra el que se cuenta al cerrar. Si al contar sale otro,
        la diferencia no la explica el POS y hay que mirarla.
        """
        return max(self.out_pieces - self.sold_pieces, 0)


@dataclass
class Consumed:
    serial: str
    ingredient: str
    kg: float
    pieces: int
    available_kg: float
    counted: bool                     # False = falta el recuento de cierre
    impossible: bool = False          # queda más de lo que había: falta un apunte
    sold_pieces: int = 0              # lo vendido en el POS de esta pieza
    expected_pieces: int = 0          # las que deberían haber quedado

    @property
    def avg_g_per_piece(self) -> float | None:
        """[00097] Peso real por pieza: los kilos que faltan entre las piezas vendidas.

        Se divide por lo vendido en el POS, no por lo que falta de la cámara:
        lo que interesa es cuánto pesa lo que sale a la mesa.
        """
        divisor = self.sold_pieces or self.pieces
        return round(self.kg * 1000 / divisor, 1) if divisor > 0 else None

    @property
    def piece_gap(self) -> int:
        """[00098] Piezas que faltan y el POS no explica. En positivo, faltan."""
        return self.pieces - self.sold_pieces if self.sold_pieces else 0


def reconcile(states: list[SerialState]) -> list[Consumed]:
    """[00093] Qué se ha gastado de cada pieza en el turno."""
    out = []
    for st in states:
        available = round(st.opening_kg + st.intake_kg, 6)
        if st.closing_kg is None:
            # [00105] Sin recuento no se inventa el consumo: se dice que falta.
            out.append(Consumed(st.serial, st.ingredient, 0.0, 0, available, counted=False))
            continue
        kg = round(available - st.closing_kg, 6)
        pieces = st.out_pieces - (st.closing_pieces or 0)
        out.append(Consumed(st.serial, st.ingredient, max(kg, 0.0), max(pieces, 0),
                            available, counted=True, impossible=kg < -EPSILON,
                            sold_pieces=st.sold_pieces,
                            expected_pieces=st.expected_pieces))
    return out


@dataclass
class Variance:
    ingredient: str
    real_kg: float          # lo que dice el conteo
    theoretical_kg: float   # lo que dicen las recetas por lo vendido
    units_sold: int = 0
    unit_cost: float = 0.0  # precio por kilo, para poner en dinero el desvío

    @property
    def gap_kg(self) -> float:
        """[00099] Lo que falta o sobra: lo que se consumió de verdad menos lo que tocaba."""
        return round(self.real_kg - self.theoretical_kg, 6)

    @property
    def gap_pct(self) -> float | None:
        """[00100] Esa diferencia en tanto por ciento. Sin teórico no hay con qué comparar."""
        if self.theoretical_kg <= EPSILON:
            return None
        return round(self.gap_kg / self.theoretical_kg * 100, 2)

    @property
    def real_g_per_unit(self) -> float | None:
        """[00101] Peso real por pieza vendida, el que sale del plato."""
        return round(self.real_kg * 1000 / self.units_sold, 1) if self.units_sold else None

    @property
    def theoretical_g_per_unit(self) -> float | None:
        """[00102] A cuántos gramos por ración debería haber salido.

        Es el número que dice si en la plancha se está cortando ancho: la carta
        dice trescientos y están saliendo trescientos cuarenta.
        """
        return round(self.theoretical_kg * 1000 / self.units_sold, 1) if self.units_sold else None

    @property
    def loss_cost(self) -> float:
        """[00103] Lo que cuesta el desvío del día: los kilos de más, a su precio.

        En negativo cuando se ha gastado menos de lo que dice la carta, que
        también hay que mirarlo: o se corta corto, o falta un apunte.
        """
        return round(self.gap_kg * self.unit_cost, 4)

    @property
    def overcut(self) -> bool:
        """[00104] Se ha cortado de más: se gasta más de lo que la receta dice."""
        pct = self.gap_pct
        return pct is not None and pct > 0


def variances(real: dict[str, float], theoretical: dict[str, float],
              units: dict[str, int] | None = None,
              costs: dict[str, float] | None = None) -> list[Variance]:
    """[00094] Compara lo contado con lo que deberían haber gastado las recetas.

    Se ordena por el dinero que se va en el desvío: arriba lo que más cuesta.
    """
    units = units or {}
    costs = costs or {}
    names = sorted(set(real) | set(theoretical))
    rows = [Variance(name, round(real.get(name, 0.0), 6),
                     round(theoretical.get(name, 0.0), 6), units.get(name, 0),
                     round(costs.get(name, 0.0), 6))
            for name in names]
    rows.sort(key=lambda v: (-abs(v.loss_cost), -abs(v.gap_kg)))
    return rows
