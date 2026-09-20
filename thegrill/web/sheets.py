"""Hojas de registro en Excel, listas para imprimir y rellenar a mano.

Son el respaldo de papel: se imprimen, se cuelgan en cocina y se rellenan con
bolígrafo cuando no hay móvil a mano o se cae la conexión. Cada hoja sale de la
misma plantilla que el formulario de la pantalla, así que las columnas del papel
y los campos de la aplicación coinciden siempre.

No llevan fórmulas: son rejillas para escribir a mano.
"""
import io
import re
from datetime import date

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from thegrill.models import FieldType, RecordTemplate, Restaurant
from thegrill.web.i18n import DEFAULT_LANG, direction, t

FONT = "Arial"
BLANK_ROWS = 20
PORTRAIT_MAX_WIDTH = 90    # caracteres de ancho que caben en un A4 vertical

INK = "1C1B19"
MUTED = "6B6862"
HEADER_FILL = "EDEAE4"
EXAMPLE_FILL = "F6F5F2"
LINE = "B9B5AE"

thin = Side(style="thin", color=LINE)
GRID = Border(left=thin, right=thin, top=thin, bottom=thin)
WRITE_LINE = Border(bottom=Side(style="thin", color=INK))


def sheet_title(name: str, used: set[str]) -> str:
    """Excel limita el nombre de pestaña a 31 caracteres y prohíbe : \\ / ? * [ ]"""
    clean = re.sub(r"[:\\/?*\[\]]", " ", name).strip()[:31] or "Hoja"
    candidate, n = clean, 1
    while candidate.lower() in used:
        n += 1
        suffix = f" {n}"
        candidate = clean[:31 - len(suffix)] + suffix
    used.add(candidate.lower())
    return candidate


def column_header(field, lang: str) -> str:
    """Etiqueta del campo con su unidad, sus límites y si es obligatorio."""
    parts = [field.label]
    if field.unit:
        parts.append(f"({field.unit})")
    if field.min_value is not None and field.max_value is not None:
        parts.append(t(lang, "sheet.limits", min=_num(field.min_value), max=_num(field.max_value)))
    elif field.min_value is not None:
        parts.append(t(lang, "sheet.min_only", min=_num(field.min_value)))
    elif field.max_value is not None:
        parts.append(t(lang, "sheet.max_only", max=_num(field.max_value)))
    header = " ".join(parts)
    return header + " *" if field.required else header


def _num(value: float) -> str:
    return f"{value:.10g}"


def example_value(field, lang: str) -> str:
    """Un valor realista por campo, para que se vea el formato esperado."""
    if field.type == FieldType.SELECT:
        options = [o.strip() for o in (field.options or "").split("|") if o.strip()]
        return options[0] if options else ""
    if field.type == FieldType.NUMBER:
        if field.min_value is not None and field.max_value is not None:
            return _num(round((field.min_value + field.max_value) / 2, 1))
        if field.max_value is not None:
            return _num(field.max_value)
        if field.min_value is not None:
            return _num(field.min_value)
        return "12"
    if field.type == FieldType.DATE:
        return date.today().isoformat()
    if field.type == FieldType.BOOL:
        return t(lang, "sheet.yes")
    return "—"


