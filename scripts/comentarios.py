"""Numera los comentarios del código y hace el índice en Excel.

    python -m scripts.comentarios            # numera y escribe COMENTARIOS.xlsx
    python -m scripts.comentarios --mirar    # solo mira, no toca nada

Para qué sirve: con mil setecientos comentarios repartidos en ciento veinte
ficheros, encontrar «aquello que explicaba por qué el reparto va por restos
mayores» es media hora de `grep` con suerte. Con un número delante —`[00423]`—
se busca en el Excel por lo que uno recuerda, sale el número, y ese número se
busca en el código y cae en el sitio exacto.

**El número vive en el propio código**, no en una lista aparte. Eso es lo que
hace que volver a pasar esto no renumere nada: lo que ya tiene número se queda
con el suyo, y solo lo nuevo coge el siguiente libre. Una lista aparte se
desincroniza el primer día que alguien mueve una función de sitio; un número
escrito dentro del comentario se mueve con él.

Se numeran tres cosas:

  * los docstrings de módulo, clase y función;
  * los bloques de comentario —una o varias líneas seguidas que empiezan por
    almohadilla—, que son los que suelen llevar el aviso que salva;
  * los comentarios de las plantillas, entre `{#` y `#}`.

Lo que NO se numera: las líneas sueltas de `# noqa`, `# type:` y demás, que no
explican nada, y los docstrings de las pruebas, que ya se leen por su nombre.
"""
from __future__ import annotations

import ast
import pathlib
import re
import sys
from dataclasses import dataclass

RAIZ = pathlib.Path(__file__).resolve().parent.parent
CODIGO = RAIZ / "thegrill"
INDICE = RAIZ / "COMENTARIOS.xlsx"

# El número, tal y como se ve en el código. Cinco cifras: con mil setecientos
# comentarios hoy, cinco dan sitio para crecer veinte años sin renumerar.
MARCA = re.compile(r"\[(\d{5})\]")
ANCHO = 5

# Lo que no es una explicación: no lleva número ni sale en el índice.
RUIDO = re.compile(r"^#\s*(noqa|type:|pragma|pylint|ruff|fmt:|isort)", re.I)


@dataclass
class Comentario:
    numero: int | None
    fichero: str
    linea: int
    tipo: str          # «módulo», «clase», «función», «bloque», «plantilla»
    donde: str         # el nombre de lo que explica, si lo tiene
    texto: str
    # Dónde empieza el literal en el fichero, para los docstrings. Se marca
    # por posición y no buscando el texto: uno que lleve una barra invertida
    # o comillas dentro no se escribe en el fichero igual que como lo devuelve
    # el intérprete, y buscarlo no encontraba nada. Pasaba de verdad, en el
    # que explica qué caracteres prohíbe Excel en el nombre de una pestaña.
    columna: int = 0


def _primera_frase(texto: str) -> str:
    """El resumen: la primera frase, que es lo que se lee en una lista."""
    limpio = " ".join(texto.replace("\n", " ").split()).strip("-= ")
    corte = limpio.find(". ")
    return (limpio[: corte + 1] if 0 < corte < 160 else limpio[:160]).strip()


def _numero_de(texto: str) -> int | None:
    """El número que ya lleva puesto, si lo lleva."""
    hallado = MARCA.search(texto[:80])
    return int(hallado.group(1)) if hallado else None


# ------------------------------------------------- leer lo que hay
def _docstrings(ruta: pathlib.Path, fuente: str) -> list[Comentario]:
    """Los docstrings del fichero, con el nombre de lo que explican."""
    try:
        arbol = ast.parse(fuente)
    except SyntaxError:
        return []
    fuera: list[Comentario] = []
    for nodo in ast.walk(arbol):
        if isinstance(nodo, ast.Module):
            tipo, donde = "módulo", ruta.stem
        elif isinstance(nodo, ast.ClassDef):
            tipo, donde = "clase", nodo.name
        elif isinstance(nodo, (ast.FunctionDef, ast.AsyncFunctionDef)):
            tipo, donde = "función", nodo.name
        else:
            continue
        texto = ast.get_docstring(nodo, clean=False)
        if not texto:
            continue
        # La línea del propio texto, no la del `def`: es donde se busca.
        primera = nodo.body[0]
        fuera.append(Comentario(_numero_de(texto), str(ruta.relative_to(RAIZ)),
                                primera.lineno, tipo, donde, texto,
                                primera.col_offset))
    return fuera


