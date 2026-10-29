"""Column rules for the servers CSV. Each rule returns a FieldCheck and never raises.

Purpose : one function per CSV column (store, server_description, host_url, server_model,
          server_type, processor, processor_sockets, server_specification), plus the reload check.
          Rules run independently, so one failing column never hides another.
Used by : runner.py.
"""
from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urlsplit

from .comparators import compare_lists, compare_spec, compare_text, norm_ws, parse_json
from .models import CsvRow, FieldCheck, PageSnapshot, Status

STORE_NAME = "Kingston"
# Longer phrases first so "Blade Server" wins over "Server".
SERVER_TYPE_KEYWORDS = ("Blade Server", "Mini PC", "Motherboard", "Server", "Workstation",
                        "Desktop", "Notebook", "Laptop", "System")
PROCESSOR_HEADINGS = {"cpu/chipsets", "cpu/chipset", "processor", "processors"}
SOCKET_HEADINGS = {"expansions", "expansion"}
DEFAULT_LABEL = "Details"
PAGE_FIELDS = ("server_description", "host_url", "server_model", "server_type",
               "processor", "processor_sockets", "server_specification")
# parts_found and status are out of scope and intentionally not reported.
# The site's JS appends ?status=active to the URL after load (history state, not a redirect).
IGNORED_QUERY_PARAMS = {"status"}


def build_spec(snapshot: PageSnapshot) -> dict[str, dict[str, list[str]]]:
    """Cards -> {h3: {h4 or 'Details': [values]}}; cards without list items are omitted."""
    spec: dict[str, dict[str, list[str]]] = {}
    for card in snapshot.cards:
        heading = norm_ws(card.get("heading"))
        items = card.get("items") or []
        if not heading or not items:
            continue
        section = spec.setdefault(heading, {})
        for item in items:
            section.setdefault(norm_ws(item.get("label")) or DEFAULT_LABEL, []).append(norm_ws(item.get("value")))
    return spec


def card_values(snapshot: PageSnapshot, headings: set[str]) -> list[str] | None:
    """All values of the first card whose h3 matches; None when no such card exists."""
    for card in snapshot.cards:
        if norm_ws(card.get("heading")).casefold().replace(" ", "") in headings:
            return [norm_ws(i.get("value")) for i in card.get("items") or []]
    return None


def derive_model(description: str) -> str:
    parts = re.split(r" ?- ", norm_ws(description), maxsplit=1)  # norm_ws already collapsed whitespace
    return parts[1] if len(parts) == 2 else norm_ws(description)


def derive_type(description: str) -> str:
    for keyword in SERVER_TYPE_KEYWORDS:
        if re.search(rf"\b{re.escape(keyword)}\b", description or "", re.IGNORECASE):
            return keyword
    return ""


def norm_url(url: str) -> str:
    """Scheme/host lower-cased, trailing slash and site-added UI-state query params dropped."""
    p = urlsplit(norm_ws(url))
    query = urlencode([(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True) if k not in IGNORED_QUERY_PARAMS])
    return f"{p.scheme.lower()}://{p.netloc.lower()}{p.path.rstrip('/')}" + (f"?{query}" if query else "")


def check_store(row: CsvRow) -> FieldCheck:
    csv_value = row.get("store")
    ok = csv_value == STORE_NAME
    return FieldCheck("store", Status.PASS if ok else Status.FAIL, csv_value, STORE_NAME,
                      "Fixed value, not scraped" if ok else f"store must be the fixed value '{STORE_NAME}'")


def check_description(row: CsvRow, snap: PageSnapshot) -> FieldCheck:
    # Scraped descriptions drop the space before the brand separator ("Acer-" vs "Acer -"); per QA
    # decision that whitespace-only difference passes (noted in the reason). Real text changes still fail.
    st, reason = compare_text(row.get("server_description"), snap.description, Status.PASS)
    return FieldCheck("server_description", st, row.get("server_description"), snap.description or "", reason)


def check_host_url(row: CsvRow, snap: PageSnapshot) -> FieldCheck:
    exp, act = row.get("host_url"), snap.url
    if norm_url(exp) != norm_url(act):
        return FieldCheck("host_url", Status.FAIL, exp, act, "Current page URL differs from CSV host_url (redirect?)")
    note = "" if exp.rstrip("/") == act.rstrip("/") else f"Matched ignoring site-added query params {sorted(IGNORED_QUERY_PARAMS)}"
    return FieldCheck("host_url", Status.PASS, exp, act, note)