def build_sheet(ws: Worksheet, template: RecordTemplate, restaurant: Restaurant,
                lang: str = DEFAULT_LANG, blank_rows: int = BLANK_ROWS) -> Worksheet:
    """Monta una hoja imprimible a partir de una plantilla de registro."""
    fields = list(template.fields)
    # Nº + Fecha + Hora + un campo por columna
    headers = ["Nº", t(lang, "common.date"), t(lang, "sheet.time")] + \
              [column_header(f, lang) for f in fields]
    last_col = len(headers)
    span = f"A1:{get_column_letter(last_col)}1"

    ws.sheet_view.rightToLeft = direction(lang) == "rtl"
    ws.sheet_view.showGridLines = False

    # --- cabecera del documento
    ws.merge_cells(span)
    cell = ws["A1"]
    cell.value = template.name
    cell.font = Font(name=FONT, size=15, bold=True, color=INK)
    cell.alignment = Alignment(vertical="center")
    ws.row_dimensions[1].height = 24

    ws.merge_cells(span.replace("1", "2"))
    subtitle = ws["A2"]
    subtitle.value = " · ".join(x for x in (restaurant.name, template.description) if x)
    subtitle.font = Font(name=FONT, size=10, color=MUTED)

    # --- datos de la hoja que se escriben a mano
    row = 4
    pairs = [(t(lang, "sheet.restaurant"), restaurant.name),
             (t(lang, "sheet.shift"), ""),
             (t(lang, "sheet.filled_by"), "")]
    col = 1
    for label, value in pairs:
        label_cell = ws.cell(row=row, column=col, value=f"{label}:")
        label_cell.font = Font(name=FONT, size=10, bold=True, color=INK)
        value_cell = ws.cell(row=row, column=col + 1, value=value)
        value_cell.font = Font(name=FONT, size=10, color=INK)
        value_cell.border = WRITE_LINE
        col += 2
    ws.row_dimensions[row].height = 20

    # --- leyenda: qué se rellena y qué no
    ws.merge_cells(f"A6:{get_column_letter(last_col)}6")
    legend = ws["A6"]
    legend.value = f"{t(lang, 'sheet.legend')}  {t(lang, 'sheet.required_mark')}"
    legend.font = Font(name=FONT, size=9, italic=True, color=MUTED)
    ws.row_dimensions[6].height = 16

    # --- fila de encabezados
    head_row = 8
    for i, text in enumerate(headers, start=1):
        c = ws.cell(row=head_row, column=i, value=text)
        c.font = Font(name=FONT, size=10, bold=True, color=INK)
        c.fill = PatternFill("solid", fgColor=HEADER_FILL)
        c.border = GRID
        c.alignment = Alignment(wrap_text=True, vertical="center", horizontal="center")
    ws.row_dimensions[head_row].height = 34

    # --- fila de ejemplo, en gris, para que se vea el formato
    example_row = head_row + 1
    example = [t(lang, "sheet.example"), date.today().isoformat(), "08:30"] + \
              [example_value(f, lang) for f in fields]
    for i, value in enumerate(example, start=1):
        c = ws.cell(row=example_row, column=i, value=value)
        c.font = Font(name=FONT, size=9, italic=True, color=MUTED)
        c.fill = PatternFill("solid", fgColor=EXAMPLE_FILL)
        c.border = GRID
        c.alignment = Alignment(horizontal="center" if i <= 3 else "left")
    ws.row_dimensions[example_row].height = 18

    # --- rejilla en blanco
    first_blank = example_row + 1
    for n in range(blank_rows):
        r = first_blank + n
        for i in range(1, last_col + 1):
            c = ws.cell(row=r, column=i)
            c.border = GRID
            c.font = Font(name=FONT, size=11, color=INK)
            if i == 1:
                c.value = n + 1
                c.alignment = Alignment(horizontal="center")
        ws.row_dimensions[r].height = 22

    # --- firma
    sign_row = first_blank + blank_rows + 1
    sign = ws.cell(row=sign_row, column=1, value=f"{t(lang, 'sheet.signature')}:")
    sign.font = Font(name=FONT, size=10, bold=True, color=INK)
    for i in range(2, min(last_col, 6) + 1):
        ws.cell(row=sign_row, column=i).border = WRITE_LINE
    ws.row_dimensions[sign_row].height = 26

    # --- anchos: generosos, porque se escribe a mano
    ws.column_dimensions["A"].width = 6
    ws.column_dimensions["B"].width = 13
    ws.column_dimensions["C"].width = 9
    total_width = 6 + 13 + 9
    for i, f in enumerate(fields, start=4):
        width = 26 if f.type in (FieldType.TEXT, FieldType.SELECT) else 16
        ws.column_dimensions[get_column_letter(i)].width = width
        total_width += width

    # --- impresión: A4, todo a lo ancho de una página, encabezado en cada hoja
    # Vertical mientras quepa: así entran más filas por página. En vertical caben
    # unos 90 caracteres de ancho; pasado eso, apaisado.
    ws.page_setup.paperSize = ws.PAPERSIZE_A4
    ws.page_setup.orientation = "portrait" if total_width <= PORTRAIT_MAX_WIDTH else "landscape"
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.print_title_rows = f"1:{head_row}"
    ws.print_area = f"A1:{get_column_letter(last_col)}{sign_row}"
    ws.page_margins.left = ws.page_margins.right = 0.4
    ws.page_margins.top = ws.page_margins.bottom = 0.5
    ws.oddFooter.right.text = "&P / &N"
    ws.oddFooter.left.text = f"{restaurant.name} · {template.name}"
    ws.oddFooter.left.size = ws.oddFooter.right.size = 8
    ws.freeze_panes = ws.cell(row=head_row + 1, column=1)
    return ws


def workbook_for(templates: list[RecordTemplate], restaurant: Restaurant,
                 lang: str = DEFAULT_LANG, blank_rows: int = BLANK_ROWS) -> bytes:
    """Un libro con una pestaña por plantilla."""
    wb = Workbook()
    wb.remove(wb.active)
    used: set[str] = set()
    for template in templates:
        ws = wb.create_sheet(sheet_title(template.name, used))
        build_sheet(ws, template, restaurant, lang, blank_rows)
    if not wb.sheetnames:                      # un libro sin hojas no es válido
        wb.create_sheet("—")
    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


def filename_for(name: str, lang: str = DEFAULT_LANG) -> str:
    """Nombre de archivo seguro; si el nombre no tiene letras latinas usa el código."""
    clean = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("_")
    return f"{clean or 'hoja'}.xlsx"
