"""Combine several runs into one final report. For each row, the latest run wins.

Why     : a full run can have rows BLOCKED by Cloudflare. Re-run just those rows (--only-rows),
          then merge so the final report has one up-to-date line per CSV row.
Usage   :
  Servers : python merge_reports.py reports/run_A/results.json reports/run_B/results.json -o reports/server/final
  Parts   : python merge_reports.py --parts --prefix ssd --title "Kingston SSD Parts QA"
                reports/ssd/run_A/ssd_results.json reports/ssd/run_B/ssd_results.json -o reports/ssd/final
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from kingston_qa.models import FieldCheck, NavResult, RowResult, Status
from kingston_qa.parts_reporter import load_parts_json, write_parts_reports
from kingston_qa.reporter import write_reports


def _load(path: Path) -> list[RowResult]:
    data = json.loads(path.read_text(encoding="utf-8"))
    results = []
    for r in data["results"]:
        checks = [FieldCheck(c["field"], Status(c["status"]), c["expected"], c["actual"], c["reason"]) for c in r["checks"]]
        nav = NavResult(**r["nav"]) if r.get("nav") else None
        results.append(RowResult(r["row_no"], r["host_url"], r["server_description"], Status(r["status"]),
                                 checks, r["screenshots"], nav, r["duration_s"]))
    return results


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("results", nargs="+", type=Path, help="results.json files, oldest first")
    ap.add_argument("--parts", action="store_true", help="inputs are parts JSON (memory / SSD / USB reports)")
    ap.add_argument("--title", default="Kingston Memory Parts QA", help="report title for --parts")
    ap.add_argument("--prefix", default="parts", help="output file prefix for --parts, e.g. ssd, usb")
    ap.add_argument("-o", "--out", type=Path, default=Path("reports") / f"consolidated_{datetime.now():%Y%m%d_%H%M%S}")
    args = ap.parse_args()
    load, write = (load_parts_json, write_parts_reports) if args.parts else (_load, write_reports)
    merged = {}
    for path in args.results:
        for result in load(path):
            merged[result.row_no] = result
    meta = {"sources": ", ".join(str(p) for p in args.results), "rule": "latest run wins per row"}
    extra = {"title": args.title, "prefix": args.prefix} if args.parts else {}
    for kind, path in write(list(merged.values()), args.out, meta, **extra).items():
        print(f"{kind}: {path}")


if __name__ == "__main__":
    main()
