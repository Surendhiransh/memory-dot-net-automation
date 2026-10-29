"""Shared data types for the server validation.

Purpose : Status (PASS/FAIL/BLOCKED/SKIPPED/NEEDS REVIEW), Match (Matched/Mismatched/Unmatched),
          and the records passed between loader -> browser -> validators -> reporter.
Used by : csv_loader, browser, validators, runner, reporter, merge_reports.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Status(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    BLOCKED = "BLOCKED"
    SKIPPED = "SKIPPED"
    NEEDS_REVIEW = "NEEDS REVIEW"


@dataclass
class CsvRow:
    row_no: int  # 1-based data row number (header excluded)
    data: dict[str, str]

    def get(self, column: str) -> str:
        return (self.data.get(column) or "").strip()


class Match(str, Enum):
    MATCHED = "Matched"        # CSV value equals website value
    MISMATCHED = "Mismatched"  # both sides have a value, and they differ
    UNMATCHED = "Unmatched"    # one side has no value to compare (missing on site, blocked, skipped)


def _blank(value: Any) -> bool:
    return value is None or (isinstance(value, (str, list, dict)) and not value)


@dataclass
class FieldCheck:
    field: str
    status: Status
    expected: Any = ""  # value in the CSV
    actual: Any = ""    # value on the website
    reason: str = ""

    @property
    def match(self) -> Match:
        if self.status is Status.PASS:
            return Match.MATCHED
        if self.status in (Status.BLOCKED, Status.SKIPPED) or _blank(self.expected) or _blank(self.actual):
            return Match.UNMATCHED
        return Match.MISMATCHED


@dataclass
class NavResult:
    ok: bool
    final_url: str = ""
    http_status: int | None = None
    attempts: int = 0
    reason: str = ""


@dataclass
class PageSnapshot:
    url: str
    description: str | None
    cards: list[dict[str, Any]]


@dataclass
class RowResult:
    row_no: int
    host_url: str
    server_description: str
    status: Status
    checks: list[FieldCheck] = field(default_factory=list)
    screenshots: list[str] = field(default_factory=list)
    nav: NavResult | None = None
    duration_s: float = 0.0