def _bloques(ruta: pathlib.Path, fuente: str) -> list[Comentario]:
    """Los bloques de almohadilla: una o más líneas seguidas de comentario."""
    fuera: list[Comentario] = []
    lineas = fuente.split("\n")
    i = 0
    while i < len(lineas):
        if not lineas[i].lstrip().startswith("#"):
            i += 1
            continue
        arranca = i
        juntas: list[str] = []
        while i < len(lineas) and lineas[i].lstrip().startswith("#"):
            juntas.append(lineas[i].lstrip()[1:].strip())
            i += 1
        texto = "\n".join(juntas).strip()
        crudo = lineas[arranca].lstrip()
        # Una raya de separación —`# ---- cámara ----`— no explica nada por sí
        # sola, pero si debajo lleva texto sí: se numera el conjunto.
        if not texto or RUIDO.match(crudo) or set(texto) <= set("-=# "):
            continue
        fuera.append(Comentario(_numero_de(texto), str(ruta.relative_to(RAIZ)),
                                arranca + 1, "bloque", "", texto))
    return fuera


def _plantilla(ruta: pathlib.Path, fuente: str) -> list[Comentario]:
    """Los comentarios de las plantillas, entre `{#` y `#}`."""
    fuera: list[Comentario] = []
    for hallado in re.finditer(r"\{#(.*?)#\}", fuente, re.S):
        texto = hallado.group(1).strip()
        if not texto:
            continue
        linea = fuente[: hallado.start()].count("\n") + 1
        fuera.append(Comentario(_numero_de(texto), str(ruta.relative_to(RAIZ)),
                                linea, "plantilla", "", texto))
    return fuera


def recoger() -> list[Comentario]:
    """Todo lo que hay que numerar, en el orden en que está escrito."""
    todo: list[Comentario] = []
    for ruta in sorted(CODIGO.rglob("*.py")):
        fuente = ruta.read_text()
        todo += _docstrings(ruta, fuente) + _bloques(ruta, fuente)
    for ruta in sorted(CODIGO.rglob("*.html")):
        todo += _plantilla(ruta, ruta.read_text())
    return todo


# ------------------------------------------------- escribir el número
def _con_numero(texto: str, numero: int) -> str:
    """El mismo texto con `[00042]` delante de la primera palabra.

    Delante y no detrás: lo primero que se ve al abrir el fichero es el
    número, y eso es lo que se copia para buscarlo en el índice. Detrás
    habría que leerse el comentario entero para encontrarlo.
    """
    marca = f"[{numero:0{ANCHO}d}]"
    sin_espacio = texto.lstrip("\n")
    sangria = texto[: len(texto) - len(sin_espacio)]
    return f"{sangria}{marca} {sin_espacio}"


def _poner_en_docstring(fuente: str, c: Comentario, numero: int) -> str:
    """Mete el número justo detrás de las comillas que abren.

    Por posición y no buscando el texto. Un docstring con una barra invertida
    dentro —el que explica qué caracteres prohíbe Excel en el nombre de una
    pestaña— no se escribe en el fichero igual que como lo devuelve el
    intérprete, así que buscarlo no encontraba nada: se quedaba sin número y
    cogía uno nuevo en cada pasada.
    """
    lineas = fuente.split("\n")
    i = c.linea - 1
    if i >= len(lineas):
        return fuente
    linea = lineas[i]
    j = c.columna
    # Delante de las comillas puede haber una letra: r, f, b, u, o dos.
    while j < len(linea) and linea[j] in "rRfFbBuU":
        j += 1
    if j >= len(linea) or linea[j] not in "\"'":
        return fuente
    comilla = linea[j]
    largo = 3 if linea[j:j + 3] == comilla * 3 else 1
    corte = j + largo
    lineas[i] = f"{linea[:corte]}[{numero:0{ANCHO}d}] {linea[corte:].lstrip()}"
    return "\n".join(lineas)


def _poner_en_bloque(fuente: str, c: Comentario, numero: int) -> str:
    """El número en la primera línea del bloque, detrás de la almohadilla."""
    lineas = fuente.split("\n")
    i = c.linea - 1
    if i >= len(lineas) or not lineas[i].lstrip().startswith("#"):
        return fuente
    cruda = lineas[i]
    sangria = cruda[: len(cruda) - len(cruda.lstrip())]
    resto = cruda.lstrip()[1:]
    lineas[i] = f"{sangria}# [{numero:0{ANCHO}d}]{resto}"
    return "\n".join(lineas)


def _poner_en_plantilla(fuente: str, c: Comentario, numero: int) -> str:
    """El número al principio del comentario de la plantilla."""
    viejo = "{#" + fuente.split("{#", 1)[1].split("#}", 1)[0] + "#}" if "{#" in fuente else ""
    # Se busca el texto exacto para no confundir dos comentarios parecidos.
    aguja = c.texto
    if aguja not in fuente:
        return fuente
    return fuente.replace(aguja, f"[{numero:0{ANCHO}d}] {aguja}", 1)


