from datetime import date
import pytest

from thegrill import rules
from thegrill.models import SourceStatus


def test_label_wins_over_sheet_and_flags():
    r = rules.resolve_from_label("Striploin USA", "Rib Eye USA Prime")   # caso serial 8017
    assert r.value == "Rib Eye USA Prime" and r.flagged and "LABEL_MISMATCH" in r.note


def test_label_equal_no_flag():
    assert not rules.resolve_from_label("cube roll", "Cube  Roll").flagged


def test_no_label_no_sheet_raises():
    with pytest.raises(rules.RuleViolation):
        rules.resolve_from_label(None, None)


def test_tg_requires_serials_and_cut_detail():
    tg = rules.TGInput("TG-0001", "AUS", serials=[], cuts=[{"cut_name": "striploin", "pieces": 0, "weight_per_piece_g": 300}])
    codes = {i.code for i in rules.validate_tg(tg)}
    assert {"TG_NO_PRIMALS", "CUT_NO_PIECES"} <= codes


def test_imported_without_serial_is_phantom_but_local_is_ok():
    imp = rules.validate_tg(rules.TGInput("TG-1", "DXB", [None], [{"cut_name": "x", "pieces": 2, "weight_per_piece_g": 250}]))
    loc = rules.validate_tg(rules.TGInput("TG-2", "LOCAL", [None], [{"cut_name": "x", "pieces": 2, "weight_per_piece_g": 250}]))
    assert any(i.code == "PHANTOM_SERIAL" for i in imp)
    assert loc == []


def test_cut_only_with_evidence():
    assert rules.can_mark_cut("despiece") and rules.can_mark_cut("physical_count")
    assert not rules.can_mark_cut("inference")


def test_weekly_count_complete_and_stale():
    assert rules.weekly_count_is_complete(125)
    assert not rules.weekly_count_is_complete(90)
    assert rules.weekly_count_is_stale(date(2026, 9, 1), date(2026, 9, 10))
    assert not rules.weekly_count_is_stale(date(2026, 9, 8), date(2026, 9, 10))


def test_fish_excluded_from_meat_entry():
    assert rules.is_meat_entry("Chicken breast", "meat")
    assert not rules.is_meat_entry("Salmon fillet", "fish")
    assert not rules.is_meat_entry("Gambas", None)


def test_three_states_never_collapse():
    assert rules.classify_source(posted=False, readable=False) == SourceStatus.NOT_POSTED_YET
    assert rules.classify_source(posted=True, readable=False) == SourceStatus.BLOCKED
    assert rules.classify_source(posted=True, readable=True) == SourceStatus.DONE


def test_price_flags():
    assert rules.bill_price_flag(100, 115) and not rules.bill_price_flag(100, 114)
    assert rules.pos_price_flag(10, 10.8) and not rules.pos_price_flag(10, 10.7)


def test_haccp_limits_and_problem_extraction():
    assert rules.haccp_status("CHILLED", 5.0) == "OK"
    assert rules.haccp_status("CHILLED", 5.1) == "OUT_OF_RANGE"
    assert rules.haccp_status("FROZEN", -18) == "OK"
    assert rules.haccp_status("FROZEN", -17) == "OUT_OF_RANGE"
    checks = [
        {"unit": "WI-1", "kind": "CHILLED", "reading_c": 4.0, "section_b_ok": True},
        {"unit": "WI-2", "kind": "CHILLED", "reading_c": 7.0, "section_b_ok": True, "corrective_action": None},
        {"unit": "FZ-1", "kind": "FROZEN", "reading_c": -19, "section_b_ok": False, "corrective_action": "reset"},
    ]
    probs = rules.haccp_problems(checks)
    assert [p["unit"] for p in probs] == ["WI-2", "FZ-1"]
    assert probs[0]["reason"] == "SIN_ACCION_CORRECTIVA" and probs[1]["reason"] == "SECCION_B"


def test_bill_illegible_is_check_not_zero():
    assert rules.bill_status(None) == "CHECK" and rules.bill_status(0.0) == "OK"
