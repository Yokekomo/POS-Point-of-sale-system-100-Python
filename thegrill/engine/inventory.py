"""[00116] Inventario de carne: cuadrar lo que dice el sistema con lo que hay.

Se cuenta pieza a pieza, por número de serie. De cada una salen cuatro
resultados posibles:

- **CUADRA**: lo contado coincide con lo que decía el sistema.
- **FALTA**: hay menos de lo que debería. Es merma no registrada, o carne que
  salió sin apuntarse.
- **SOBRA**: hay más. Casi siempre significa que una salida se apuntó de más.
- **NO ENCONTRADA**: el sistema la tiene y no aparece en la cámara.
- **DESCONOCIDA**: está en la cámara y el sistema no la tiene.

Un inventario al que le faltan piezas por contar es parcial, y un parcial no
cuadra: se dice cuáles faltan y esas no se tocan.

El motor es puro: recibe lo esperado y lo contado, y devuelve las cuentas.
"""
from dataclasses import dataclass, field
# [01671] Ver la nota de `engine/cost.py`: `exacto` es aritmética, no una capa de arriba.
from thegrill.web import exacto

TOLERANCE_KG = 0.005      # cinco gramos: por debajo es la báscula, no una diferencia

MATCH = "MATCH"
SHORT = "SHORT"
OVER = "OVER"
NOT_FOUND = "NOT_FOUND"
UNKNOWN = "UNKNOWN"
UNCOUNTED = "UNCOUNTED"


@dataclass
class Expected:
    serial: str
    label: str
    kind: str                 # CUT o PRIMAL
    kg: float
    unit_cost: float | None = None


@dataclass
class Counted:
    serial: str
    kg: float
    pieces: int | None = None


@dataclass
class Line:
    serial: str
    label: str
    kind: str
    expected_kg: float
    counted_kg: float | None
    unit_cost: float | None
    outcome: str

    @property
    def gap_kg(self) -> float:
        """[00118] Lo que falta o sobra en esa línea. Sin contar, cero.

        Una línea que nadie ha contado no es una línea que cuadra: es una que no se
        ha mirado, y meterla como diferencia cero escondería justo lo que falta.
        """
        if self.counted_kg is None:
            return 0.0
        return round(self.counted_kg - self.expected_kg, 6)

    @property
    def gap_value(self) -> float:
        """[00119] Lo que cuesta esa diferencia."""
        return round(self.gap_kg * self.unit_cost, 4) if self.unit_cost else 0.0

    @property
    def adjusts(self) -> bool:
        """[00120] Solo se re-ancla el stock de lo que se ha contado de verdad."""
        return self.outcome in (SHORT, OVER, NOT_FOUND)


@dataclass
class Summary:
    lines: list[Line] = field(default_factory=list)

    def of(self, outcome: str) -> list[Line]:
        """[00121] Las líneas que acabaron de una manera: contadas, sin contar, con falta."""
        return [l for l in self.lines if l.outcome == outcome]

    @property
    def complete(self) -> bool:
        """[00122] Un inventario con piezas sin contar no cuadra."""
        return not self.of(UNCOUNTED)

    @property
    def counted_lines(self) -> int:
        """[00123] Cuántas líneas se llegaron a contar."""
        return len([l for l in self.lines if l.outcome != UNCOUNTED])

    @property
    def gap_kg(self) -> float:
        """[00124] Los kilos que no cuadran en todo el inventario."""
        return round(sum(l.gap_kg for l in self.lines), 4)

    @property
    def shrink_kg(self) -> float:
        """[00125] Lo que falta, en kilos. Positivo es carne que se ha perdido."""
        return round(-sum(l.gap_kg for l in self.lines if l.gap_kg < 0), 4)

    @property
    def gap_value(self) -> float:
        """[00126] Lo que cuesta todo lo que no cuadra, sumando faltas y sobras."""
        return exacto.eur(sum(l.gap_value for l in self.lines))

    @property
    def shrink_value(self) -> float:
        """[00127] Solo lo que falta, en dinero, sin que lo tape lo que sobra.

        Doscientos euros de menos en el lomo y doscientos de más en la aguja no son
        cero: son cuatrocientos euros de recuento que no se sostiene, y con el
        total a secas no se ve ninguno de los dos.
        """
        return exacto.eur(-sum(l.gap_value for l in self.lines if l.gap_value < 0))

    @property
    def accuracy_pct(self) -> float | None:
        """[00128] Qué parte de las piezas contadas cuadraba."""
        counted = self.counted_lines
        if not counted:
            return None
        return round(len(self.of(MATCH)) / counted * 100, 1)

    def worst(self, n: int = 5) -> list[Line]:
        """[00129] Donde más carne se ha ido, por valor."""
        return sorted((l for l in self.lines if l.gap_value), key=lambda l: l.gap_value)[:n]


def reconcile(expected: list[Expected], counted: list[Counted],
              tolerance_kg: float = TOLERANCE_KG) -> Summary:
    """[00117] Cuadra lo contado contra lo que el sistema dice que debería haber."""
    found = {c.serial: c for c in counted}
    summary = Summary()

    for item in expected:
        hit = found.pop(item.serial, None)
        if hit is None:
            summary.lines.append(Line(item.serial, item.label, item.kind, item.kg, None,
                                      item.unit_cost, UNCOUNTED))
            continue
        gap = hit.kg - item.kg
        if hit.kg <= tolerance_kg and item.kg > tolerance_kg:
            outcome = NOT_FOUND            # contada a cero: no está
        elif abs(gap) <= tolerance_kg:
            outcome = MATCH
        else:
            outcome = OVER if gap > 0 else SHORT
        summary.lines.append(Line(item.serial, item.label, item.kind, item.kg, hit.kg,
                                  item.unit_cost, outcome))

    # [00130] Lo que apareció en cámara y el sistema no conocía.
    for leftover in found.values():
        summary.lines.append(Line(leftover.serial, leftover.serial, "CUT", 0.0,
                                  leftover.kg, None, UNKNOWN))
    return summary
