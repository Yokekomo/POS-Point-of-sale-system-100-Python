"""[01683] Lo que el cliente se lleva: toda su casa en un libro de Excel.

Esto existe porque faltaba, y faltaba en el peor sitio. La web promete, en los
siete idiomas, que los datos son del cliente. Y hasta hoy no había forma de
sacarlos: las hojas de `/descargas` son rejillas en blanco para imprimir y
rellenar a mano, no los registros de la casa. Un restaurante que dejara de
pagar perdía el acceso el mismo día a sus propias recepciones, despieces,
pesadas, mermas e inventarios —que son los papeles que le piden en una
inspección— y no tenía manera de haberlos copiado antes.

Y no es solo feo: el Reglamento (UE) 931/2011, artículo 3(3), obliga al
restaurante a tener esa información «recuperable» y a poder facilitarla a la
autoridad «sin demora indebida», y el (CE) 852/2004, artículo 5(4)(a) y (c), a
presentar la prueba del cumplimiento y a conservarla. Un programa que se queda
con los papeles el día que se deja de pagar le impide al cliente cumplir con
algo que la ley le exige a él. Eso no es una carencia del producto: es un
defecto.

Lo que hace este módulo es una sola cosa y la hace entera: coge todo lo que una
casa ha escrito, cada tabla en su hoja, y lo deja en un fichero que se abre con
cualquier programa de hojas de cálculo. Sin depender de que el programa siga
funcionando, ni de que la cuenta siga pagada, ni de nosotros.

Cuatro decisiones, y las cuatro tienen motivo:

**Excel y no CSV.** Un CSV por tabla son catorce ficheros sueltos que nadie
sabe volver a juntar. Un libro con catorce hojas se abre de un clic en el
ordenador del gestor, que es quien lo va a mirar.

**Las hijas van en su propia hoja, con la llave de la madre al lado.** Los
cortes de un despiece podrían escribirse dentro de la fila del despiece, todos
apretados en una celda; entonces no se pueden sumar ni filtrar, que es para lo
único que se van a usar. Van aparte, y su primera columna es el número de
despiece, para poder volver a juntarlas.

**Los nombres de las columnas salen del propio programa**, de las mismas claves
de texto que usan las pantallas. Así el que abre el fichero lee «Número de
primal» y no `serial`, y lo lee en su idioma —los siete—. Y no hay que mantener
doscientas ochenta traducciones más solo para esto.

**Las fechas van como fechas y los números como números.** Un registro
exportado con las cifras convertidas en texto no se puede sumar, y entonces no
sirve para lo único que se le va a pedir: cuadrar.
"""
from __future__ import annotations

import io
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Callable, Sequence

from sqlalchemy.orm import Session

from thegrill import models as m

# El ancho de las columnas en Excel se mide en caracteres, no en píxeles. Estos
# son los que caben sin que el encabezado salga cortado en las siete lenguas:
# el alemán y el húngaro son los que mandan.
ANCHO = 18
ANCHO_LARGO = 30

# Excel no admite estos caracteres en el nombre de una pestaña, ni nombres de
# más de 31 letras. Los títulos salen traducidos, así que hay que contar con lo
# que traigan.
PROHIBIDO = set("[]:*?/\\")
LARGO_PESTAÑA = 31


@dataclass(frozen=True)
class Col:
    """Una columna: de dónde sale el dato y cómo se llama en pantalla.

    `clave` puede ser una sola clave de texto o varias: varias se juntan con un
    punto medio —«Cerrar · Quién»—, que es como se dicen en el programa las
    cosas que no tienen un nombre propio en el catálogo. Es a propósito: una
    cadena nueva cuesta las siete lenguas, y una columna de un fichero que se
    abre una vez no lo vale.
    """
    clave: str | tuple[str, ...]
    saca: Callable[[Any], Any]
    ancho: int = ANCHO


