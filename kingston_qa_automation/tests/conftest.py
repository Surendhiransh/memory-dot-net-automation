"""Pytest wiring shared by all live validations (servers, memory, USB, SSD).

Purpose :
  1. Command-line options (--rows, --only-rows, --headless, --reports-dir, ...).
  2. Test generation: one test per server row, or one test per server page for parts files.
  3. Session fixtures: settings, run folder + log file, one shared browser, result collectors.
  4. When the session ends, the collectors write the CSV / Excel / HTML / JSON reports.
Usage   : python -m pytest tests/test_kingston_servers.py --rows 5
"""
from __future__ import annotations

import logging
import time
from datetime import datetime

import pytest
from playwright.sync_api import sync_playwright

from kingston_qa.browser import KingstonPage
from kingston_qa.config import Settings
from kingston_qa.csv_loader import load_rows
from kingston_qa.parts import group_by_page, load_part_rows
from kingston_qa.parts_reporter import write_parts_reports
from kingston_qa.reporter import write_reports
from kingston_qa.ssd import VariantCache

log = logging.getLogger("kingston_qa")


def pytest_addoption(parser):
    g = parser.getgroup("kingston", "Kingston CSV QA")
    g.addoption("--csv", default=None, help="Path to the servers CSV (default kingston_servers_30.csv)")
    g.addoption("--parts-csv", default=None, dest="parts_csv",
                help="Memory parts CSV/ODS/XLSX (default 'kingston memory.csv')")
    g.addoption("--ssd-csv", default=None, dest="ssd_csv",
                help="Parts file whose 'ssd' rows are validated (default kingston_parts_30.csv)")
    g.addoption("--rows", type=int, default=None, help="Validate only N rows (0 = all)")
    g.addoption("--row-start", type=int, default=None, dest="row_start", help="First data row number (1-based)")
    g.addoption("--only-rows", default=None, dest="only_rows", help="Comma-separated row numbers, e.g. 11,12,19")
    g.addoption("--headless", action="store_true", default=False, help="Run headless (Cloudflare usually blocks this)")
    g.addoption("--channel", default=None, help="Browser channel: chrome (default), msedge, chromium")
    g.addoption("--nav-timeout", type=int, default=None, dest="nav_timeout", help="Navigation timeout in ms")
    g.addoption("--row-delay", type=float, default=None, dest="row_delay", help="Pause between rows in seconds")
    g.addoption("--reports-dir", default=None, dest="reports_dir", help="Root folder for report runs")


def _settings(config) -> Settings:
    if not hasattr(config, "_kqa_settings"):
        config._kqa_settings = Settings.from_options(config.option)
    return config._kqa_settings


def pytest_generate_tests(metafunc):
    if "csv_row" in metafunc.fixturenames:
        s = _settings(metafunc.config)
        rows = load_rows(s.csv_path, s.row_start, s.row_limit)
        if s.only_rows:
            rows = [r for r in rows if r.row_no in s.only_rows]
        metafunc.parametrize("csv_row", rows, ids=[f"row{r.row_no:03d}" for r in rows])
    if "parts_page" in metafunc.fixturenames:
        s = _settings(metafunc.config)
        rows = load_part_rows(s.parts_csv_path, s.parts_category or None, s.row_start, s.row_limit, s.only_rows)
        pages = group_by_page(rows)
        metafunc.parametrize("parts_page", pages, ids=[f"rows{p.rows[0].row_no:03d}-{p.rows[-1].row_no:03d}" for p in pages])
    if "ssd_page" in metafunc.fixturenames:
        s = _settings(metafunc.config)
        rows = load_part_rows(s.ssd_csv_path, "ssd", s.row_start, s.row_limit, s.only_rows)
        pages = group_by_page(rows)
        metafunc.parametrize("ssd_page", pages, ids=[f"rows{p.rows[0].row_no:03d}-{p.rows[-1].row_no:03d}" for p in pages])


@pytest.fixture(scope="session")
def settings(pytestconfig) -> Settings:
    return _settings(pytestconfig)


@pytest.fixture(scope="session")
def run_dir(settings):
    path = settings.reports_root / f"run_{datetime.now():%Y%m%d_%H%M%S}"
    (path / "screenshots").mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(path / "run.log", encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    yield path
    root.removeHandler(handler)
    handler.close()


@pytest.fixture(scope="session")
def qa_results(settings, run_dir):
    results = []
    started = datetime.now()
    yield results
    if results:
        meta = {"csv": settings.csv_path, "headless": settings.headless, "channel": settings.browser_channel or "chromium",
                "started": f"{started:%Y-%m-%d %H:%M:%S}", "finished": f"{datetime.now():%Y-%m-%d %H:%M:%S}",
                "max_retries": settings.max_retries, "nav_timeout_ms": settings.nav_timeout_ms}
        paths = write_reports(results, run_dir, meta)
        log.info("Reports written: %s", {k: str(v) for k, v in paths.items()})


@pytest.fixture(scope="session")
def parts_results(settings, run_dir):
    results = []
    started = datetime.now()
    yield results
    if results:
        meta = {"input": settings.parts_csv_path, "category": settings.parts_category,
                "headless": settings.headless, "started": f"{started:%Y-%m-%d %H:%M:%S}",
                "finished": f"{datetime.now():%Y-%m-%d %H:%M:%S}", "max_retries": settings.max_retries}
        category = settings.parts_category or "parts"
        prefix = {"memory": "parts", "datatraveler usb drives": "usb"}.get(category, category.replace(" ", "_"))
        title = "Kingston Memory Parts QA" if category == "memory" else f"Kingston {category.title()} QA"
        paths = write_parts_reports(results, run_dir, meta, title=title, prefix=prefix)
        log.info("Parts reports written: %s", {k: str(v) for k, v in paths.items()})


@pytest.fixture(scope="session")
def ssd_results(settings, run_dir):
    results = []
    started = datetime.now()
    yield results
    if results:
        meta = {"input": settings.ssd_csv_path, "category": "ssd", "headless": settings.headless,
                "started": f"{started:%Y-%m-%d %H:%M:%S}", "finished": f"{datetime.now():%Y-%m-%d %H:%M:%S}",
                "max_retries": settings.max_retries}
        paths = write_parts_reports(results, run_dir, meta, title="Kingston SSD Parts QA", prefix="ssd")
        log.info("SSD reports written: %s", {k: str(v) for k, v in paths.items()})


@pytest.fixture(scope="session")
def ssd_variants(kingston_page, settings):
    return VariantCache(kingston_page, settings.variant_delay_s)


@pytest.fixture(scope="session")
def kingston_page(settings):
    """One persistent browser profile for the whole run (keeps cookies like a normal browser session)."""
    with sync_playwright() as pw:
        context = pw.chromium.launch_persistent_context(
            str(settings.user_data_dir), headless=settings.headless, channel=settings.browser_channel,
            viewport={"width": 1366, "height": 900}, locale="en-US")
        try:
            yield KingstonPage(context, settings)
        finally:
            context.close()


@pytest.fixture
def row_pacing(settings):
    """Polite delay between consecutive rows."""
    yield
    time.sleep(settings.row_delay_s)
