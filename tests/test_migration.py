"""Que una casa que ya está en marcha no reviente al actualizar.

`create_all` crea tablas nuevas, pero no toca las que ya están: una base de
datos con meses de trabajo dentro se quedaba sin las columnas que se añaden
después y reventaba al leer. Por eso `add_missing_columns` las añade sola al
arrancar, y por eso esto se prueba: es el camino por el que pasa cada
actualización de cada cliente, y no se ve hasta que falla.
"""
from datetime import date

import pytest
from sqlalchemy import text

from thegrill import db
from thegrill.models import Primal, PrimalStatus, Restaurant, Storage, User
from thegrill.web import auth

HOY = date.today()

# Columnas añadidas después de que la primera casa estuviera funcionando. Si
# una de estas no vuelve sola, al cliente le falla el programa al abrirlo.
NUEVAS = {
    "primals": ["storage", "storage_since", "aging_start_kg", "aging_target_days",
                "received_kg", "site_id", "chamber"],
    "ingredient_lots": ["frozen", "site_id", "chamber"],
    "ingredients": ["sold_by_weight"],
    "recipes": ["by_weight", "price_per_kg"],
    "recipe_lines": ["by_weight"],
    "users": ["totp_secret", "totp_enabled", "recovery_codes", "site_id"],
    "auth_sessions": ["pending_2fa"],
    "sales_by_product": ["kg"],
    "meat_counts": ["site_id", "open_key"],
    "meat_count_lines": ["counted_by", "counted_at", "disputed"],
    "shift_closures": ["site_key"],
    "bug_reports": ["note", "detail"],
    "despiece_cuts": ["by_weight"],
}


def indices(tabla: str) -> set[str]:
    with db.session_scope() as s:
        filas = s.execute(text(f"PRAGMA index_list({tabla})")).all()
    return {f[1] for f in filas}


def columnas(tabla: str) -> set[str]:
    with db.session_scope() as s:
        filas = s.execute(text(f"PRAGMA table_info({tabla})")).all()
    return {f[1] for f in filas}


def test_the_new_columns_come_back_on_their_own(tmp_path):
    """Se le quitan a una base en marcha, y al arrancar vuelven."""
    db.init_engine(f"sqlite:///{tmp_path/'vieja.db'}")
    db.create_all()
    with db.session_scope() as s:
        rest, ana = auth.create_restaurant(s, "Asador", "ana@a.com", "Ana",
                                           "clave-larga-1", language="es")
        s.add(Primal(restaurant_id=rest.id, serial="8017", sku="Ribeye", weight_kg=9.0,
                     received_kg=9.0, landed_usd_per_kg=30.0, piece_cost_usd=270.0,
                     received_date=HOY, storage=Storage.AGING))
        s.flush()

    # La base de datos de antes: sin esas columnas ni sus índices.
    with db.session_scope() as s:
        for tabla, nombres in NUEVAS.items():
            for nombre in nombres:
                s.execute(text(f'DROP INDEX IF EXISTS "ix_{tabla}_{nombre}"'))
                # Y las reglas que se apoyan en esa columna: SQLite no deja
                # quitar una columna que alguien está mirando.
                for indice in indices(tabla):
                    if not indice.startswith("sqlite_"):
                        s.execute(text(f'DROP INDEX IF EXISTS "{indice}"'))
                s.execute(text(f'ALTER TABLE "{tabla}" DROP COLUMN "{nombre}"'))
    for tabla, nombres in NUEVAS.items():
        assert not (set(nombres) & columnas(tabla)), tabla

    añadidas = db.add_missing_columns()

    for tabla, nombres in NUEVAS.items():
        faltan = set(nombres) - columnas(tabla)
        assert not faltan, f"{tabla}: {faltan}"
    assert any("primals.storage" == c for c in añadidas)
    # Y con la columna vuelve su índice: misma forma y mismo rendimiento.
    assert "ix_primals_site_id" in añadidas
    assert "ix_primals_site_id" in indices("primals")

    # Y lo que había dentro sigue ahí, con las columnas nuevas vacías.
    with db.session_scope() as s:
        pieza = s.query(Primal).one()
        assert pieza.serial == "8017" and pieza.weight_kg == 9.0
        assert pieza.storage is None                 # vacía, no inventada
        assert s.query(User).count() == 1


