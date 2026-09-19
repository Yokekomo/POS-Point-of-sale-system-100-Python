"""Plantillas de registro por defecto, válidas para cualquier restaurante.

Un restaurante nuevo arranca con estas y el manager las edita, desactiva o
añade las suyas. Nada aquí es específico de una cocina concreta.

Refrigeración y congelación van en plantillas separadas a propósito: cada una
tiene su propio límite legal, y un solo campo numérico no puede validar los dos.
"""
from sqlalchemy.orm import Session

from thegrill.models import FieldType, RecordTemplate, TemplateField

DEFAULT_TEMPLATES = [
    {
        "code": "temp_refrigeracion", "name": "Temperatura de refrigeración", "category": "haccp",
        "description": "Cámaras, neveras y vitrinas. Límite legal habitual: hasta +5 °C.",
        "frequency": "shift", "expected_per_day": 2, "requires_photo": False, "sort_order": 10,
        "fields": [
            {"key": "unidad", "label": "Equipo", "type": FieldType.SELECT,
             "options": "Cámara refrigerada 1|Cámara refrigerada 2|Nevera cocina|Vitrina|Abatidor"},
            {"key": "temperatura", "label": "Temperatura", "type": FieldType.NUMBER, "unit": "°C",
             "min_value": -2, "max_value": 5,
             "help_text": "Fuera de rango genera alerta crítica y exige acción correctiva."},
            {"key": "accion_correctiva", "label": "Acción correctiva si está fuera de rango",
             "type": FieldType.TEXT, "required": False},
        ],
    },
    {
        "code": "temp_congelacion", "name": "Temperatura de congelación", "category": "haccp",
        "description": "Congeladores y arcones. Límite legal habitual: hasta -18 °C.",
        "frequency": "shift", "expected_per_day": 2, "requires_photo": False, "sort_order": 11,
        "fields": [
            {"key": "unidad", "label": "Equipo", "type": FieldType.SELECT,
             "options": "Congelador 1|Congelador 2|Arcón|Cámara de congelación"},
            {"key": "temperatura", "label": "Temperatura", "type": FieldType.NUMBER, "unit": "°C",
             "min_value": -40, "max_value": -18,
             "help_text": "Por encima de -18 °C se rompe la cadena de frío: alerta crítica."},
            {"key": "accion_correctiva", "label": "Acción correctiva si está fuera de rango",
             "type": FieldType.TEXT, "required": False},
        ],
    },
    {
        "code": "recepcion", "name": "Recepción de mercancía", "category": "reception",
        "description": "Entrada de producto: proveedor, lote, caducidad y temperatura de llegada.",
        "frequency": "adhoc", "expected_per_day": 1, "requires_photo": True, "sort_order": 20,
        "fields": [
            {"key": "proveedor", "label": "Proveedor", "type": FieldType.TEXT},
            {"key": "producto", "label": "Producto", "type": FieldType.TEXT},
            {"key": "lote", "label": "Lote", "type": FieldType.TEXT, "required": False},
            {"key": "cantidad", "label": "Cantidad", "type": FieldType.NUMBER, "unit": "kg"},
            {"key": "caducidad", "label": "Fecha de caducidad", "type": FieldType.DATE,
             "expiry_alert_days": 3, "help_text": "Base del control FEFO: se consume primero lo que antes caduca."},
            {"key": "temperatura_llegada", "label": "Temperatura de llegada", "type": FieldType.NUMBER,
             "unit": "°C", "min_value": -30, "max_value": 5, "required": False},
            {"key": "conforme", "label": "Mercancía conforme", "type": FieldType.BOOL},
        ],
    },
    {
        "code": "merma", "name": "Merma y desperdicio", "category": "waste",
        "description": "Producto desechado, con motivo. Base del control de coste.",
        "frequency": "daily", "expected_per_day": 1, "requires_photo": True, "sort_order": 30,
        "fields": [
            {"key": "producto", "label": "Producto", "type": FieldType.TEXT},
            {"key": "cantidad", "label": "Cantidad", "type": FieldType.NUMBER, "unit": "kg"},
            {"key": "motivo", "label": "Motivo", "type": FieldType.SELECT,
             "options": "Caducado|Mal estado|Error de elaboración|Devolución de cliente|Rotura|Otro"},
            {"key": "area", "label": "Área", "type": FieldType.SELECT,
             "options": "Cocina caliente|Cocina fría|Pastelería|Bar|Almacén|Sala"},
        ],
    },
    {
        "code": "produccion", "name": "Producción / mise en place", "category": "production",
        "description": "Elaboraciones del turno con su fecha de consumo preferente.",
        "frequency": "daily", "expected_per_day": 1, "requires_photo": False, "sort_order": 40,
        "fields": [
            {"key": "elaboracion", "label": "Elaboración", "type": FieldType.TEXT},
            {"key": "cantidad", "label": "Cantidad", "type": FieldType.NUMBER, "unit": "kg"},
            {"key": "consumir_antes_de", "label": "Consumir antes de", "type": FieldType.DATE,
             "expiry_alert_days": 2},
            {"key": "responsable", "label": "Responsable", "type": FieldType.TEXT, "required": False},
        ],
    },
    {
        "code": "limpieza", "name": "Limpieza y desinfección", "category": "cleaning",
        "description": "Plan de limpieza firmado por turno.",
        "frequency": "shift", "expected_per_day": 2, "requires_photo": False, "sort_order": 50,
        "fields": [
            {"key": "zona", "label": "Zona", "type": FieldType.SELECT,
             "options": "Cocina|Cámaras|Almacén|Baños|Sala|Zona de lavado"},
            {"key": "realizada", "label": "Limpieza realizada", "type": FieldType.BOOL},
            {"key": "producto_usado", "label": "Producto usado", "type": FieldType.TEXT, "required": False},
            {"key": "incidencias", "label": "Incidencias", "type": FieldType.TEXT, "required": False},
        ],
    },
    {
        "code": "inventario", "name": "Conteo de inventario", "category": "count",
        "description": "Conteo físico que re-ancla el stock.",
        "frequency": "weekly", "expected_per_day": 1, "requires_photo": False, "sort_order": 60,
        "fields": [
            {"key": "articulo", "label": "Artículo", "type": FieldType.TEXT},
            {"key": "cantidad", "label": "Cantidad contada", "type": FieldType.NUMBER, "unit": "kg"},
            {"key": "ubicacion", "label": "Ubicación", "type": FieldType.TEXT, "required": False},
        ],
    },
]


def seed_templates(session: Session, restaurant_id: int) -> list[RecordTemplate]:
    """Crea las plantillas por defecto. Idempotente: no duplica por código."""
    created = []
    existing = {t.code for t in session.query(RecordTemplate).filter_by(restaurant_id=restaurant_id)}
    for spec in DEFAULT_TEMPLATES:
        if spec["code"] in existing:
            continue
        tpl = RecordTemplate(restaurant_id=restaurant_id,
                             **{k: v for k, v in spec.items() if k != "fields"})
        for i, f in enumerate(spec.get("fields", [])):
            tpl.fields.append(TemplateField(sort_order=i * 10, **f))
        session.add(tpl)
        created.append(tpl)
    session.flush()
    return created