@dataclass(frozen=True)
class Hoja:
    """Una hoja del libro: una tabla de la casa.

    `padre` es para las tablas que no llevan la casa escrita encima —los cortes
    de un despiece, las líneas de un inventario—: cuelgan de su madre, y es la
    madre la que dice de quién son. Sin esto habría que filtrarlas por una
    columna que no tienen; y escrito con descuido, sacarían las de todas las
    casas, que es exactamente lo que no puede pasar en un fichero que se envía.
    """
    nombre: str
    tabla: Any
    columnas: Sequence[Col]
    orden: Any = None
    padre: tuple[Any, Any] | None = None   # (clase madre, columna que la apunta)


def _quien(gente: dict[int, str], uid: int | None) -> str:
    """El nombre de quien lo hizo, o su número si esa persona ya no está.

    Se resuelve contra la lista de la casa y no se copia dentro de cada fila: si
    mañana alguien cambia de apellido, el histórico no se queda con el viejo. Y
    al que se fue se le exporta su número y no un hueco, porque un registro sin
    autor no vale para nada en una inspección.
    """
    if uid is None:
        return ""
    return gente.get(uid, f"#{uid}")


def hojas(gente: dict[int, str], tg: dict[int, str], cuenta: dict[int, Any],
          plantilla: dict[int, str], valores: dict[int, str]) -> list[Hoja]:
    """El mapa del libro. Una hoja por cosa que la casa escribe.

    El orden no es casual: primero lo que entra (piezas), luego lo que se hace
    con ello (despiece y sus dos hijas, pesadas, descongelados), luego lo que
    sale (mermas, ventas, traslados), después lo que se cuenta (inventarios y
    sus líneas, lotes de cámara) y al final los partes de la casa y sus sedes.
    Es el mismo orden en que se cuenta la historia de un kilo de carne.
    """
    return [
        # ------------------------------------------------------- lo que entra
        Hoja("m.rec.title", m.Primal, [
            Col("m.rec.serial", lambda r: r.serial, 14),
            Col("m.rec.lot", lambda r: r.lot or "", 20),
            Col("common.date", lambda r: r.received_date),
            Col("m.rec.sku", lambda r: r.sku or "", ANCHO_LARGO),
            Col("m.rec.kg", lambda r: r.received_kg),
            Col("m.df.left", lambda r: r.weight_kg),
            Col("m.rec.grade", lambda r: r.grade or ""),
            Col("m.rec.origin", lambda r: r.origin or ""),
            Col("m.rec.supplier_lot", lambda r: r.supplier_lot or ""),
            Col("m.rec.plant", lambda r: r.producer_plant or "", ANCHO_LARGO),
            Col("m.rec.est", lambda r: r.est_code or ""),
            Col("m.rec.breed", lambda r: r.breed or ""),
            Col("m.rec.slaughter", lambda r: r.slaughter_date),
            Col("m.rec.pack", lambda r: r.pack_date),
            Col("m.rec.use_by", lambda r: r.expiry_label),
            # [01696] La de la etiqueta y la del congelador son dos fechas distintas, y
            # para una pieza congelada manda la segunda. Sacar solo una de las
            # dos es justo el dato que falta el día que preguntan por ella.
            Col("m.rec.frozen_use_by", lambda r: r.frozen_use_by),
            Col("m.rec.storage", lambda r: r.storage),
            Col("m.rec.arrival_c", lambda r: r.arrival_c),
            Col("m.rec.arrival_frozen", lambda r: r.frozen_on_arrival),
            Col("m.rec.halal", lambda r: r.halal),
            Col("m.ch.name", lambda r: r.chamber or ""),
            Col("m.rec.price_kg", lambda r: r.landed_usd_per_kg),
            Col("m.rec.cost", lambda r: r.piece_cost_usd),
            Col("common.status", lambda r: r.status),
            Col("form.note", lambda r: r.notes or "", ANCHO_LARGO),
        ], orden=m.Primal.received_date),

        # -------------------------------------------- lo que se hace con ello
        Hoja("m.tg.title", m.Despiece, [
            Col("m.tg.number", lambda r: r.tg, 14),
            Col("common.date", lambda r: r.date),
            Col("m.tg.staff", lambda r: r.staff or "", ANCHO_LARGO),
            Col("m.rec.grade", lambda r: r.grade or ""),
            Col("m.tg.before_kg", lambda r: r.weight_before_kg),
            Col("m.tr.cut_kg", lambda r: r.total_cuts_kg),
            Col("m.tg.waste_kg", lambda r: r.waste_kg),
            Col("m.tg.trim", lambda r: r.trim_kg),
            Col("m.ag.yield", lambda r: r.yield_pct),
            Col("form.note", lambda r: r.notes or "", ANCHO_LARGO),
        ], orden=m.Despiece.date),

        Hoja("m.tg.primals", m.DespiecePrimal, [
            Col("m.tg.number", lambda r: tg.get(r.despiece_id, ""), 14),
            Col("m.rec.serial", lambda r: r.serial, 14),
            Col("m.rec.kg", lambda r: r.label_kg),
        ], orden=m.DespiecePrimal.despiece_id,
            padre=(m.Despiece, m.DespiecePrimal.despiece_id)),

        Hoja("m.tg.cuts", m.DespieceCut, [
            Col("m.tg.number", lambda r: tg.get(r.despiece_id, ""), 14),
            Col("m.tg.cut_name", lambda r: r.cut_name or "", ANCHO_LARGO),
            Col("meat.pieces", lambda r: r.pieces),
            Col("m.tg.g_piece", lambda r: r.weight_per_piece_g),
            Col("m.tg.total_kg", lambda r: r.total_kg),
            Col("m.tg.trim", lambda r: r.is_trim),
            Col("m.tg.by_weight", lambda r: r.by_weight),
            Col("m.tg.value_index", lambda r: r.value_index),
        ], orden=m.DespieceCut.despiece_id,
            padre=(m.Despiece, m.DespieceCut.despiece_id)),

        Hoja("m.ag.title", m.PrimalWeighing, [
            Col("m.rec.serial", lambda r: r.serial, 14),
            Col("common.date", lambda r: r.date),
            Col("m.tr.what", lambda r: r.kind),
            Col("m.df.opening", lambda r: r.previous_kg),
            Col("m.rec.kg", lambda r: r.kg),
            Col("m.ag.lost", lambda r: r.loss_kg),
            Col("m.tg.waste_kg", lambda r: r.waste_kg),
            Col("m.ag.per_kg", lambda r: r.cost_per_kg),
            Col("records.who", lambda r: _quien(gente, r.created_by)),
            Col("form.note", lambda r: r.note or "", ANCHO_LARGO),
        ], orden=m.PrimalWeighing.date),

        Hoja("m.df.title", m.DefrostEntry, [
            Col("common.date", lambda r: r.date),
            Col("m.df.shift", lambda r: r.shift or ""),
            Col("m.tr.what", lambda r: r.kind),
            Col("m.rec.serial", lambda r: r.lot_serial or "", 14),
            Col("meat.pieces", lambda r: r.pieces),
            Col("m.df.kg", lambda r: r.total_kg),
            Col("records.who", lambda r: _quien(gente, r.created_by)),
            Col("m.df.note", lambda r: r.note or "", ANCHO_LARGO),
        ], orden=m.DefrostEntry.date),

        # ------------------------------------------------------- lo que sale
        Hoja("waste.title", m.IngredientMovement, [
            Col("common.date", lambda r: r.date),
            Col("m.tr.what", lambda r: r.kind),
            Col("ing.qty", lambda r: r.qty),
            Col("waste.cost", lambda r: r.cost),
            Col("waste.source", lambda r: r.source or ""),
            Col(("waste.source", "form.record"), lambda r: r.source_ref or "", ANCHO_LARGO),
            Col("records.who", lambda r: _quien(gente, r.created_by)),
        ], orden=m.IngredientMovement.date),

        Hoja("m.ag.sales", m.WeightSale, [
            Col("common.date", lambda r: r.date),
            Col("m.rec.serial", lambda r: r.serial or "", 14),
            Col("sale.grams", lambda r: r.grams),
            Col("m.ag.money", lambda r: r.price),
            Col("rec.line_cost", lambda r: r.cost),
            Col("m.ag.per_kg", lambda r: r.cost_per_kg),
            Col("m.menu.dish", lambda r: r.dish or "", ANCHO_LARGO),
            Col("records.who", lambda r: _quien(gente, r.created_by)),
        ], orden=m.WeightSale.date),

        Hoja("m.tr.title", m.Transfer, [
            Col("common.date", lambda r: r.date),
            Col("m.tr.what", lambda r: r.kind),
            Col("m.rec.serial", lambda r: r.serial or "", 14),
            Col("m.rec.label_name", lambda r: r.label or "", ANCHO_LARGO),
            Col("m.rec.kg", lambda r: r.kg),
            Col("rec.line_cost", lambda r: r.cost),
            Col("m.tr.new_serial", lambda r: r.new_serial or "", 14),
            Col("records.who", lambda r: _quien(gente, r.created_by)),
            Col("form.note", lambda r: r.note or "", ANCHO_LARGO),
        ], orden=m.Transfer.date),

        # --------------------------------------------------- lo que se cuenta
        Hoja("inv.title", m.MeatCount, [
            Col("common.date", lambda r: r.date),
            Col("inv.period", lambda r: r.period),
            Col("common.status", lambda r: r.status),
            Col("records.who", lambda r: _quien(gente, r.created_by)),
            Col(("m.si.close", "records.who"), lambda r: _quien(gente, r.closed_by)),
            Col("inv.cancel_reason", lambda r: r.cancel_reason or "", ANCHO_LARGO),
            Col("form.note", lambda r: r.note or "", ANCHO_LARGO),
        ], orden=m.MeatCount.date),

        Hoja("inv.counted", m.MeatCountLine, [
            Col("common.date", lambda r: cuenta.get(r.count_id, "")),
            Col("m.tr.what", lambda r: r.kind),
            Col("waste.serial", lambda r: r.serial or "", 14),
            Col("m.rec.label_name", lambda r: r.label or "", ANCHO_LARGO),
            Col("inv.expected", lambda r: r.expected_kg),
            Col("inv.counted", lambda r: r.counted_kg),
            Col("meat.pieces", lambda r: r.counted_pieces),
            Col("m.rec.price_kg", lambda r: r.unit_cost),
            Col("common.status", lambda r: r.outcome),
            Col("inv.disputed", lambda r: r.disputed),
            Col("inv.who", lambda r: _quien(gente, r.counted_by)),
            Col("form.note", lambda r: r.note or "", ANCHO_LARGO),
        ], orden=m.MeatCountLine.count_id,
            padre=(m.MeatCount, m.MeatCountLine.count_id)),

        Hoja("ing.lots", m.IngredientLot, [
            Col("m.rec.serial", lambda r: r.serial or "", 14),
            Col(("m.tg.primals", "m.rec.serial"), lambda r: r.parent_serial or "", 14),
            Col("ing.lot_code", lambda r: r.lot_code or "", 20),
            Col("common.date", lambda r: r.received),
            Col("m.rec.use_by", lambda r: r.expiry),
            Col("ing.qty", lambda r: r.qty),
            Col("m.df.left", lambda r: r.qty_remaining),
            Col("ing.unit_cost", lambda r: r.unit_cost),
            Col("meat.pieces", lambda r: r.pieces),
            Col("m.tg.g_piece", lambda r: r.piece_weight_g),
            Col("m.rec.grade", lambda r: r.grade or ""),
            Col("m.rec.origin", lambda r: r.origin or ""),
            Col("m.rec.arrival_frozen", lambda r: r.frozen),
            Col("m.ch.name", lambda r: r.chamber or ""),
        ], orden=m.IngredientLot.received),

        # ---------------------------------------------- los partes y las sedes
        Hoja("nav.records", m.Record, [
            Col("common.date", lambda r: r.business_date),
            Col("form.record", lambda r: plantilla.get(r.template_id, ""), ANCHO_LARGO),
            Col("sheet.shift", lambda r: r.shift or ""),
            Col("common.status", lambda r: r.status),
            Col("records.who", lambda r: _quien(gente, r.created_by)),
            Col("records.data", lambda r: valores.get(r.id, ""), 60),
            Col("form.note", lambda r: r.note or "", ANCHO_LARGO),
        ], orden=m.Record.business_date),

        Hoja("m.si.title", m.Site, [
            Col("common.name", lambda r: r.name, ANCHO_LARGO),
            Col("m.si.kind", lambda r: r.kind),
            Col("acct.state", lambda r: r.active),
        ], orden=m.Site.name),
    ]