def check_model(row: CsvRow, snap: PageSnapshot, ws: Status) -> FieldCheck:
    expected = row.get("server_model")
    if snap.description is None:
        return FieldCheck("server_model", Status.FAIL, expected, "", "No description on page to derive model from")
    derived = derive_model(snap.description)
    st, reason = compare_text(expected, derived, ws)
    return FieldCheck("server_model", st, expected, derived, reason)


def check_type(row: CsvRow, snap: PageSnapshot) -> FieldCheck:
    expected = row.get("server_type")
    expected = "" if expected.lower() == "nan" else expected
    if snap.description is None:
        return FieldCheck("server_type", Status.FAIL, expected, "", "No description on page to derive type from")
    derived = derive_type(snap.description)
    ok = expected.casefold() == derived.casefold()
    reason = "" if ok else f"Derived {derived or '(none)'!r} from site description, CSV has {expected or '(empty)'!r}"
    return FieldCheck("server_type", Status.PASS if ok else Status.FAIL, expected, derived, reason)


def _check_card_list(column: str, row: CsvRow, snap: PageSnapshot, headings: set[str], ws: Status) -> FieldCheck:
    raw = row.get(column)
    expected, err = parse_json(raw, list)
    actual = card_values(snap, headings)
    if err:
        return FieldCheck(column, Status.FAIL, raw, actual, err)
    if actual is None:
        if not expected:
            return FieldCheck(column, Status.PASS, expected, [], "Card absent on site and CSV empty")
        return FieldCheck(column, Status.FAIL, expected, [], f"No card with heading in {sorted(headings)} on page")
    st, msgs = compare_lists(expected, actual, ws)
    return FieldCheck(column, st, expected, actual, "; ".join(msgs))


def check_specification(row: CsvRow, snap: PageSnapshot, ws: Status) -> FieldCheck:
    raw = row.get("server_specification")
    expected, err = parse_json(raw, dict)
    actual = build_spec(snap)
    if err:
        return FieldCheck("server_specification", Status.FAIL, raw, actual, err)
    st, issues = compare_spec(expected, actual, ws)
    return FieldCheck("server_specification", st, expected, actual, "; ".join(issues))


def validate_page_fields(row: CsvRow, snap: PageSnapshot, ws: Status) -> list[FieldCheck]:
    """Run every page-dependent check independently so one failure never hides another."""
    rules = {
        "server_description": lambda: check_description(row, snap),
        "host_url": lambda: check_host_url(row, snap),
        "server_model": lambda: check_model(row, snap, ws),
        "server_type": lambda: check_type(row, snap),
        "processor": lambda: _check_card_list("processor", row, snap, PROCESSOR_HEADINGS, ws),
        "processor_sockets": lambda: _check_card_list("processor_sockets", row, snap, SOCKET_HEADINGS, ws),
        "server_specification": lambda: check_specification(row, snap, ws),
    }
    checks = []
    for name, rule in rules.items():
        try:
            checks.append(rule())
        except Exception as exc:  # a rule bug must not abort the other columns
            checks.append(FieldCheck(name, Status.NEEDS_REVIEW, row.get(name), "", f"Validator error: {exc!r}"))
    return checks


def blocked_checks(row: CsvRow, reason: str) -> list[FieldCheck]:
    return [FieldCheck(f, Status.BLOCKED, row.get(f), "", reason) for f in PAGE_FIELDS]


def compare_snapshots(first: PageSnapshot, second: PageSnapshot) -> FieldCheck:
    def view(s: PageSnapshot) -> dict:
        return {"url": norm_url(s.url), "description": norm_ws(s.description), "spec": build_spec(s)}

    a, b = view(first), view(second)
    if a == b:
        return FieldCheck("reload_consistency", Status.PASS, "", "", "Values identical after reload")
    changed = [k for k in a if a[k] != b[k]]
    return FieldCheck("reload_consistency", Status.NEEDS_REVIEW, {k: a[k] for k in changed},
                      {k: b[k] for k in changed}, f"Values changed after reload: {changed}")
