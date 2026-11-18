"""
Regression tests for client review VP-17 (D06 validation results).

Each test reproduces one of the client's fixtures (PY-012, QR-004, QR-007,
QX-001) and checks that the script now returns an Issue or a failure
where it previously returned a pass.

Run from the repository root:  python -m pytest tests -v
"""
import csv
import importlib.util
from collections import Counter
from pathlib import Path

import pandas as pd
import pytest

REPO = Path(__file__).resolve().parents[1]


def _load(name, relative_path):
    spec = importlib.util.spec_from_file_location(name, REPO / relative_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


asrock = _load("asrock_module_capacity_mb", "Asrock/asrock_module_capacity_mb.py")
crucial = _load("cruical_maxmemory_speed", "Cruical/Cruical_maxmemory_speed.py")
dell_ranks = _load("dell_dimmranks", "Dell/dell_dimmranks.py")
dell_slot = _load("dell_dimmslot", "Dell/dell_dimmslot.py")


# ---------------------------------------------------------------- PY-012 ----
# ASR-QA-004: unparseable capacity values must not be marked Verified.

def test_py012_asrock_unparseable_values_are_issue():
    _, _, status, reason = asrock.validate_row("unparseable", "unparseable")
    assert status == "Issue"
    assert "capacity unparseable" in reason
    assert "module_capacity_mb unparseable" in reason


@pytest.mark.parametrize("capacity, actual", [
    ("unparseable", 16384),   # only capacity bad
    ("16GB", "unparseable"),  # only module_capacity_mb bad
    ("16GB", 100),            # plain mismatch
])
def test_py012_asrock_other_bad_rows_are_issue(capacity, actual):
    assert asrock.validate_row(capacity, actual)[2] == "Issue"


@pytest.mark.parametrize("capacity, actual", [("16GB", 16384), ("1TB", "1048576"), ("32 GB", 32768.0)])
def test_py012_asrock_good_rows_still_verified(capacity, actual):
    assert asrock.validate_row(capacity, actual)[2] == "Verified"


# ---------------------------------------------------------------- QR-004 ----
# CRUCIAL-QA-002: a mixed Match|Mismatch row must fail the pytest gate.

def _write_csv(path, rows):
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def test_qr004_crucial_mixed_status_fails_gate(tmp_path):
    main_csv = tmp_path / "main.csv"
    amd_csv = tmp_path / "amd.csv"
    intel_csv = tmp_path / "intel.csv"
    _write_csv(main_csv, [{
        "server_description": "Fixture",
        "processor": "['CPU A','CPU B']",
        "processor_family": "['Family']",
        "processor_max_memory_speed": "['CPU A (3200 MT/s)','CPU B (3600 MT/s)']",
    }])
    lookup_rows = [
        {"product_name": "CPU A", "maximum_memory_speed": "3200 MT/s", "product_family": "Family"},
        {"product_name": "CPU B", "maximum_memory_speed": "3200 MT/s", "product_family": "Family"},
    ]
    _write_csv(amd_csv, lookup_rows)
    _write_csv(intel_csv, lookup_rows[:1])

    summary = crucial.validate(main_csv, amd_csv, intel_csv, tmp_path / "d.csv", tmp_path / "s.csv")

    assert summary == Counter({"Match|Mismatch": 1})  # the client's observed summary
    with pytest.raises(AssertionError, match="1 row\\(s\\) contain Mismatch"):
        crucial.assert_no_mismatch_or_duplicate(summary)


def test_qr004_crucial_all_match_passes_gate():
    crucial.assert_no_mismatch_or_duplicate(Counter({"Match": 5}))


@pytest.mark.parametrize("status", ["Mismatch", "Duplicate", "Match|Duplicate", "Missing|Mismatch"])
def test_qr004_crucial_gate_catches_any_combination(status):
    with pytest.raises(AssertionError):
        crucial.assert_no_mismatch_or_duplicate(Counter({status: 1}))


# ---------------------------------------------------------------- QR-007 ----
# DELL-QA-001: 16GB vs 100MB must be a capacity issue, not PASS.

def _dell_row(capacity, module_capacity_mb):
    return pd.DataFrame([{
        "server_description": "Fixture",
        "dimm_ranks": "['2Rx4']",
        "server_dimm_ranks": "['2Rx4']",
        "capacity": capacity,
        "module_capacity_mb": module_capacity_mb,
    }])


def test_qr007_dell_capacity_mismatch_fails_row(tmp_path, monkeypatch):
    # Write the report into tmp_path instead of the Dell folder.
    monkeypatch.setattr(dell_ranks, "__file__", str(tmp_path / "dell_dimmranks.py"))

    with pytest.raises(pytest.fail.Exception):
        dell_ranks.test_dimm_ranks_presence_in_server(_dell_row("16GB", 100))

    report = pd.read_csv(tmp_path / "dimm_ranks_dell_report.csv")
    assert report.loc[0, "status"] == "FAIL"
    assert "Capacity mismatch" in report.loc[0, "capacity_issue"]


def test_qr007_dell_standalone_capacity_test_fails():
    with pytest.raises(pytest.fail.Exception, match="Capacity mismatch"):
        dell_ranks.test_capacity_mb_conversion_and_comparison(_dell_row("16GB", 100))


def test_qr007_dell_matching_capacity_still_passes(tmp_path, monkeypatch):
    monkeypatch.setattr(dell_ranks, "__file__", str(tmp_path / "dell_dimmranks.py"))
    dell_ranks.test_dimm_ranks_presence_in_server(_dell_row("16GB", 16384))
    dell_ranks.test_capacity_mb_conversion_and_comparison(_dell_row("16GB", 16384))
    report = pd.read_csv(tmp_path / "dimm_ranks_dell_report.csv")
    assert report.loc[0, "status"] == "PASS"


@pytest.mark.parametrize("capacity, module_mb", [("unparseable", 16384), ("16GB", "abc"), ("16GB", None)])
def test_qr007_dell_unparseable_or_missing_capacity_is_issue(capacity, module_mb):
    assert dell_ranks._capacity_issue(capacity, module_mb)


# ---------------------------------------------------------------- QX-001 ----
# DELL-QA-002: a missing referenced PDF must fail, not be skipped.

def test_qx001_dell_missing_pdf_fails(tmp_path, monkeypatch):
    csv_path = tmp_path / "dell.csv"
    _write_csv(csv_path, [{
        "file_name": "absent.pdf",
        "dimm_slots": "['16']",
        "maximum_memory": "['512GB']",
    }])

    failures = dell_slot.validate_pdfs(str(csv_path), str(tmp_path))
    assert len(failures) == 1
    assert "referenced PDF not found" in failures[0]

    monkeypatch.setattr(dell_slot, "CSV_PATH", str(csv_path))
    monkeypatch.setattr(dell_slot, "PDF_DIR", str(tmp_path))
    with pytest.raises(AssertionError, match="absent.pdf"):
        dell_slot.test_pdf_vs_csv()
