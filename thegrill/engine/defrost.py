"""Descongelado y recuento de cierre.

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
    """Lo que se sabe de una pieza en un turno."""
    serial: str
    ingredient: str = ""
    opening_kg: float = 0.0
    opening_pieces: int = 0
    intake_kg: float = 0.0
    intake_pieces: int = 0
    closing_kg: float | None = None      # None = no se ha contado
    closing_pieces: int | None = None


@dataclass
class Consumed:
    serial: str
    ingredient: str
    kg: float
    pieces: int
    available_kg: float
    counted: bool                     # False = falta el recuento de cierre
    impossible: bool = False          # queda más de lo que había: falta un apunte

    @property
    def avg_g_per_piece(self) -> float | None:
        return round(self.kg * 1000 / self.pieces, 1) if self.pieces > 0 else None


def reconcile(states: list[SerialState]) -> list[Consumed]:
    """Qué se ha gastado de cada pieza en el turno."""
    out = []
    for st in states:
        available = round(st.opening_kg + st.intake_kg, 6)
        if st.closing_kg is None:
            # Sin recuento no se inventa el consumo: se dice que falta.
            out.append(Consumed(st.serial, st.ingredient, 0.0, 0, available, counted=False))
            continue
        kg = round(available - st.closing_kg, 6)
        pieces = (st.opening_pieces + st.intake_pieces) - (st.closing_pieces or 0)
        out.append(Consumed(st.serial, st.ingredient, max(kg, 0.0), max(pieces, 0),
                            available, counted=True, impossible=kg < -EPSILON))
    return out


@dataclass
class Variance:
    ingredient: str
    real_kg: float          # lo que dice el conteo
    theoretical_kg: float   # lo que dicen las recetas por lo vendido
    units_sold: int = 0

    @property
    def gap_kg(self) -> float:
        return round(self.real_kg - self.theoretical_kg, 6)

    @property
    def gap_pct(self) -> float | None:
        if self.theoretical_kg <= EPSILON:
            return None
        return round(self.gap_kg / self.theoretical_kg * 100, 2)

    @property
    def real_g_per_unit(self) -> float | None:
        """Peso real por pieza vendida, el que sale del plato."""
        return round(self.real_kg * 1000 / self.units_sold, 1) if self.units_sold else None

    @property
    def theoretical_g_per_unit(self) -> float | None:
        return round(self.theoretical_kg * 1000 / self.units_sold, 1) if self.units_sold else None

    @property
    def overcut(self) -> bool:
        """Se ha cortado de más: se gasta más de lo que la receta dice."""
        pct = self.gap_pct
        return pct is not None and pct > 0


def variances(real: dict[str, float], theoretical: dict[str, float],
              units: dict[str, int] | None = None) -> list[Variance]:
    """Compara lo contado con lo que deberían haber gastado las recetas.

    Se ordena por el desvío en kilos: arriba donde más carne se está yendo.
    """
    units = units or {}
    names = sorted(set(real) | set(theoretical))
    rows = [Variance(name, round(real.get(name, 0.0), 6),
                     round(theoretical.get(name, 0.0), 6), units.get(name, 0))
            for name in names]
    rows.sort(key=lambda v: -abs(v.gap_kg))
    return rows