def numerar(mirar: bool = False) -> list[Comentario]:
    """Da número a lo que no lo tiene y lo escribe en el código.

    Lo que ya lo tiene se queda con el suyo: así se puede volver a pasar esto
    cada vez que se toca el código, sin que un número escrito en una libreta
    hace tres meses deje de valer.
    """
    todo = recoger()
    usados = {c.numero for c in todo if c.numero}
    siguiente = max(usados) + 1 if usados else 1

    porfichero: dict[str, list[Comentario]] = {}
    for c in todo:
        if c.numero is None:
            c.numero = siguiente
            siguiente += 1
            porfichero.setdefault(c.fichero, []).append(c)
    # Cuántos son nuevos de verdad. Se apunta aparte porque a partir de aquí
    # ya todos tienen número y contarlos diría siempre el total.
    numerar.nuevos = sum(len(v) for v in porfichero.values())

    if mirar:
        return todo

    for fichero, nuevos in porfichero.items():
        ruta = RAIZ / fichero
        fuente = ruta.read_text()
        # De abajo arriba: así las líneas de los de arriba no se mueven.
        for c in sorted(nuevos, key=lambda x: -x.linea):
            if c.tipo == "bloque":
                fuente = _poner_en_bloque(fuente, c, c.numero)
            elif c.tipo == "plantilla":
                fuente = _poner_en_plantilla(fuente, c, c.numero)
            else:
                fuente = _poner_en_docstring(fuente, c, c.numero)
        ruta.write_text(fuente)
    return todo


# ------------------------------------------------- el índice, en Excel
def a_excel(todo: list[Comentario], destino: pathlib.Path = INDICE) -> None:
    """Escribe el índice: una fila por comentario, para buscar y filtrar.

    Va en Excel porque es donde se busca sin saber SQL: se abre, se le da al
    filtro y se escribe lo que uno recuerda. Las columnas van en el orden en
    que se pregunta: primero el número —que es lo que se ve en el código—,
    luego dónde está, y al final el texto entero.
    """
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    libro = Workbook()
    hoja = libro.active
    hoja.title = "Comentarios"

    cabeceras = ["Número", "Fichero", "Línea", "Tipo", "Qué explica",
                 "Resumen", "Texto completo"]
    hoja.append(cabeceras)
    for celda in hoja[1]:
        celda.font = Font(bold=True, color="FFFFFF")
        celda.fill = PatternFill("solid", fgColor="A8471F")   # el color de la casa
        celda.alignment = Alignment(vertical="center")

    for c in sorted(todo, key=lambda x: x.numero or 0):
        hoja.append([f"{c.numero:0{ANCHO}d}", c.fichero, c.linea, c.tipo,
                     c.donde, _primera_frase(c.texto), c.texto])

    anchos = [10, 42, 8, 11, 26, 70, 110]
    for i, ancho in enumerate(anchos, start=1):
        hoja.column_dimensions[get_column_letter(i)].width = ancho
    for fila in hoja.iter_rows(min_row=2):
        fila[6].alignment = Alignment(wrap_text=True, vertical="top")
        fila[5].alignment = Alignment(wrap_text=True, vertical="top")

    # El filtro puesto y la cabecera fija: al abrirlo ya se puede buscar sin
    # tocar nada, que es la diferencia entre que se use y que no.
    hoja.auto_filter.ref = hoja.dimensions
    hoja.freeze_panes = "A2"

    resumen = libro.create_sheet("Por fichero")
    resumen.append(["Fichero", "Comentarios", "Del", "Al"])
    for celda in resumen[1]:
        celda.font = Font(bold=True, color="FFFFFF")
        celda.fill = PatternFill("solid", fgColor="A8471F")
    porfichero: dict[str, list[int]] = {}
    for c in todo:
        porfichero.setdefault(c.fichero, []).append(c.numero or 0)
    for fichero, numeros in sorted(porfichero.items()):
        resumen.append([fichero, len(numeros),
                        f"{min(numeros):0{ANCHO}d}", f"{max(numeros):0{ANCHO}d}"])
    for letra, ancho in (("A", 46), ("B", 14), ("C", 10), ("D", 10)):
        resumen.column_dimensions[letra].width = ancho
    resumen.auto_filter.ref = resumen.dimensions
    resumen.freeze_panes = "A2"

    libro.save(destino)


def main(argv: list[str] | None = None) -> int:
    """Numera y escribe el índice. Con `--mirar`, solo cuenta."""
    argv = sys.argv[1:] if argv is None else argv
    mirar = "--mirar" in argv
    todo = numerar(mirar=mirar)
    nuevos = getattr(numerar, "nuevos", 0)
    if mirar:
        print(f"{len(todo)} comentarios; sin número todavía: {nuevos}")
        return 0
    a_excel(todo)
    print(f"{len(todo)} comentarios en el índice · {nuevos} numerados ahora "
          f"· {INDICE.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
