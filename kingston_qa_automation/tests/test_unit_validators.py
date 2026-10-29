"""Offline unit tests for the server comparison and derivation rules (no browser).

Usage   : python -m pytest tests/test_unit_validators.py
"""
from kingston_qa.comparators import compare_spec, compare_text, parse_json
from kingston_qa.models import CsvRow, PageSnapshot, Status
from kingston_qa.validators import build_spec, check_store, derive_model, derive_type, validate_page_fields

NR = Status.NEEDS_REVIEW


def test_derive_model_and_type():
    assert derive_model("Acer - Aspire T3-715") == "Aspire T3-715"
    assert derive_model("ABIT - AN9 32X Motherboard") == "AN9 32X Motherboard"
    assert derive_type("ABIT - AN9 32X Motherboard") == "Motherboard"
    assert derive_type("HP - ProLiant BL460c Blade Server") == "Blade Server"
    assert derive_type("Acer - Aspire T3-715") == ""


def test_compare_text_whitespace_only():
    assert compare_text("a b", " a   b ", NR)[0] is Status.PASS
    assert compare_text("Acer- X", "Acer - X", NR)[0] is NR
    assert compare_text("Acer X", "Dell X", NR)[0] is Status.FAIL
    assert compare_text("x", None, NR)[0] is Status.FAIL


def test_parse_json():
    assert parse_json("nan", list) == ([], "")
    assert parse_json('["a"]', list) == (["a"], "")
    assert parse_json("{bad", dict)[0] is None


def test_compare_spec_ignores_order_case_whitespace():
    exp = {"Memory": {"Standard": ["8 GB"], "Maximum": ["64 GB"]}, "Expansions": {"Details": ["2 Socket(s)"]}}
    act = {"EXPANSIONS": {"Details": ["2  Socket(s) "]}, "Memory": {"Maximum": ["64 GB"], "Standard": ["8 GB"]}}
    assert compare_spec(exp, act, NR) == (Status.PASS, [])


def test_compare_spec_reports_each_difference():
    exp = {"Memory": {"Maximum": ["64 GB"]}, "Storage": {"Bus": ["x"]}}
    act = {"Memory": {"Maximum": ["32 GB"]}, "Notes": {"Details": ["y"]}}
    status, issues = compare_spec(exp, act, NR)
    assert status is Status.FAIL and len(issues) == 3


def test_full_row_against_snapshot():
    snap = PageSnapshot(
        url="https://www.kingston.com/en/memory/search/model/1/x",
        description="Acer - Aspire X",
        cards=[{"heading": "Expansions", "items": [{"label": None, "value": "2 Socket(s)"}]},
               {"heading": "CPU / Chipsets", "items": [{"label": None, "value": "Intel i5"}]},
               {"heading": "System Part Numbers", "items": []}])
    row = CsvRow(1, {"store": "Kingston", "server_description": "Acer - Aspire X", "host_url": snap.url + "/",
                     "server_model": "Aspire X", "server_type": "", "processor": '["Intel i5"]',
                     "processor_sockets": '["2 Socket(s)"]',
                     "server_specification": '{"CPU / Chipsets": {"Details": ["Intel i5"]}, "Expansions": {"Details": ["2 Socket(s)"]}}'})
    assert build_spec(snap) == {"Expansions": {"Details": ["2 Socket(s)"]}, "CPU / Chipsets": {"Details": ["Intel i5"]}}
    assert check_store(row).status is Status.PASS
    assert {c.field: c.status for c in validate_page_fields(row, snap, NR)} == {
        f: Status.PASS for f in ("server_description", "host_url", "server_model", "server_type",
                                 "processor", "processor_sockets", "server_specification")}


def test_norm_url_ignores_site_added_status_param():
    from kingston_qa.validators import norm_url
    assert norm_url("https://www.kingston.com/en/x?status=active") == norm_url("https://WWW.kingston.com/en/x/")
    assert norm_url("https://www.kingston.com/en/x?id=2") != norm_url("https://www.kingston.com/en/x")


def test_match_result_classification():
    from kingston_qa.models import FieldCheck, Match
    assert FieldCheck("f", Status.PASS, "a", "a").match is Match.MATCHED
    assert FieldCheck("f", Status.FAIL, "a", "b").match is Match.MISMATCHED
    assert FieldCheck("f", NR, "Acer- X", "Acer - X").match is Match.MISMATCHED
    assert FieldCheck("f", Status.FAIL, ["x"], []).match is Match.UNMATCHED
    assert FieldCheck("f", Status.BLOCKED, "a", "").match is Match.UNMATCHED
    assert FieldCheck("f", Status.SKIPPED, "43", "").match is Match.UNMATCHED


def test_description_whitespace_only_passes_but_text_change_fails():
    from kingston_qa.validators import check_description
    snap = PageSnapshot(url="u", description="Acer - Aspire X", cards=[])
    ok = check_description(CsvRow(1, {"server_description": "Acer- Aspire X"}), snap)
    assert ok.status is Status.PASS and "whitespace" in ok.reason
    bad = check_description(CsvRow(1, {"server_description": "Acer- Aspire Y"}), snap)
    assert bad.status is Status.FAIL
