"""[00668] Los tutoriales guiados: qué se enseña, en qué pantalla y a quién.

La primera vez que alguien entra en un apartado le sale un tutorial corto que
ilumina lo que tiene que mirar y le dice qué hacer. Una vez, y no vuelve —
salvo que suba la versión del tutorial o lo pida él con el botón «?».

Todo lo que define un tutorial vive **aquí y en un solo sitio**. Para añadir
uno a una pantalla nueva bastan tres líneas al final de `TOURS`, y los
`data-tour` en su plantilla.

Dos cosas que no son de adorno:

- **Los pasos se filtran en el servidor, no en el navegador.** Un carnicero no
  ve dinero en ninguna pantalla, así que tampoco puede recibir un paso que
  hable de dinero: ni en el HTML ni en el JSON. Esconderlo con CSS o con un
  `if` en el guion sería enseñárselo igual a quien mire el código fuente.
- **Un paso cuyo elemento no está en la página se salta.** Una lista vacía —un
  día sin piezas que pesar— no puede dejar el tutorial a medias.
"""
from dataclasses import dataclass, field

from thegrill.models import Role

# [00675] Quién ve un paso cuando el paso no dice otra cosa: todo el mundo.
TODOS: tuple = (Role.OWNER, Role.MANAGER, Role.BUTCHER, Role.EMPLOYEE)
# [00676] Y quién ve los pasos que hablan de dinero. Es la misma lista que la de la
# capacidad `money` en `perms`, escrita aquí para que se lea de un vistazo.
DINERO: tuple = (Role.OWNER, Role.MANAGER)

# [00677] Más de cuatro pasos no es un tutorial, es un manual: nadie lo lee de pie.
MAX_PASOS = 4


@dataclass(frozen=True)
class Paso:
    """[00669] Un paso: qué se ilumina, qué se dice y quién puede verlo."""
    selector: str                 # el `data-tour` del elemento, sin más
    titulo: str                   # clave de traducción
    texto: str                    # clave de traducción
    roles: tuple = TODOS

    def para(self, role) -> bool:
        """[00674] Si ese paso del tutorial se le enseña a ese nivel."""
        return role in self.roles


@dataclass(frozen=True)
class Tour:
    """[00670] El tutorial de una pantalla."""
    pantalla: str
    version: int
    pasos: list = field(default_factory=list)


def _t(pantalla: str, version: int, *pasos: Paso) -> Tour:
    """[00671] Monta un tutorial de pantalla, con tope de pasos.

    El tope no es capricho: un tutorial de quince pasos no lo termina nadie, y
    el que lo abandona a la mitad se queda sin ver lo importante.
    """
    assert len(pasos) <= MAX_PASOS, f"{pantalla}: {len(pasos)} pasos, y el tope son {MAX_PASOS}"
    return Tour(pantalla=pantalla, version=version, pasos=list(pasos))


# [00678] ------------------------------------------------------------ los tutoriales
# La clave de cada pantalla es su ruta sin la barra, que es lo que ya
# identifica a la pantalla en el resto del programa.
TOURS: dict[str, Tour] = {
    "recepcion": _t(
        "recepcion", 1,
        Paso("lote", "m.tour.rec1.t", "m.tour.rec1.b"),
        Paso("pieza", "m.tour.rec2.t", "m.tour.rec2.b"),
        Paso("llegada", "m.tour.rec3.t", "m.tour.rec3.b"),
    ),
    "precios": _t(
        "precios", 1,
        Paso("lista", "m.tour.pre1.t", "m.tour.pre1.b", DINERO),
        Paso("precio", "m.tour.pre2.t", "m.tour.pre2.b", DINERO),
    ),
    "despiece": _t(
        "despiece", 1,
        Paso("piezas", "m.tour.des1.t", "m.tour.des1.b"),
        Paso("cortes", "m.tour.des2.t", "m.tour.des2.b"),
        Paso("merma", "m.tour.des3.t", "m.tour.des3.b"),
        Paso("cerrar", "m.tour.des4.t", "m.tour.des4.b"),
    ),
    "maduracion": _t(
        "maduracion", 1,
        Paso("conteo", "m.tour.mad1.t", "m.tour.mad1.b"),
        Paso("limpiar", "m.tour.mad2.t", "m.tour.mad2.b"),
    ),
    "carne": _t(
        "carne", 1,
        Paso("cortes", "m.tour.cam1.t", "m.tour.cam1.b"),
        Paso("caducan", "m.tour.cam2.t", "m.tour.cam2.b"),
    ),
    # [00679] Sacar carne y contar lo que sobró son dos pantallas, y cada una tiene el
    # suyo: si compartieran tutorial, el de la primera se daría por visto y en
    # el recuento —que es donde se descuadra el turno— no saldría nunca nada.
    "descongelado": _t(
        "descongelado", 1,
        Paso("sacar", "m.tour.df1.t", "m.tour.df1.b"),
    ),
    "recuento": _t(
        "recuento", 1,
        Paso("contar", "m.tour.df2.t", "m.tour.df2.b"),
    ),
    "merma": _t(
        "merma", 1,
        Paso("apuntar", "m.tour.mer1.t", "m.tour.mer1.b"),
        Paso("coste", "m.tour.mer2.t", "m.tour.mer2.b", DINERO),
    ),
    "traslados": _t(
        "traslados", 1,
        Paso("destino", "m.tour.tra1.t", "m.tour.tra1.b"),
        Paso("piezas", "m.tour.tra2.t", "m.tour.tra2.b"),
    ),
    "inventario": _t(
        "inventario", 1,
        Paso("contar", "m.tour.inv1.t", "m.tour.inv1.b"),
        Paso("ados", "m.tour.inv2.t", "m.tour.inv2.b"),
    ),
    "trazabilidad": _t(
        "trazabilidad", 1,
        Paso("buscar", "m.tour.trz1.t", "m.tour.trz1.b"),
    ),
    "parte": _t(
        "parte", 1,
        Paso("imprimir", "m.tour.rp1.t", "m.tour.rp1.b"),
    ),
    "carta": _t(
        "carta", 1,
        Paso("pos", "m.tour.car1.t", "m.tour.car1.b", DINERO),
        Paso("gramos", "m.tour.car2.t", "m.tour.car2.b", DINERO),
    ),
}

# [00680] Qué tutorial le toca a cada ruta.
RUTAS: dict[str, str] = {
    "/recepcion": "recepcion",
    "/recepcion/precios": "precios",
    "/despiece": "despiece",
    "/maduracion": "maduracion",
    "/carne": "carne",
    "/descongelado": "descongelado",
    "/descongelado/recuento": "recuento",
    "/merma": "merma",
    "/traslados": "traslados",
    "/inventario": "inventario",
    "/trazabilidad": "trazabilidad",
    "/parte": "parte",
    "/carta": "carta",
}


def de_ruta(ruta: str) -> Tour | None:
    """[00672] El tutorial de esa pantalla, si tiene."""
    return TOURS.get(RUTAS.get((ruta or "").rstrip("/") or "/", ""))


def pasos_para(tour: Tour | None, role) -> list[Paso]:
    """[00673] Los pasos que puede ver esa persona. Los demás no salen de aquí."""
    if tour is None:
        return []
    return [p for p in tour.pasos if p.para(role)]