def test_running_it_twice_changes_nothing(tmp_path):
    """Arrancar el programa dos veces no puede duplicar ni una columna."""
    db.init_engine(f"sqlite:///{tmp_path/'dos.db'}")
    db.create_all()
    assert db.add_missing_columns() == []
    db.create_all()
    assert db.add_missing_columns() == []


def test_an_old_database_still_works_after_upgrading(tmp_path):
    """La prueba de verdad: quitar columnas, actualizar y seguir trabajando."""
    db.init_engine(f"sqlite:///{tmp_path/'trabajo.db'}")
    db.create_all()
    with db.session_scope() as s:
        rest, ana = auth.create_restaurant(s, "Asador", "ana@a.com", "Ana",
                                           "clave-larga-1", language="es")
        s.add(Primal(restaurant_id=rest.id, serial="8017", sku="Ribeye", weight_kg=9.0,
                     landed_usd_per_kg=30.0, piece_cost_usd=270.0, received_date=HOY))
        s.flush()
    with db.session_scope() as s:
        s.execute(text('ALTER TABLE "primals" DROP COLUMN "storage"'))
        s.execute(text('ALTER TABLE "primals" DROP COLUMN "received_kg"'))

    db.create_all()          # esto es lo que hace el programa al arrancar

    from thegrill.web import aging
    with db.session_scope() as s:
        ana = s.query(User).one()
        movida = aging.move(s, ana, "8017", Storage.AGING, target_days=45, on=HOY)
        assert movida.now == Storage.AGING
        pesada = aging.weigh(s, ana, "8017", 8.2, on=HOY)
        assert pesada.loss_kg == 0.8
        assert aging.board(s, ana.restaurant_id, on=HOY)[0].serial == "8017"


def test_a_database_from_before_the_sites_gets_them_and_keeps_working(tmp_path):
    """Las sedes son tablas nuevas: al actualizar aparecen y la carne sigue en su sitio.

    Una casa en marcha no tenía ni obrador ni locales. Después de actualizar
    tiene una sede —la principal—, y las piezas que ya estaban dentro están en
    ella: nadie tiene que configurar nada para que el día de mañana sea igual
    que el de ayer.
    """
    from thegrill.models import Site
    from thegrill.web import sites

    db.init_engine(f"sqlite:///{tmp_path/'sinsedes.db'}")
    db.create_all()
    with db.session_scope() as s:
        rest, ana = auth.create_restaurant(s, "Asador", "ana@a.com", "Ana",
                                           "clave-larga-1", language="es")
        s.add(Primal(restaurant_id=rest.id, serial="8017", sku="Ribeye", weight_kg=9.0,
                     landed_usd_per_kg=30.0, piece_cost_usd=270.0, received_date=HOY))
        s.flush()
    with db.session_scope() as s:
        s.execute(text('DROP TABLE "transfers"'))
        s.execute(text('DROP TABLE "sites"'))
        s.execute(text('DROP INDEX IF EXISTS "ix_primals_site_id"'))
        s.execute(text('ALTER TABLE "primals" DROP COLUMN "site_id"'))

    db.create_all()          # esto es lo que hace el programa al arrancar

    with db.session_scope() as s:
        ana = s.query(User).one()
        pieza = s.query(Primal).one()
        assert pieza.site_id is None                  # vacía, no inventada
        principal = sites.main(s, ana.restaurant_id)
        assert sites.where(s, ana.restaurant_id, pieza).id == principal.id
        assert s.query(Site).count() == 1
        fila = [f for f in sites.stock(s, ana.restaurant_id) if f.site.id == principal.id][0]
        assert fila.primals == 1 and fila.primal_kg == 9.0


