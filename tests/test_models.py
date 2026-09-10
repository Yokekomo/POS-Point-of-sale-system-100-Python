from datetime import date

import pytest
from sqlalchemy.exc import IntegrityError

from thegrill import db
from thegrill.models import Despiece, DespieceCut, DespiecePrimal, Primal, PrimalStatus


def test_schema_creates_and_serial_is_single_source_of_truth(tmp_path):
    db.init_engine(f"sqlite:///{tmp_path/'m.db'}")
    db.create_all()
    with db.session_scope() as s:
        s.add(Primal(serial="8017", sku="RIB_EYE_USA_PRIME", weight_kg=7.2, lot="DXB20260828",
                     label_product="Rib Eye USA Prime", origin="USA"))
        tg = Despiece(tg="TG-0042", date=date(2026, 9, 10), weight_before_kg=7.2, total_cuts_kg=5.8,
                      waste_kg=0.9, trim_kg=0.5)
        tg.primals.append(DespiecePrimal(serial="8017", label_kg=7.2))
        tg.cuts.append(DespieceCut(cut_name="Rib eye", pieces=14, weight_per_piece_g=400, total_kg=5.6))
        s.add(tg)
    with db.session_scope() as s:
        p = s.get(Primal, "8017")
        assert p.status == PrimalStatus.IN_STOCK
        assert [c.pieces for c in s.get(Despiece, "TG-0042").cuts] == [14]
    with pytest.raises(IntegrityError):
        with db.session_scope() as s:
            s.add(DespiecePrimal(tg="TG-0042", serial="8017"))   # mismo serial dos veces en el mismo TG
