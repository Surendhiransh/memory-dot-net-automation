"""Reads the servers CSV (read-only) into CsvRow objects.

Purpose : fail early if a required column is missing, and number rows 1..N so reports can point
          back to the exact CSV line. The original file is never written to.
Used by : tests/conftest.py (pytest_generate_tests) to create one test per CSV row.
"""
from __future__ import annotations

import csv
from pathlib import Path

from .models import CsvRow

REQUIRED_COLUMNS = (
    "store", "server_description", "host_url", "server_model", "server_type",
    "processor", "processor_sockets", "server_specification",
)


def load_rows(path: Path, start: int = 1, limit: int | None = None) -> list[CsvRow]:
    """Read the CSV read-only and return rows [start, start+limit)."""
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        missing = [c for c in REQUIRED_COLUMNS if c not in (reader.fieldnames or [])]
        if missing:
            raise ValueError(f"CSV {path} is missing required columns: {missing}")
        rows = [CsvRow(row_no=i, data=dict(r)) for i, r in enumerate(reader, start=1)]
    selected = [r for r in rows if r.row_no >= start]
    return selected[:limit] if limit else selected
