"""Live validation of the SSD rows of the parts CSV (one test per server page).

Usage   : python -m pytest tests/test_kingston_ssd.py --row-delay 10 --reports-dir reports/ssd
Outcome : any MISMATCHED -> failed, else any UNMATCHED -> xfailed, else any BLOCKED -> skipped, else passed.
"""
import pytest

from kingston_qa.parts import PartStatus
from kingston_qa.ssd import validate_ssd_page


@pytest.mark.e2e
def test_ssd_page(ssd_page, kingston_page, ssd_variants, ssd_results, run_dir, row_pacing):
    results = validate_ssd_page(ssd_page, kingston_page, ssd_variants, run_dir / "screenshots")
    ssd_results.extend(results)

    def lines(status):
        return "\n".join(f"  row {r.row_no} {r.part_number} {c.column}: {c.comment}"
                         for r in results for c in r.checks if c.status is status)

    overall = {r.overall for r in results}
    if PartStatus.MISMATCHED in overall:
        pytest.fail(f"MISMATCHED on {ssd_page.host_url}\n{lines(PartStatus.MISMATCHED)}", pytrace=False)
    if PartStatus.UNMATCHED in overall:
        pytest.xfail(f"UNMATCHED\n{lines(PartStatus.UNMATCHED)}")
    if PartStatus.BLOCKED in overall:
        pytest.skip(f"BLOCKED: {next(c.comment for r in results for c in r.checks if c.status is PartStatus.BLOCKED)}")
