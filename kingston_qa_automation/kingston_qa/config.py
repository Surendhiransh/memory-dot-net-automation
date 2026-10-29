"""Run settings for every validation (servers, memory, SSD, USB).

Purpose : one place for paths, timeouts, retry counts and pacing, so no value is hard-coded elsewhere.
Used by : tests/conftest.py builds one Settings object per pytest session and hands it to the fixtures.
Usage   : every field can be set by a pytest option (e.g. --rows 5) or by an environment variable
          named KQA_<NAME> (e.g. KQA_MAX_RETRIES=3, KQA_HEADLESS=1). Options win over env vars.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _env(name: str, default: str) -> str:
    return os.environ.get(f"KQA_{name}", default)


@dataclass(frozen=True)
class Settings:
    csv_path: Path
    parts_csv_path: Path
    parts_category: str
    ssd_csv_path: Path
    variant_delay_s: float
    reports_root: Path
    headless: bool
    browser_channel: str | None
    user_data_dir: Path
    nav_timeout_ms: int
    element_timeout_ms: int
    challenge_wait_ms: int
    max_retries: int
    retry_backoff_s: float
    row_delay_s: float
    row_start: int
    row_limit: int | None
    only_rows: frozenset[int]
    whitespace_only_status: str

    @classmethod
    def from_options(cls, opt) -> "Settings":
        """Build settings from pytest options; each falls back to a KQA_* env var."""
        def pick(attr: str, env: str, default: str) -> str:
            value = getattr(opt, attr, None)
            return str(value) if value is not None else _env(env, default)

        limit = pick("rows", "ROWS", "0")
        channel = pick("channel", "CHANNEL", "chrome")
        return cls(
            csv_path=Path(pick("csv", "CSV", str(PROJECT_ROOT / "kingston_servers_30.csv"))),
            parts_csv_path=Path(pick("parts_csv", "PARTS_CSV", str(PROJECT_ROOT / "kingston memory.csv"))),
            parts_category=_env("PARTS_CATEGORY", "memory"),
            ssd_csv_path=Path(pick("ssd_csv", "SSD_CSV", str(PROJECT_ROOT / "kingston_parts_30.csv"))),
            variant_delay_s=float(_env("VARIANT_DELAY_S", "5")),
            reports_root=Path(pick("reports_dir", "REPORTS_DIR", str(PROJECT_ROOT / "reports"))),
            headless=getattr(opt, "headless", False) or _env("HEADLESS", "0") == "1",
            browser_channel=None if channel.lower() in ("", "chromium", "none") else channel,
            user_data_dir=Path(_env("PROFILE_DIR", str(PROJECT_ROOT / ".browser_profile"))),
            nav_timeout_ms=int(pick("nav_timeout", "NAV_TIMEOUT_MS", "60000")),
            element_timeout_ms=int(_env("ELEMENT_TIMEOUT_MS", "15000")),
            challenge_wait_ms=int(_env("CHALLENGE_WAIT_MS", "30000")),
            max_retries=int(_env("MAX_RETRIES", "2")),
            retry_backoff_s=float(_env("RETRY_BACKOFF_S", "5")),
            row_delay_s=float(pick("row_delay", "ROW_DELAY_S", "2")),
            row_start=int(pick("row_start", "ROW_START", "1")),
            row_limit=int(limit) or None,
            only_rows=frozenset(int(n) for n in pick("only_rows", "ONLY_ROWS", "").split(",") if n.strip()),
            whitespace_only_status=_env("WHITESPACE_ONLY_STATUS", "NEEDS REVIEW"),
        )
