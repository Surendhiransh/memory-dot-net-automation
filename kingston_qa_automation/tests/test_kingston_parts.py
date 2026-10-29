"""Live validation of memory (or USB) part rows against kingston.com (one test per server page).

Usage   : python -m pytest tests/test_kingston_parts.py --row-delay 10
          USB: KQA_PARTS_CATEGORY="datatraveler usb drives" python -m pytest tests/test_kingston_parts.py
               --parts-csv kingston_parts_30.csv --reports-dir reports/usb
Outcome : any MISMATCHED -> failed, else any UNMATCHED -> xfailed, else any BLOCKED -> skipped, else passed.
"""
import pytest

from kingston_qa.parts import PartStatus, validate_parts_page


@pytest.mark.e2e
def test_parts_page(parts_page, kingston_page, parts_results, run_dir, row_pacing):
    results = validate_parts_page(parts_page, kingston_page, run_dir / "screenshots")
    parts_results.extend(results)

    def lines(status):
        return "\n".join(f"  row {r.row_no} {r.part_number} {c.column}: {c.comment}"
                         for r in results for c in r.checks if c.status is status)

    overall = {r.overall for r in results}
    if PartStatus.MISMATCHED in overall:
        pytest.fail(f"MISMATCHED on {parts_page.host_url}\n{lines(PartStatus.MISMATCHED)}", pytrace=False)
    if PartStatus.UNMATCHED in overall:
        pytest.xfail(f"UNMATCHED\n{lines(PartStatus.UNMATCHED)}")
    if PartStatus.BLOCKED in overall:
        pytest.skip(f"BLOCKED: {next(c.comment for r in results for c in r.checks if c.status is PartStatus.BLOCKED)}")