def _texto(t: Callable[[str], str], clave: str | tuple[str, ...]) -> str:
    """El encabezado ya traducido. Varias claves se juntan con un punto medio."""
    if isinstance(clave, tuple):
        return " · ".join(t(c) or c for c in clave)
    return t(clave) or clave


def _pestaña(t: Callable[[str], str], clave: str, usados: set[str]) -> str:
    """El nombre de la pestaña, como Excel lo admite y sin repetirse.

    Los títulos vienen traducidos, así que hay que contar con lo que traigan:
    una barra dentro de un título en otra lengua rompería el fichero entero, y
    dos pestañas con el mismo nombre también.
    """
    limpio = "".join(" " if c in PROHIBIDO else c for c in _texto(t, clave))
    nombre = " ".join(limpio.split())[:LARGO_PESTAÑA].strip() or clave[:LARGO_PESTAÑA]
    if nombre in usados:
        for n in range(2, 99):
            prueba = f"{nombre[:LARGO_PESTAÑA - 3].strip()} {n}"
            if prueba not in usados:
                nombre = prueba
                break
    usados.add(nombre)
    return nombre


def _celda(valor: Any) -> Any:
    """Las fechas como fechas y los números como números, para que se sumen.

    Un sí o un no se escribe con una marca y no con `True`, que en un fichero
    que abre un gestor no dice nada. Y un enum se escribe por su valor, no por
    su nombre de Python: hay que leer «RECIBIDO», no `PrimalStatus.RECEIVED`.
    """
    if valor is None:
        return ""
    if isinstance(valor, bool):
        return "✓" if valor else ""
    if isinstance(valor, (date, datetime, int, float, str)):
        return valor
    return getattr(valor, "value", str(valor))


