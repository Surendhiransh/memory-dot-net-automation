"""Pure comparison helpers (no browser): whitespace handling and JSON diffing.

Purpose : compare CSV values with website values fairly. Extra spaces, key order and heading case
          are ignored; any real text difference is reported with the expected and actual values.
Used by : validators.py (servers), parts.py (memory / USB) and ssd.py (SSD specifications).
"""
from __future__ import annotations

import json
import re
from typing import Any

from .models import Status

_WS = re.compile(r"\s+")


def norm_ws(value: Any) -> str:
    return _WS.sub(" ", str(value or "")).strip()


def _squash(value: Any) -> str:
    return _WS.sub("", str(value or ""))


def worst(statuses) -> Status:
    order = [Status.FAIL, Status.BLOCKED, Status.NEEDS_REVIEW, Status.SKIPPED, Status.PASS]
    statuses = list(statuses)
    return next((s for s in order if s in statuses), Status.PASS)


def compare_text(expected: str, actual: str | None, ws_only_status: Status) -> tuple[Status, str]:
    """Exact compare after collapsing whitespace; whitespace-only differences get ws_only_status."""
    if actual is None:
        return Status.FAIL, "Value not found on page"
    exp, act = norm_ws(expected), norm_ws(actual)
    if exp == act:
        return Status.PASS, ""
    if _squash(exp) == _squash(act):
        return ws_only_status, f"Differs only by whitespace: CSV {exp!r} vs site {act!r}"
    return Status.FAIL, f"Text mismatch: CSV {exp!r} vs site {act!r}"


def parse_json(raw: str, expected_type: type) -> tuple[Any, str]:
    """Parse a CSV JSON cell. Empty cells become an empty container of expected_type."""
    if not raw or raw.strip().lower() in ("nan", "null", "none"):
        return expected_type(), ""
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        return None, f"Invalid JSON in CSV: {exc}"
    if not isinstance(value, expected_type):
        return None, f"CSV JSON is {type(value).__name__}, expected {expected_type.__name__}"
    return value, ""


def compare_lists(expected: list, actual: list, ws_only_status: Status, path: str = "") -> tuple[Status, list[str]]:
    exp = [norm_ws(v) for v in expected]
    act = [norm_ws(v) for v in actual]
    if exp == act:
        return Status.PASS, []
    where = f"{path}: " if path else ""
    if [_squash(v) for v in exp] == [_squash(v) for v in act]:
        return ws_only_status, [f"{where}whitespace-only difference {exp} vs {act}"]
    if sorted(exp) == sorted(act):
        return Status.NEEDS_REVIEW, [f"{where}same values, different order {exp} vs {act}"]
    detail = f"{where}expected {exp} vs actual {act}"
    missing = [v for v in exp if v not in act]
    extra = [v for v in act if v not in exp]
    if missing:
        detail += f"; missing on site {missing}"
    if extra:
        detail += f"; extra on site {extra}"
    return Status.FAIL, [detail]


def _index(d: dict) -> dict[str, tuple[str, Any]]:
    """Key lookup ignoring case/whitespace (section headings are CSS-cased on the site)."""
    return {norm_ws(k).casefold(): (norm_ws(k), v) for k, v in d.items()}


def compare_spec(expected: dict, actual: dict, ws_only_status: Status) -> tuple[Status, list[str]]:
    """Compare {section: {label: [values]}} ignoring key order, key case and irrelevant whitespace."""
    issues: list[str] = []
    statuses: list[Status] = []

    def flag(status: Status, msg: str) -> None:
        statuses.append(status)
        issues.append(msg)

    exp_idx, act_idx = _index(expected), _index(actual)
    for key, (name, exp_section) in exp_idx.items():
        if key not in act_idx:
            flag(Status.FAIL, f"Section '{name}' missing on site")
            continue
        if not isinstance(exp_section, dict):
            flag(Status.FAIL, f"Section '{name}' in CSV is not an object")
            continue
        e_labels, a_labels = _index(exp_section), _index(act_idx[key][1])
        for lkey, (lname, e_vals) in e_labels.items():
            if lkey not in a_labels:
                flag(Status.FAIL, f"'{name}' > '{lname}' missing on site (CSV {e_vals})")
                continue
            e_list = e_vals if isinstance(e_vals, list) else [e_vals]
            st, msgs = compare_lists(e_list, a_labels[lkey][1], ws_only_status, f"'{name}' > '{lname}'")
            statuses.append(st)
            issues.extend(msgs)
        for lkey, (lname, a_vals) in a_labels.items():
            if lkey not in e_labels:
                flag(Status.FAIL, f"'{name}' > '{lname}' on site but not in CSV ({a_vals})")
    for key, (name, a_section) in act_idx.items():
        if key not in exp_idx:
            flag(Status.FAIL, f"Section '{name}' on site but not in CSV ({a_section})")
    return worst(statuses), issues