def test_a_database_from_before_two_people_at_once_gets_the_rule(tmp_path):
    """La regla de «un turno, un cuadre» tiene que llegar a las casas de antes.

    En una base de datos que ya venía funcionando, la columna nueva se añade
    sola; lo que hay que comprobar es que detrás va su regla, porque si no, la
    casa vieja y la nueva tienen la misma forma y no las mismas defensas: dos
    personas cerrando el turno a la vez seguirían escribiendo dos cuadres.
    """
    db.init_engine(f"sqlite:///{tmp_path/'vieja.db'}")
    db.create_all()
    with db.session_scope() as s:
        s.execute(text('DROP INDEX IF EXISTS "uq_shift_closure"'))
        s.execute(text('ALTER TABLE "shift_closures" DROP COLUMN "site_key"'))
    assert "site_key" not in columnas("shift_closures")

    añadidas = db.add_missing_columns()

    assert "shift_closures.site_key" in añadidas
    assert "uq_shift_closure" in indices("shift_closures")


def test_a_brand_new_table_appears_by_itself_on_a_house_that_is_working(tmp_path):
    """El tutorial guarda quién lo ha visto en una tabla suya, que no existía.

    Una casa que lleva meses trabajando no tiene esa tabla. No hay que hacer
    nada: `create_all` —que es lo que corre al arrancar— la crea sola, y lo
    que ya había dentro se queda como estaba.
    """
    from thegrill.meat import tutorial
    from thegrill.models import TourVisto

    db.init_engine(f"sqlite:///{tmp_path/'sin_tabla.db'}")
    db.create_all()
    with db.session_scope() as s:
        rest, ana = auth.create_restaurant(s, "Asador", "ana@a.com", "Ana",
                                           "clave-larga-1", language="es")
        s.add(Primal(restaurant_id=rest.id, serial="8017", sku="Ribeye", weight_kg=9.0,
                     landed_usd_per_kg=30.0, piece_cost_usd=270.0, received_date=HOY))
        s.flush()
    with db.session_scope() as s:
        s.execute(text('DROP TABLE "tours_vistos"'))

    db.create_all()          # esto es lo que hace el programa al arrancar
    assert db.add_missing_columns() == []

    with db.session_scope() as s:
        persona = s.query(User).one()
        tutorial.marcar(s, persona, "recepcion")
        assert tutorial.visto(s, persona, "recepcion", 1)
        assert s.query(TourVisto).count() == 1
        assert s.query(Primal).one().serial == "8017"


# ==================== lo que la migración no hacía, y lo que ahora sí dice
#
# Hasta ahora solo volvían las columnas que admiten vacío. Una columna
# obligatoria, una regla de «no puede haber dos iguales» o un valor nuevo de
# una lista cerrada se quedaban fuera **sin decir una palabra**: el programa
# arrancaba, parecía que todo iba bien, y lo que faltaba se descubría semanas
# después con un error raro en una pantalla que no tenía nada que ver.
def _quitar(tabla: str, columna: str) -> None:
    with db.session_scope() as s:
        s.execute(text(f'ALTER TABLE {tabla} DROP COLUMN {columna}'))


def _quitar_indice(nombre: str) -> None:
    with db.session_scope() as s:
        s.execute(text(f'DROP INDEX IF EXISTS "{nombre}"'))


def _una_casa(tmp_path, nombre="vieja.db"):
    db.init_engine(f"sqlite:///{tmp_path/nombre}")
    db.create_all()
    with db.session_scope() as s:
        auth.create_restaurant(s, "Asador", "ana@a.com", "Ana", "clave-larga-1",
                               language="es")


def test_a_required_column_with_a_default_comes_back_filled(tmp_path):
    """`currency` es obligatoria y vale EUR: se puede poner, y se pone."""
    _una_casa(tmp_path)
    _quitar("restaurants", "currency")
    assert "restaurants.currency" not in columnas("restaurants")

    assert "restaurants.currency" in db.add_missing_columns()
    assert "currency" in columnas("restaurants")
    with db.session_scope() as s:
        # Y la casa que ya estaba no se queda con el hueco vacío.
        assert s.query(Restaurant).filter(
            Restaurant.platform.isnot(True)).one().currency == "EUR"


