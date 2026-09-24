"""[01368] Plantillas de registro por defecto, válidas para cualquier restaurante.

Un restaurante nuevo arranca con estas y el manager las edita, desactiva o
añade las suyas. Nada aquí es específico de una cocina concreta.

Los textos se materializan en el idioma del restaurante al darlo de alta: a
partir de ahí son datos suyos, que puede reescribir como quiera.

Refrigeración y congelación van en plantillas separadas a propósito: cada una
tiene su propio límite legal, y un solo campo numérico no puede validar los dos.
"""
from sqlalchemy.orm import Session

from thegrill.models import FieldType, RecordTemplate, TemplateField
from thegrill.web.i18n import DEFAULT_LANG, t

DEFAULT_TEMPLATES = [
    {
        "code": "temp_refrigeracion", "name_key": "seed.fridge.name", "category": "haccp",
        "desc_key": "seed.fridge.desc",
        "frequency": "shift", "expected_per_day": 2, "requires_photo": False, "sort_order": 10,
        "fields": [
            {"key": "unidad", "label_key": "seed.fridge.unit", "type": FieldType.SELECT,
             "options_key": "seed.fridge.unit_options"},
            {"key": "temperatura", "label_key": "seed.temp", "type": FieldType.NUMBER, "unit": "°C",
             "min_value": -2, "max_value": 5,
             "help_key": "seed.fridge.temp_help"},
            {"key": "accion_correctiva", "label_key": "seed.corrective",
             "type": FieldType.TEXT, "required": False},
        ],
    },
    {
        "code": "temp_congelacion", "name_key": "seed.freezer.name", "category": "haccp",
        "desc_key": "seed.freezer.desc",
        "frequency": "shift", "expected_per_day": 2, "requires_photo": False, "sort_order": 11,
        "fields": [
            {"key": "unidad", "label_key": "seed.fridge.unit", "type": FieldType.SELECT,
             "options_key": "seed.freezer.unit_options"},
            {"key": "temperatura", "label_key": "seed.temp", "type": FieldType.NUMBER, "unit": "°C",
             "min_value": -40, "max_value": -18,
             "help_key": "seed.freezer.temp_help"},
            {"key": "accion_correctiva", "label_key": "seed.corrective",
             "type": FieldType.TEXT, "required": False},
        ],
    },
    {
        "code": "recepcion", "name_key": "seed.reception.name", "category": "reception",
        "desc_key": "seed.reception.desc",
        "frequency": "adhoc", "expected_per_day": 1, "requires_photo": True, "sort_order": 20,
        "fields": [
            {"key": "proveedor", "label_key": "seed.supplier", "type": FieldType.TEXT},
            {"key": "producto", "label_key": "seed.product", "type": FieldType.TEXT},
            {"key": "lote", "label_key": "seed.lot", "type": FieldType.TEXT, "required": False},
            {"key": "cantidad", "label_key": "seed.quantity", "type": FieldType.NUMBER, "unit": "kg"},
            {"key": "caducidad", "label_key": "seed.expiry", "type": FieldType.DATE,
             "expiry_alert_days": 3, "help_key": "seed.expiry_help"},
            {"key": "temperatura_llegada", "label_key": "seed.arrival_temp", "type": FieldType.NUMBER,
             "unit": "°C", "min_value": -30, "max_value": 5, "required": False},
            {"key": "conforme", "label_key": "seed.conform", "type": FieldType.BOOL},
        ],
    },
    {
        "code": "merma", "name_key": "seed.waste.name", "category": "waste",
        "desc_key": "seed.waste.desc",
        "frequency": "daily", "expected_per_day": 1, "requires_photo": True, "sort_order": 30,
        "fields": [
            {"key": "producto", "label_key": "seed.product", "type": FieldType.TEXT},
            {"key": "cantidad", "label_key": "seed.quantity", "type": FieldType.NUMBER, "unit": "kg"},
            {"key": "motivo", "label_key": "seed.reason", "type": FieldType.SELECT,
             "options_key": "seed.waste.reason_options"},
            {"key": "area", "label_key": "seed.area", "type": FieldType.SELECT,
             "options_key": "seed.waste.area_options"},
        ],
    },
    {
        "code": "produccion", "name_key": "seed.production.name", "category": "production",
        "desc_key": "seed.production.desc",
        "frequency": "daily", "expected_per_day": 1, "requires_photo": False, "sort_order": 40,
        "fields": [
            {"key": "elaboracion", "label_key": "seed.preparation", "type": FieldType.TEXT},
            {"key": "cantidad", "label_key": "seed.quantity", "type": FieldType.NUMBER, "unit": "kg"},
            {"key": "consumir_antes_de", "label_key": "seed.use_by", "type": FieldType.DATE,
             "expiry_alert_days": 2},
            {"key": "responsable", "label_key": "seed.responsible", "type": FieldType.TEXT, "required": False},
        ],
    },
    {
        "code": "limpieza", "name_key": "seed.cleaning.name", "category": "cleaning",
        "desc_key": "seed.cleaning.desc",
        "frequency": "shift", "expected_per_day": 2, "requires_photo": False, "sort_order": 50,
        "fields": [
            {"key": "zona", "label_key": "seed.zone", "type": FieldType.SELECT,
             "options_key": "seed.cleaning.zone_options"},
            {"key": "realizada", "label_key": "seed.cleaning.done", "type": FieldType.BOOL},
            {"key": "producto_usado", "label_key": "seed.cleaning.product", "type": FieldType.TEXT, "required": False},
            {"key": "incidencias", "label_key": "seed.incidents", "type": FieldType.TEXT, "required": False},
        ],
    },
    {
        "code": "inventario", "name_key": "seed.count.name", "category": "count",
        "desc_key": "seed.count.desc",
        "frequency": "weekly", "expected_per_day": 1, "requires_photo": False, "sort_order": 60,
        "fields": [
            {"key": "articulo", "label_key": "seed.item", "type": FieldType.TEXT},
            {"key": "cantidad", "label_key": "seed.counted", "type": FieldType.NUMBER, "unit": "kg"},
            {"key": "ubicacion", "label_key": "seed.location", "type": FieldType.TEXT, "required": False},
        ],
    },
]


TEMPLATE_KEYS = {"name_key": "name", "desc_key": "description"}
FIELD_KEYS = {"label_key": "label", "options_key": "options", "help_key": "help_text"}


def _materialize(spec: dict, mapping: dict[str, str], lang: str) -> dict:
    """[01369] Convierte las claves de traducción en el texto del idioma pedido."""
    out = {k: v for k, v in spec.items() if k not in mapping and k != "fields"}
    for key_field, target in mapping.items():
        if key_field in spec:
            out[target] = t(lang, spec[key_field])
    return out


def seed_templates(session: Session, restaurant_id: int,
                   lang: str = DEFAULT_LANG) -> list[RecordTemplate]:
    """[01370] Crea las plantillas por defecto en `lang`. Idempotente: no duplica por código."""
    created = []
    existing = {row.code for row in
                session.query(RecordTemplate).filter_by(restaurant_id=restaurant_id)}
    for spec in DEFAULT_TEMPLATES:
        if spec["code"] in existing:
            continue
        tpl = RecordTemplate(restaurant_id=restaurant_id, **_materialize(spec, TEMPLATE_KEYS, lang))
        for i, f in enumerate(spec.get("fields", [])):
            tpl.fields.append(TemplateField(sort_order=i * 10, **_materialize(f, FIELD_KEYS, lang)))
        session.add(tpl)
        created.append(tpl)
    session.flush()
    return created