def _refuerzos(session: Session, rid: int) -> tuple[dict, dict, dict, dict, dict]:
    """Los nombres que las filas guardan como número, resueltos de una vez.

    Se hace con cinco consultas y no fila a fila a propósito: una casa de un año
    tiene veinte mil pesadas, y preguntar por el autor de cada una son veinte
    mil idas y vueltas para escribir un nombre que se repite.
    """
    gente = {u.id: u.name for u in
             session.query(m.User).filter_by(restaurant_id=rid).all()}
    tg = {d.id: d.tg for d in
          session.query(m.Despiece).filter_by(restaurant_id=rid).all()}
    cuenta = {c.id: c.date for c in
              session.query(m.MeatCount).filter_by(restaurant_id=rid).all()}
    plantilla = {p.id: p.name for p in
                 session.query(m.RecordTemplate).filter_by(restaurant_id=rid).all()}

    # Los valores de cada parte —temperaturas, firmas, casillas— viven en su
    # propia tabla, uno por fila. Una hoja con cien mil filas de «clave, valor»
    # no la lee nadie: aquí van juntos en la celda de su parte, y lo que salió
    # fuera de banda va marcado, que es lo que se busca al abrir el fichero.
    valores: dict[int, list[str]] = {}
    filas = (session.query(m.RecordValue)
             .join(m.Record, m.RecordValue.record_id == m.Record.id)
             .filter(m.Record.restaurant_id == rid)
             .order_by(m.RecordValue.record_id, m.RecordValue.id).all())
    for v in filas:
        dato = v.value_text
        if dato is None and v.value_number is not None:
            dato = f"{v.value_number:g}"
        if dato is None and v.value_date is not None:
            dato = v.value_date.isoformat()
        if dato is None and v.value_bool is not None:
            dato = "✓" if v.value_bool else "✗"
        marca = " ⚠" if v.out_of_range else ""
        valores.setdefault(v.record_id, []).append(f"{v.field_key}={dato or ''}{marca}")
    return gente, tg, cuenta, plantilla, {k: " · ".join(v) for k, v in valores.items()}


