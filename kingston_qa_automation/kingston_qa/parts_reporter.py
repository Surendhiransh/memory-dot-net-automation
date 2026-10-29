"""Parts report writer (memory / SSD / USB): PartResults -> shared row-wise layout (wide.py) plus JSON.

Outputs : <prefix>_results.csv / .xlsx, <prefix>_report.html, <prefix>_results.json.
          Columns: Row No | Overall Status | <field> CSV / Website / Status ... | Comments.
Used by : tests/conftest.py (end of a parts run) and merge_reports.py --parts.
"""
from __future__ import annotations

import json
from collections import Counter
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .parts import PART_COLUMNS, PartCheck, PartResult, PartStatus
from .wide import WideTable, write_csv, write_html, write_xlsx

STATUSES = [s.value for s in PartStatus]
# Report columns: part number first so each line is identifiable; skipped-only columns are left out.
REPORT_COLUMNS = ["part_number"] + [c for c in PART_COLUMNS if c not in ("part_number", "status")] + ["reload_consistency"]
LEGEND = ("MATCHED = CSV and website values match;  MISMATCHED = both values exist but differ;  "
          "UNMATCHED = product or value missing / cannot be matched;  BLOCKED = website inaccessible")


def _text(value: Any) -> str:
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return "" if value is None else str(value)


def _title(column: str) -> str:
    return column.replace("_", " ").title().replace("Url", "URL")


def summarize(results: list[PartResult]) -> dict[str, Any]:
    overall = Counter(r.overall.value for r in results)
    checks = Counter(c.status.value for r in results for c in r.checks)
    return {"total_records": len(results),
            "records_by_overall_status": {s: overall.get(s, 0) for s in STATUSES},
            "column_checks_by_status": {s: checks.get(s, 0) for s in STATUSES}}


def _comments(r: PartResult) -> str:
    issues = [f"{c.column}: {c.comment}" for c in r.checks if c.status not in (PartStatus.MATCHED, PartStatus.SKIPPED)]
    return "\n".join(issues) if issues else "All columns matched."


def build_table(results: list[PartResult], title: str) -> WideTable:
    rows = []
    for r in results:
        by_col = {c.column: c for c in r.checks}
        values: list[Any] = [r.row_no, r.overall.value]
        for column in REPORT_COLUMNS:
            c = by_col.get(column)
            values += [_text(c.csv_value), _text(c.site_value), c.status.value] if c else ["", "", ""]
        values.append(_comments(r))
        rows.append(values)
    return WideTable(
        title=title, lead=["Row No", "Overall Status"], groups=[_title(c) for c in REPORT_COLUMNS],
        parts=["CSV", "Website", "Status"], tail=["Comments"], rows=rows, overall_col=2,
        overall_values=[s for s in STATUSES if s != "SKIPPED"],
        field_values=[s for s in STATUSES if s != "SKIPPED"], status_part=2, legend=LEGEND,
        wide_groups={"Part Specification", "Reload Consistency"})


def write_parts_reports(results: list[PartResult], out_dir: Path, meta: dict[str, Any],
                        title: str = "Kingston Memory Parts QA", prefix: str = "parts") -> dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    results = sorted(results, key=lambda r: r.row_no)
    summary = summarize(results)
    table = build_table(results, title)
    paths = {"csv": out_dir / f"{prefix}_results.csv", "xlsx": out_dir / f"{prefix}_results.xlsx",
             "html": out_dir / f"{prefix}_report.html", "json": out_dir / f"{prefix}_results.json"}
    write_csv(table, paths["csv"])
    write_xlsx(table, paths["xlsx"], meta)
    write_html(table, paths["html"], meta, {k: v for k, v in summary["records_by_overall_status"].items() if k != "SKIPPED"})
    payload = {"meta": meta, "summary": summary,
               "results": [{**asdict(r), "overall": r.overall.value,
                            "checks": [{**asdict(c), "status": c.status.value} for c in r.checks]} for r in results]}
    paths["json"].write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    return paths


def load_parts_json(path: Path) -> list[PartResult]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return [PartResult(r["row_no"], r["server_description"], r["part_number"], r["host_url"],
                       PartStatus(r["overall"]),
                       [PartCheck(c["column"], c["csv_value"], c["site_value"], PartStatus(c["status"]), c["comment"])
                        for c in r["checks"]], r["screenshots"]) for r in data["results"]]
