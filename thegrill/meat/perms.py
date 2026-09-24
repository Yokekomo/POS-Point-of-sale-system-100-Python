"""[00519] Quién puede hacer qué.

Tres niveles, pensados desde el trabajo y no desde el organigrama:

- **Dueño de la plataforma**: da de alta las casas, cobra el recibo y puede
  bloquear una cuenta entera. Es el único que toca la configuración del
  restaurante.
- **Manager**: su casa entera. Es el único de la casa que ve el dinero —costes, food cost, el valor
  de la cámara, lo que se pierde al día— y el único que toca la carta, las
  ventas y el equipo.
- **Carnicero**: la carne entera. Recibe primales, los despieza, los mete a
  madurar o al congelador y los vuelve a pesar, saca a descongelar, cuenta,
  hace el inventario, apunta la merma y manda carne del obrador a los locales. Ve lo que queda de primales y los
  cortes de cada pieza, en kilos y en piezas. El dinero, no.
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
AGE = "age"                # mover piezas a madurar o al congelador, y pesarlas
TRANSFER = "transfer"      # mandar carne del obrador a un local
STOCK = "stock"            # ver cámara, cortes y trazabilidad de una pieza
CATALOGUE = "catalogue"    # dar de alta cortes y artículos
MENU = "menu"              # carta, ingredientes del plato y ventas
MONEY = "money"            # ver costes, food cost y valor
TEAM = "team"              # equipo, alertas y configuración de la casa
FIX = "fix"                # recuperar piezas y dar de alta lo que apareció
PLATFORM = "platform"      # la plataforma: altas de casas y el recibo del mes
SETTINGS = "settings"      # la configuración del restaurante

BUTCHER_CAPS = frozenset({RECEIVE, BUTCHER, DEFROST, CLOSE_SHIFT, COUNT,
                          INVENTORY, WASTE, STOCK, AGE, TRANSFER})
EMPLOYEE_CAPS = frozenset({DEFROST, COUNT, WASTE, STOCK})
MANAGER_CAPS = frozenset({RECEIVE, BUTCHER, DEFROST, CLOSE_SHIFT, COUNT, INVENTORY,
                          WASTE, STOCK, AGE, TRANSFER, CATALOGUE, MENU, MONEY, TEAM, FIX})
OWNER_CAPS = MANAGER_CAPS | {PLATFORM, SETTINGS}

CAPS: dict[Role, frozenset[str]] = {
    Role.OWNER: OWNER_CAPS,
    Role.MANAGER: MANAGER_CAPS,
    Role.BUTCHER: BUTCHER_CAPS,
    Role.EMPLOYEE: EMPLOYEE_CAPS,
}


def caps_for(role: Role) -> frozenset[str]:
    """[00520] Lo que puede hacer ese nivel. Un nivel que no consta, lo mínimo."""
    return CAPS.get(role, EMPLOYEE_CAPS)


def can(user, capability: str) -> bool:
    """[00521] Si esta persona puede hacer eso. Sin usuario, no."""
    return bool(user) and capability in caps_for(user.role)


def checker(user):
    """[00522] La versión que reciben las plantillas: `can("money")`."""
    allowed = caps_for(user.role) if user else frozenset()
    return lambda capability: capability in allowed


# [00526] ==================================================== quién manda sobre quién
# Un grupo con obrador y tres locales no lo lleva una sola persona. El manager
# general —el que no tiene sede— es el de la casa entera y da de alta a los
# managers de cada local; el manager de un local lleva el suyo y su gente, y
# no toca al de al lado ni al de arriba.
#
#   plataforma  >  manager general  >  manager de local  >  carnicero, ayudante
#
# Esto se comprueba en la ruta y no en la plantilla: una pantalla sin el
# desplegable no es una puerta cerrada, porque el formulario se puede mandar
# a mano.
def is_general_manager(user) -> bool:
    """[00523] El manager de la casa entera: el que no está atado a una sede."""
    return bool(user) and user.role == Role.MANAGER and not getattr(user, "site_id", None)


def can_manage(actor, target) -> bool:
    """[00524] Si `actor` puede tocar la cuenta de `target`: su nivel, su clave, su alta.

    Nadie toca al dueño de la plataforma salvo él mismo, y nadie toca a un
    igual: dos managers generales de la misma casa no se dan de baja el uno al
    otro.
    """
    if not (actor and target) or actor.restaurant_id != target.restaurant_id:
        return False
    if actor.role == Role.OWNER:
        return True
    if target.role == Role.OWNER:
        return False                       # la plataforma no la toca la casa
    if target.role == Role.MANAGER:
        # [00527] A un manager solo le entra el general, y solo si el otro es de local.
        return is_general_manager(actor) and not is_general_manager(target)
    if actor.role != Role.MANAGER:
        return False
    # [00528] Y el de un local, solo a los suyos. Estaba escrito arriba —«no toca al
    # de al lado»— y no se comprobaba: el encargado de Playa podía cambiarle
    # la contraseña a la carnicera de Sierra y entrar como ella. En un grupo
    # con cuatro locales eso son cuatro puertas abiertas entre sí, y el rastro
    # de quién apuntó qué deja de valer: cualquiera puede apuntar como
    # cualquiera. Quien lleva la casa entera —el manager sin sede— sí entra a
    # todos, que para eso la lleva.
    if is_general_manager(actor):
        return True
    return getattr(actor, "site_id", None) == getattr(target, "site_id", None)


def grantable_roles(actor) -> list:
    """[00525] Los niveles que esa persona puede repartir. Nunca uno por encima suyo."""
    if not actor:
        return []
    if actor.role == Role.OWNER:
        return list(Role)
    if is_general_manager(actor):
        return [r for r in Role if r != Role.OWNER]
    if actor.role == Role.MANAGER:
        return [r for r in Role if r not in (Role.OWNER, Role.MANAGER)]
    return []