def libro(session: Session, restaurant_id: int, lang: str = "es") -> bytes:
    """Todo lo que ha escrito una casa, en un libro de Excel.

    No lleva filtro de fechas a propósito: esto es «llévatelo todo», no «mira un
    trozo». Quien quiera un trimestre lo filtra en su hoja de cálculo; quien se
    va de la plataforma necesita el archivo entero, y una sola vez.
    """
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    from thegrill.meat import i18n_meat  # noqa: F401  — fusiona el catálogo de carne
    from thegrill.web import i18n

    # El catálogo de carne se funde con el general al importarse, y las claves
    # de las columnas son casi todas suyas. Importarlo aquí y no fiarlo al
    # orden de importación es la diferencia entre un encabezado traducido y un
    # fichero lleno de «m.rec.serial».
    t = i18n.translator(lang)
    refuerzos = _refuerzos(session, restaurant_id)

    wb = Workbook()
    wb.remove(wb.active)
    usados: set[str] = set()
    negrita = Font(bold=True)
    fondo = PatternFill("solid", fgColor="EEEEEE")
    arriba = Alignment(vertical="top")

    for hoja in hojas(*refuerzos):
        ws = wb.create_sheet(_pestaña(t, hoja.nombre, usados))
        ws.append([_texto(t, c.clave) for c in hoja.columnas])
        for i, c in enumerate(hoja.columnas, start=1):
            celda = ws.cell(row=1, column=i)
            celda.font = negrita
            celda.fill = fondo
            celda.alignment = arriba
            ws.column_dimensions[get_column_letter(i)].width = c.ancho

        if hoja.padre is None:
            consulta = session.query(hoja.tabla).filter(
                hoja.tabla.restaurant_id == restaurant_id)
        else:
            madre, apunta = hoja.padre
            consulta = (session.query(hoja.tabla)
                        .join(madre, apunta == madre.id)
                        .filter(madre.restaurant_id == restaurant_id))
        if hoja.orden is not None:
            consulta = consulta.order_by(hoja.orden, hoja.tabla.id)
        for fila in consulta.all():
            ws.append([_celda(c.saca(fila)) for c in hoja.columnas])

        # La primera fila se queda quieta al bajar: con mil piezas, sin esto no
        # se sabe qué columna se está mirando.
        ws.freeze_panes = "A2"

    fuera = io.BytesIO()
    wb.save(fuera)
    return fuera.getvalue()


