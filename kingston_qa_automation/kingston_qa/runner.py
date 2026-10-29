"""Validates one server CSV row against its live page.

Purpose : the step-by-step flow for a server row:
  1. check the fixed 'store' value (works even if the page is blocked)
  2. open host_url (retries + Cloudflare handling live in browser.py)
  3. blocked  -> every page field is BLOCKED, with the reason and a screenshot
     loaded   -> extract the page once and run every column rule independently
  4. reload the page once and confirm the extracted values did not change
  5. work out the row status (FAIL > NEEDS REVIEW > PASS)
Used by : tests/test_kingston_servers.py (one call per CSV row).
"""
from __future__ import annotations

import logging
import re
import time
from pathlib import Path

from .browser import KingstonPage
from .comparators import worst
from .models import CsvRow, FieldCheck, RowResult, Status
from .validators import (blocked_checks, check_store, compare_snapshots,
                         validate_page_fields)

log = logging.getLogger("kingston_qa.runner")


def _row_status(checks: list[FieldCheck], page_blocked: bool) -> Status:
    if page_blocked:
        return Status.BLOCKED
    statuses = [c.status for c in checks if c.status is not Status.SKIPPED]
    if Status.FAIL in statuses:
        return Status.FAIL
    if Status.BLOCKED in statuses or Status.NEEDS_REVIEW in statuses:
        return Status.NEEDS_REVIEW  # e.g. reload blocked: consistency unverified, so never PASS
    return Status.PASS


def validate_row(row: CsvRow, kp: KingstonPage, shots_dir: Path) -> RowResult:
    started = time.monotonic()
    ws_status = Status(kp.settings.whitespace_only_status)
    url = row.get("host_url")
    tag = f"row{row.row_no:03d}"
    checks: list[FieldCheck] = [check_store(row)]  # validated even if the page is blocked
    shots: list[str] = []

    def shoot(suffix: str) -> None:
        name = re.sub(r"[^A-Za-z0-9_-]+", "_", f"{tag}_{suffix}") + ".png"
        if (path := kp.screenshot(shots_dir / name)):
            shots.append(path)

    # Step 1: open the server page (browser.py retries transient errors and waits out challenges).
    nav = kp.open(url) if url else None
    # Step 2a: page not reachable -> every page-based column is BLOCKED (not a data mismatch).
    if nav is None or not nav.ok:
        reason = "CSV host_url is empty" if nav is None else nav.reason
        if nav is not None:
            shoot("blocked")
        checks += blocked_checks(row, reason)
        result = RowResult(row.row_no, url, row.get("server_description"), Status.BLOCKED, checks, shots, nav)
    else:
        # Step 2b: read the page once and run every column rule independently.
        first = kp.snapshot()
        checks += validate_page_fields(row, first, ws_status)
        if worst(c.status for c in checks) is not Status.PASS:
            shoot("validation")
        # Step 3: reload once and confirm the same values come back.
        reload_nav = kp.reload()
        if reload_nav.ok:
            consistency = compare_snapshots(first, kp.snapshot())
            if consistency.status is not Status.PASS:
                shoot("after_reload")
        else:
            consistency = FieldCheck("reload_consistency", Status.BLOCKED, "", "", f"Reload failed: {reload_nav.reason}")
            shoot("reload_blocked")
        checks.append(consistency)
        # Step 4: row status = worst column result (FAIL > NEEDS REVIEW > PASS).
        result = RowResult(row.row_no, url, row.get("server_description"), _row_status(checks, False), checks, shots, nav)

    result.duration_s = round(time.monotonic() - started, 2)
    bad = [f"{c.field}={c.status.value}" for c in checks if c.status not in (Status.PASS, Status.SKIPPED)]
    log.info("Row %d %s -> %s %s", row.row_no, row.get("server_description"), result.status.value, bad or "")
    return result
