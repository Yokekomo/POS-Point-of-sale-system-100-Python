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
                "received_kg", "site_id"],
    "ingredient_lots": ["frozen", "site_id"],
    "ingredients": ["sold_by_weight"],
    "recipes": ["by_weight", "price_per_kg"],
    "recipe_lines": ["by_weight"],
    "users": ["totp_secret", "totp_enabled", "recovery_codes", "site_id"],
    "auth_sessions": ["pending_2fa"],
    "sales_by_product": ["kg"],
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