def nombre_fichero(casa: str, hoy: date) -> str:
    """Cómo se llama el fichero que se descarga.

    Con el nombre de la casa y la fecha dentro: el día que alguien lo busque en
    su carpeta de descargas, dentro de dos años y con una inspección encima, el
    nombre tiene que decirle qué es sin abrirlo.

    Y solo letras y números de los de toda la vida, porque el nombre lo escribe
    el cliente y esto acaba en una cabecera HTTP, donde un acento o una comilla
    rompen la descarga o algo peor. Los acentos no se tachan: se deshacen —
    «Asador El Niño» sale «Asador-El-Nino» y no «Asador-El-Ni-o»—, que para el
    que lo busca en su carpeta no es lo mismo. En árabe y en húngaro no siempre
    queda nada que deshacer, y entonces manda la fecha: por eso va también en
    el nombre y no solo dentro.
    """
    plano = unicodedata.normalize("NFKD", casa or "")
    limpio = "".join(c if (c.isascii() and c.isalnum()) or c in " -_" else "-"
                     for c in plano if not unicodedata.combining(c))
    limpio = "-".join(limpio.split())
    while "--" in limpio:
        limpio = limpio.replace("--", "-")
    limpio = limpio.strip("-") or "casa"
    return f"{limpio[:40].strip('-')}-{hoy.isoformat()}.xlsx"
