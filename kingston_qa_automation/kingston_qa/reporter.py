"""Server report writer: turns RowResults into the shared row-wise layout (wide.py) plus JSON.

Outputs : server_results.csv / .xlsx, server_report.html, results.json (JSON keeps every detail and
          is what merge_reports.py reads back).
Used by : tests/conftest.py (end of a server run) and merge_reports.py.
"""
from __future__ import annotations

import json
from collections import Counter
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .models import Match, RowResult, Status
from .wide import WideTable, write_csv, write_html, write_xlsx

STATUS_ORDER = [s.value for s in Status]
MATCH_ORDER = [m.value for m in Match]
LEGEND = ("Matched = CSV equals website;  Mismatched = both have values that differ;  "
          "Unmatched = no value on one side (missing on site or blocked)")


def _cell(value: Any) -> str:
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return "" if value is None else str(value)


def _title(field: str) -> str:
    return field.replace("_", " ").title().replace("Url", "URL")


def summarize(results: list[RowResult]) -> dict[str, Any]:
    rows = Counter(r.status.value for r in results)
    fields = Counter(c.status.value for r in results for c in r.checks)
    matches = Counter(c.match.value for r in results for c in r.checks)
    return {
        "total_rows": len(results),
        "rows_by_status": {s: rows.get(s, 0) for s in STATUS_ORDER},
        "checks_by_status": {s: fields.get(s, 0) for s in STATUS_ORDER},
        "checks_by_match": {m: matches.get(m, 0) for m in MATCH_ORDER},
    }


def build_table(results: list[RowResult]) -> WideTable:
    fields: list[str] = []
    for r in results:
        fields += [c.field for c in r.checks if c.field not in fields]
    rows = []
    for r in results:
        by_field = {c.field: c for c in r.checks}
        values: list[Any] = [r.row_no, r.status.value]
        for f in fields:
            c = by_field.get(f)
            values += [_cell(c.expected), _cell(c.actual), c.match.value] if c else ["", "", ""]
        issues = "\n".join(f"{c.field}: {c.status.value} - {c.reason}" for c in r.checks
                           if c.status not in (Status.PASS, Status.SKIPPED))
        values += [issues or "All fields matched.", " | ".join(r.screenshots)]
        rows.append(values)
    return WideTable(
        title="Kingston Server QA", lead=["Row No", "Row Status"], groups=[_title(f) for f in fields],
        parts=["CSV", "Website", "Match"], tail=["Issues", "Screenshots"], rows=rows, overall_col=2,
        overall_values=[s for s in STATUS_ORDER if s != "SKIPPED"], field_values=MATCH_ORDER, status_part=2,
        legend=LEGEND, record_label="Rows", wide_groups={"Server Specification", "Reload Consistency"})


def write_reports(results: list[RowResult], out_dir: Path, meta: dict[str, Any]) -> dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    results = sorted(results, key=lambda r: r.row_no)
    summary = summarize(results)
    table = build_table(results)
    paths = {"csv": out_dir / "server_results.csv", "xlsx": out_dir / "server_results.xlsx",
             "html": out_dir / "server_report.html", "json": out_dir / "results.json"}
    write_csv(table, paths["csv"])
    write_xlsx(table, paths["xlsx"], meta)
    write_html(table, paths["html"], meta, {k: v for k, v in summary["rows_by_status"].items() if k != "SKIPPED"})
    payload = {"meta": meta, "summary": summary, "results": [_row_dict(r) for r in results]}
    paths["json"].write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    return paths


def _row_dict(r: RowResult) -> dict[str, Any]:
    d = asdict(r)
    d["status"] = r.status.value
    for c, check in zip(d["checks"], r.checks):
        c["status"] = c["status"].value
        c["match"] = check.match.value
    return d
