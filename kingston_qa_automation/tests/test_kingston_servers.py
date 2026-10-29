"""Live validation of every servers CSV row against kingston.com (one test per row).

Usage   : python -m pytest tests/test_kingston_servers.py --row-delay 10
          add --rows 5 for a quick sample, or --only-rows 11,12 to re-run specific rows.
Outcome : the reports are the source of truth. Pytest shows PASS -> passed, FAIL -> failed,
          BLOCKED -> skipped, NEEDS REVIEW -> xfailed.
"""
import pytest

from kingston_qa.models import Status
from kingston_qa.runner import validate_row


@pytest.mark.e2e
def test_server_row(csv_row, kingston_page, qa_results, run_dir, row_pacing):
    result = validate_row(csv_row, kingston_page, run_dir / "screenshots")
    qa_results.append(result)

    issues = "\n".join(f"  {c.field}: {c.status.value} - {c.reason}" for c in result.checks
                       if c.status not in (Status.PASS, Status.SKIPPED))
    if result.status is Status.BLOCKED:
        pytest.skip(f"BLOCKED: {result.nav.reason if result.nav else 'no URL'}")
    if result.status is Status.FAIL:
        pytest.fail(f"Row {csv_row.row_no} FAIL\n{issues}", pytrace=False)
    if result.status is Status.NEEDS_REVIEW:
        pytest.xfail(f"NEEDS REVIEW\n{issues}")
