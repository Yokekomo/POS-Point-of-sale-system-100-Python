"""El esquema aguanta el modelo del negocio y aísla cada restaurante."""
from datetime import date

import pytest
from sqlalchemy.exc import IntegrityError

from thegrill import db
from thegrill.models import (Despiece, DespieceCut, DespiecePrimal, Primal,
                             PrimalStatus, Restaurant)


@pytest.fixture
def two_restaurants(tmp_path):
    db.init_engine(f"sqlite:///{tmp_path/'m.db'}")
    db.create_all()
    with db.session_scope() as s:
        s.add_all([Restaurant(id=1, name="The Grill", slug="the-grill", join_code="AAAA1111"),
                   Restaurant(id=2, name="Casa Pepe", slug="casa-pepe", join_code="BBBB2222")])
    return 1, 2


def test_despiece_keeps_serials_and_cut_detail(two_restaurants):
    with db.session_scope() as s:
        s.add(Primal(restaurant_id=1, serial="8017", sku="RIB_EYE_USA_PRIME", weight_kg=7.2,
                     lot="DXB20260828", label_product="Rib Eye USA Prime", origin="USA"))
        tg = Despiece(restaurant_id=1, tg="TG-0042", date=date(2026, 9, 10), weight_before_kg=7.2,
                      total_cuts_kg=5.8, waste_kg=0.9, trim_kg=0.5)
        tg.primals.append(DespiecePrimal(serial="8017", label_kg=7.2))
        tg.cuts.append(DespieceCut(cut_name="Rib eye", pieces=14, weight_per_piece_g=400, total_kg=5.6))
        s.add(tg)
    with db.session_scope() as s:
        primal = s.query(Primal).filter_by(restaurant_id=1, serial="8017").one()
        assert primal.status == PrimalStatus.IN_STOCK
        tg = s.query(Despiece).filter_by(restaurant_id=1, tg="TG-0042").one()
        assert [c.pieces for c in tg.cuts] == [14] and [p.serial for p in tg.primals] == ["8017"]


def test_same_serial_allowed_in_another_restaurant_but_not_twice_in_one(two_restaurants):
    with db.session_scope() as s:
        s.add_all([Primal(restaurant_id=1, serial="8017", sku="X", weight_kg=1.0),
                   Primal(restaurant_id=2, serial="8017", sku="X", weight_kg=1.0)])
    with db.session_scope() as s:
        assert s.query(Primal).filter_by(serial="8017").count() == 2
    with pytest.raises(IntegrityError):
        with db.session_scope() as s:
            s.add(Primal(restaurant_id=1, serial="8017", sku="X", weight_kg=1.0))


def test_a_despiece_cannot_consume_the_same_serial_twice(two_restaurants):
    with db.session_scope() as s:
        tg = Despiece(restaurant_id=1, tg="TG-1", date=date(2026, 9, 10), weight_before_kg=5.0)
        tg.primals.append(DespiecePrimal(serial="9001"))
        s.add(tg)
    with pytest.raises(IntegrityError):
        with db.session_scope() as s:
            tg = s.query(Despiece).filter_by(restaurant_id=1, tg="TG-1").one()
            s.add(DespiecePrimal(despiece_id=tg.id, serial="9001"))
