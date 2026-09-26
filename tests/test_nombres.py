"""Un nombre que no existe, encontrado en un segundo y no en doce minutos.

Una ruta que usa una variable que en esa función no existe no se cae al
escribirla ni al arrancar: se cae **al pintar esa pantalla**, y solo si alguien
llega por el camino que pasa por esa línea. Aquí ha pasado de verdad: al
traducir los avisos, el sitio donde se dice el error de la tarifa quedó
pidiendo un `lang` que en esa función no estaba. La pantalla se caía solo
cuando el dueño escribía una rebaja mal puesta, que no es todos los días.

La suite entera lo encuentra, y tarda doce minutos. Esto tarda un segundo y
mira **todas** las funciones, también las que ninguna prueba recorre.
"""
import builtins
import pathlib
import symtable

RAIZ = pathlib.Path(__file__).resolve().parents[1]

# Los que Python pone en cada módulo sin que nadie los escriba.
DEL_PROPIO_PYTHON = {"__file__", "__name__", "__doc__", "__package__", "__spec__",
                     "__loader__", "__builtins__", "__path__"}


def _huerfanos(fichero: pathlib.Path) -> list[str]:
    """Nombres que una función usa y que no están ni dentro ni en el módulo."""
    tabla = symtable.symtable(fichero.read_text(), str(fichero), "exec")
    fuera = set(tabla.get_identifiers()) | set(dir(builtins)) | DEL_PROPIO_PYTHON
    sueltos: list[str] = []

    def mirar(donde, camino=""):
        for hijo in donde.get_children():
            nombre = f"{camino}.{hijo.get_name()}" if camino else hijo.get_name()
            if hijo.get_type() == "function":
                for simbolo in hijo.get_symbols():
                    # `is_global` aquí quiere decir «no es de esta función ni de
                    # ninguna que la envuelva»: o está en el módulo, o no está.
                    if simbolo.is_global() and simbolo.get_name() not in fuera:
                        donde_esta = (fichero.relative_to(RAIZ)
                                      if fichero.is_relative_to(RAIZ) else fichero)
                        sueltos.append(f"{donde_esta}:{nombre} → "
                                       f"«{simbolo.get_name()}»")
            mirar(hijo, nombre)

    mirar(tabla)
    return sueltos


def test_no_function_asks_for_a_name_that_does_not_exist():
    """Ni una sola, en todo el programa."""
    sueltos: list[str] = []
    for fichero in sorted((RAIZ / "thegrill").rglob("*.py")):
        sueltos += _huerfanos(fichero)
    assert sueltos == [], sueltos


def test_the_guard_would_catch_it():
    """Y que esto no dé verde por no estar mirando nada.

    Se le da el fallo exacto que se coló —usar `lang` donde no lo hay— y tiene
    que verlo. Sin esto, un día el comprobador deja de funcionar y nadie se
    entera, que es la peor clase de guardia.
    """
    import tempfile

    with tempfile.TemporaryDirectory() as carpeta:
        malo = pathlib.Path(carpeta) / "malo.py"
        malo.write_text("def guardar(e):\n    return traducir(e, lang)\n")
        encontrados = _huerfanos(malo)
    assert any("lang" in x for x in encontrados), encontrados
