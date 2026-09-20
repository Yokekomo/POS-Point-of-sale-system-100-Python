"""Quién puede hacer qué.

Tres niveles, pensados desde el trabajo y no desde el organigrama:

- **Dueño de la plataforma**: da de alta las casas, cobra el recibo y puede
  bloquear una cuenta entera. Es el único que toca la configuración del
  restaurante.
- **Manager**: su casa entera. Es el único de la casa que ve el dinero —costes, food cost, el valor
  de la cámara, lo que se pierde al día— y el único que toca la carta, las
  ventas y el equipo.
- **Carnicero**: la carne entera. Recibe primales, los despieza, saca a
  descongelar, cuenta, hace el inventario y apunta la merma. Ve lo que queda de
  primales y los cortes de cada pieza, en kilos y en piezas. El dinero, no.
- **Ayudante**: mete los datos del día —descongelado, recuentos y merma— y ve el
  stock. Ni recibe ni despieza ni abre inventarios.

Lo que no está permitido no se enseña: si alguien no puede tocar una pantalla,
tampoco le aparece en la barra. Y la puerta se cierra en la ruta, no solo en la
plantilla, porque una barra sin enlace no es una puerta cerrada.
"""
from thegrill.models import Role

# --------------------------------------------------------------- capacidades
RECEIVE = "receive"        # dar de alta primales
BUTCHER = "butcher"        # despiezar y volcar a cámara
DEFROST = "defrost"        # sacar a descongelar y contar
CLOSE_SHIFT = "close_shift"  # cerrar el turno: descuenta stock
COUNT = "count"            # contar en un inventario
INVENTORY = "inventory"    # abrir, cerrar o cancelar un inventario
WASTE = "waste"            # apuntar merma
STOCK = "stock"            # ver cámara, cortes y trazabilidad de una pieza
CATALOGUE = "catalogue"    # dar de alta cortes y artículos
MENU = "menu"              # carta, ingredientes del plato y ventas
MONEY = "money"            # ver costes, food cost y valor
TEAM = "team"              # equipo, alertas y configuración de la casa
FIX = "fix"                # recuperar piezas y dar de alta lo que apareció
PLATFORM = "platform"      # la plataforma: altas de casas y el recibo del mes
SETTINGS = "settings"      # la configuración del restaurante

BUTCHER_CAPS = frozenset({RECEIVE, BUTCHER, DEFROST, CLOSE_SHIFT, COUNT,
                          INVENTORY, WASTE, STOCK})
EMPLOYEE_CAPS = frozenset({DEFROST, COUNT, WASTE, STOCK})
MANAGER_CAPS = frozenset({RECEIVE, BUTCHER, DEFROST, CLOSE_SHIFT, COUNT, INVENTORY,
                          WASTE, STOCK, CATALOGUE, MENU, MONEY, TEAM, FIX})
OWNER_CAPS = MANAGER_CAPS | {PLATFORM, SETTINGS}

CAPS: dict[Role, frozenset[str]] = {
    Role.OWNER: OWNER_CAPS,
    Role.MANAGER: MANAGER_CAPS,
    Role.BUTCHER: BUTCHER_CAPS,
    Role.EMPLOYEE: EMPLOYEE_CAPS,
}


def caps_for(role: Role) -> frozenset[str]:
    return CAPS.get(role, EMPLOYEE_CAPS)


def can(user, capability: str) -> bool:
    """Si esta persona puede hacer eso. Sin usuario, no."""
    return bool(user) and capability in caps_for(user.role)


def checker(user):
    """La versión que reciben las plantillas: `can("money")`."""
    allowed = caps_for(user.role) if user else frozenset()
    return lambda capability: capability in allowed