def test_a_required_column_with_no_default_is_not_hidden(tmp_path):
    """`name` no dice con qué rellenar: no se inventa, pero se avisa."""
    _una_casa(tmp_path)
    _quitar("restaurants", "name")
    db.add_missing_columns()
    assert "name" not in columnas("restaurants")
    faltan = {p.que for p in db.pendientes()}
    assert "restaurants.name" in faltan
    aviso = next(p for p in db.pendientes() if p.que == "restaurants.name")
    assert "obligatoria" in aviso.porque and aviso.mano        # y cómo arreglarlo


def test_an_empty_table_takes_the_column_anyway(tmp_path):
    """Sin filas no hay nada que rellenar: la columna entra igual."""
    db.init_engine(f"sqlite:///{tmp_path/'nueva.db'}")
    db.create_all()                      # base recién hecha: sin ninguna casa
    _quitar("restaurants", "name")
    assert "restaurants.name" in db.add_missing_columns()
    assert "name" in columnas("restaurants")
    assert db.pendientes() == []


def test_the_no_two_the_same_rule_comes_back_too(tmp_path):
    """Sin la regla, el mismo nombre de sede se puede repetir en una casa que
    viene de antes y no en una nueva. Y eso no se ve hasta que pasa."""
    _una_casa(tmp_path)
    with db.session_scope() as s:
        s.execute(text("DROP TABLE sites"))
        s.execute(text("CREATE TABLE sites (id INTEGER PRIMARY KEY AUTOINCREMENT, "
                       "restaurant_id INTEGER, name VARCHAR(96), kind VARCHAR(16), "
                       "address TEXT, active BOOLEAN, created_at DATETIME)"))
    assert "uq_site_restaurant_name" not in indices("sites")

    assert "uq_site_restaurant_name" in db.add_missing_columns()
    assert "uq_site_restaurant_name" in indices("sites")
    # Y una vez puesta, no se vuelve a poner en cada arranque.
    assert db.add_missing_columns() == []


def test_a_rule_that_the_data_already_breaks_is_reported_not_swallowed(tmp_path):
    """Si ya hay dos iguales, la regla no se puede poner: eso hay que decirlo,
    porque significa que algo se duplicó de verdad.

    La tabla se rehace a mano tal y como la tenía una casa de antes de que la
    regla existiera. Es la única manera de probarlo: en SQLite una regla que
    viene en el `CREATE TABLE` no se puede quitar después.
    """
    _una_casa(tmp_path)
    with db.session_scope() as s:
        rest_id = s.query(Restaurant).filter(
            Restaurant.platform.isnot(True)).one().id
        s.execute(text("DROP TABLE sites"))
        s.execute(text("CREATE TABLE sites (id INTEGER PRIMARY KEY AUTOINCREMENT, "
                       "restaurant_id INTEGER, name VARCHAR(96), kind VARCHAR(16), "
                       "address TEXT, active BOOLEAN, created_at DATETIME)"))
        for _ in range(2):
            s.execute(text(
                "INSERT INTO sites (restaurant_id, name, kind, active, created_at) "
                f"VALUES ({rest_id}, 'Playa', 'OUTLET', 1, '2026-09-24 10:00:00')"))

    db.add_missing_columns()
    aviso = next((p for p in db.pendientes()
                  if p.que == "uq_site_restaurant_name"), None)
    assert aviso is not None and "repetidas" in aviso.porque
    assert aviso.mano.startswith("CREATE UNIQUE INDEX")      # y cómo arreglarlo


def test_a_clean_update_has_nothing_to_report(tmp_path):
    _una_casa(tmp_path)
    assert db.add_missing_columns() == []
    assert db.pendientes() == []
