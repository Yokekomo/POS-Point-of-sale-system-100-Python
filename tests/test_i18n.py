"""Idiomas: catálogos completos, elección y textos que se guardan."""
import pytest

from thegrill import db
from thegrill.models import RecordTemplate, Restaurant
from thegrill.web import auth, i18n, service
from thegrill.web.seed import DEFAULT_TEMPLATES, seed_templates


# ------------------------------------------------------- calidad del catálogo
def test_every_language_translates_every_key():
    """Ningún idioma se queda a medias: si falta una clave, este test la nombra."""
    reference = set(i18n.ES)
    for code, table in i18n.TRANSLATIONS.items():
        assert set(table) == reference, (
            f"{code}: faltan {sorted(reference - set(table))}, sobran {sorted(set(table) - reference)}")


def test_every_declared_language_has_a_catalogue():
    assert set(i18n.LANGUAGES) == set(i18n.TRANSLATIONS)
    assert i18n.DEFAULT_LANG in i18n.LANGUAGES


def test_no_translation_is_left_empty():
    for code, table in i18n.TRANSLATIONS.items():
        blanks = [k for k, v in table.items() if not v.strip()]
        assert blanks == [], f"{code}: vacías {blanks}"


def test_placeholders_match_the_reference():
    """Un texto traducido no puede perder ni inventar un hueco como {label}."""
    import re
    holes = lambda s: set(re.findall(r"\{(\w+)\}", s))
    for code, table in i18n.TRANSLATIONS.items():
        for key, spanish in i18n.ES.items():
            assert holes(table[key]) == holes(spanish), f"{code} / {key}"


def test_seed_keys_exist_in_the_catalogue():
    """Las plantillas iniciales solo referencian claves que existen."""
    used = set()
    for spec in DEFAULT_TEMPLATES:
        used.update(v for k, v in spec.items() if k.endswith("_key"))
        for field in spec.get("fields", []):
            used.update(v for k, v in field.items() if k.endswith("_key"))
    assert used and used <= set(i18n.ES), sorted(used - set(i18n.ES))


# ------------------------------------------------------------- resolución
def test_accept_language_header():
    assert i18n.from_accept_language("de-DE,de;q=0.9,en;q=0.8") == "de"
    assert i18n.from_accept_language("en-GB;q=0.5,nl;q=0.9") == "nl"      # gana la q más alta
    assert i18n.from_accept_language("en-GB,nl") == "en"                  # a igualdad, el primero
    assert i18n.from_accept_language("ja,ko") is None
    assert i18n.from_accept_language(None) is None
    assert i18n.from_accept_language("") is None


def test_resolution_order():
    assert i18n.resolve(user_lang="de", cookie="fr", accept_header="ar", restaurant_lang="nl") == "de"
    assert i18n.resolve(cookie="fr", accept_header="ar", restaurant_lang="nl") == "fr"
    assert i18n.resolve(accept_header="ar", restaurant_lang="nl") == "ar"
    assert i18n.resolve(restaurant_lang="nl") == "nl"
    assert i18n.resolve() == "es"


def test_unknown_language_is_ignored_at_every_step():
    assert i18n.resolve(user_lang="klingon", cookie="xx", restaurant_lang="de") == "de"
    assert not i18n.is_supported("pt")
    assert i18n.t("klingon", "nav.panel") == i18n.t("es", "nav.panel")   # cae al español


def test_unknown_key_returns_itself_instead_of_breaking():
    assert i18n.t("en", "clave.que.no.existe") == "clave.que.no.existe"


def test_arabic_is_right_to_left_and_the_rest_are_not():
    assert i18n.direction("ar") == "rtl"
    assert {c for c in i18n.LANGUAGES if i18n.direction(c) == "rtl"} == {"ar"}


def test_interpolation_and_its_failure_mode():
    assert "9°C" in i18n.t("en", "alert.above_max", label="T", value="9", unit="°C", limit="5")
    # si falta un dato no revienta: devuelve el texto sin sustituir
    assert i18n.t("en", "alert.above_max", label="T") == i18n.EN["alert.above_max"]


# ------------------------------------------------------- datos en el idioma
@pytest.fixture
def session(tmp_path):
    db.init_engine(f"sqlite:///{tmp_path/'i.db'}")
    db.create_all()
    with db.session_scope() as s:
        yield s


@pytest.mark.parametrize("lang,expected", [
    ("es", "Temperatura de refrigeración"),
    ("en", "Refrigeration temperature"),
    ("fr", "Température de réfrigération"),
    ("de", "Kühltemperatur"),
    ("nl", "Koeltemperatuur"),
    ("ar", "درجة حرارة التبريد"),
])
def test_default_forms_are_created_in_the_chosen_language(session, lang, expected):
    rest, _ = auth.create_restaurant(session, f"R{lang}", f"a@{lang}.com", "A", "clave-larga-1",
                                     language=lang)
    tpls = seed_templates(session, rest.id, lang)
    assert rest.language == lang
    fridge = next(x for x in tpls if x.code == "temp_refrigeracion")
    assert fridge.name == expected
    assert fridge.fields[0].options and "|" in fridge.fields[0].options
    assert all(f.label for f in fridge.fields)


def test_validation_speaks_to_the_person_and_alerts_speak_to_the_team(session):
    """El empleado lee el error en su idioma; la alerta se guarda en el del local."""
    rest, manager = auth.create_restaurant(session, "Casa", "ana@casa.com", "Ana",
                                           "clave-larga-1", language="es")
    seed_templates(session, rest.id, "es")
    luis = auth.join_restaurant(session, rest.join_code, "luis@casa.com", "Luis",
                                "clave-larga-2", language="en")
    fridge = session.query(RecordTemplate).filter_by(restaurant_id=rest.id,
                                                     code="temp_refrigeracion").one()

    with pytest.raises(service.ValidationError) as e:
        service.submit_record(session, luis, fridge, {})
    assert "is required" in str(e.value)               # inglés, el idioma de Luis

    result = service.submit_record(session, luis, fridge,
                                   {"unidad": "Vitrina", "temperatura": "11"})
    assert "por encima del máximo" in result.alerts[0].message      # español, el del local
    assert "registrado por Luis" in result.notifications[0].body


def test_a_restaurant_in_german_stores_its_alerts_in_german(session):
    rest, manager = auth.create_restaurant(session, "Haus", "ana@haus.de", "Ana",
                                           "clave-larga-1", language="de")
    seed_templates(session, rest.id, "de")
    hans = auth.join_restaurant(session, rest.join_code, "hans@haus.de", "Hans", "clave-larga-2")
    fridge = session.query(RecordTemplate).filter_by(restaurant_id=rest.id,
                                                     code="temp_refrigeracion").one()
    result = service.submit_record(session, hans, fridge, {"unidad": "Vitrine", "temperatura": "11"})
    assert "über dem Maximum" in result.alerts[0].message
    assert "erfasst von Hans" in result.notifications[0].body


def test_a_user_without_a_language_follows_the_restaurant(session):
    rest, _ = auth.create_restaurant(session, "Huis", "ana@huis.nl", "Ana", "clave-larga-1",
                                     language="nl")
    piet = auth.join_restaurant(session, rest.join_code, "piet@huis.nl", "Piet", "clave-larga-2")
    assert piet.language is None
    assert i18n.resolve(user_lang=piet.language, restaurant_lang=rest.language) == "nl"
