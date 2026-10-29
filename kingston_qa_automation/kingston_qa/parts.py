"""Memory and USB part validation (also shared helpers for SSD).

Purpose : the flow for each server page:
  1. open host_url once and read every product card in one JavaScript call
  2. for each CSV row on that page, find its card by part number
  3. compare every column (category, part number, URLs, image, names, specification JSON, ...)
  4. reload once and confirm the card values did not change
  5. overall status per record: MISMATCHED > UNMATCHED > BLOCKED > MATCHED
Usage   : python -m pytest tests/test_kingston_parts.py                      (memory)
          KQA_PARTS_CATEGORY="datatraveler usb drives" python -m pytest tests/test_kingston_parts.py
              --parts-csv kingston_parts_30.csv                              (USB drives)
"""
from __future__ import annotations

import json
import logging
import re
import time
import zipfile
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from .browser import KingstonPage
from .comparators import compare_spec, norm_ws, parse_json
from .comparators import _squash  # whitespace-insensitive form, shared with the server rules
from .models import Status
from .validators import STORE_NAME, norm_url

log = logging.getLogger("kingston_qa.parts")


class PartStatus(str, Enum):
    MATCHED = "MATCHED"
    MISMATCHED = "MISMATCHED"
    UNMATCHED = "UNMATCHED"
    BLOCKED = "BLOCKED"
    SKIPPED = "SKIPPED"


PART_COLUMNS = ("store", "server_description", "host_url", "category", "part_number", "capacity", "form_factor",
                "part_url", "datasheet_url", "part_image_url", "part_description", "description",
                "part_specification", "status")
PAGE_COLUMNS = ("server_description", "host_url")  # validated from the page, not the card
CARD_COLUMNS = ("category", "part_number", "part_url", "datasheet_url", "part_image_url", "part_description",
                "description", "part_specification")
SKIPPED_COLUMNS = {"status": "Not checked (out of scope)."}
ATTR_COLUMNS = {"capacity": "data_capacity", "form_factor": "data_form_factor"}  # read from the card <li> attributes
# Site category label (data-category, else tab label) -> CSV category key.
CATEGORY_KEYS = {"system-specific memory": "memory", "memory": "memory", "solid-state drives": "ssd",
                 "ssd": "ssd", "datatraveler usb drives": "datatraveler usb drives"}
MEMORY_CARD_SELECTOR = "div.c-productCard4.c-productCard4--dram.c-productCard4--listView"

EXTRACT_PARTS_JS = """
() => {
  const clean = s => (s || '').replace(/\\s+/g, ' ').trim();
  const abs = v => { try { return v ? new URL(v, location.href).href : null; } catch (e) { return v; } };
  const tabLabel = el => {
    for (let n = el.parentElement; n; n = n.parentElement) {
      if (!n.id) continue;
      const id = CSS.escape(n.id);
      const t = document.querySelector(`[aria-controls="${id}"], [href="#${id}"], [data-target="#${id}"], [data-tab="${id}"]`);
      if (t) return clean(t.textContent);
    }
    return null;
  };
  const cards = [...document.querySelectorAll('div.c-productCard4')].map(card => {
    const li = card.closest('li');
    const dl = card.querySelector('ul.c-productCard4__details__downloads > li:first-child a[href]');
    const img = card.querySelector('.c-productCard4__image img');
    const learnMore = [...card.querySelectorAll('a[href]')].find(a => /^\\s*learn more\\s*$/i.test(a.textContent));
    return {
      learn_more_url: learnMore ? learnMore.href : null,
      // Capacity / form factor are card attributes when the site provides them (USB / SSD cards do; memory cards do not).
      data_capacity: clean(li?.getAttribute('data-capacity')) || null,
      data_form_factor: clean(li?.getAttribute('data-formfactor') || li?.getAttribute('data-form-factor')) || null,
      part_number: clean(card.querySelector('.c-productCard4__partNumber__value')?.textContent) || null,
      is_memory_card: card.matches('%s'),
      data_category: clean(li?.getAttribute('data-category') ?? card.getAttribute('data-category')) || null,
      tab_label: tabLabel(card),
      first_download_url: dl ? dl.href : null,
      first_download_is_datasheet: !!dl && (dl.classList.contains('downLoadPdf') || /\\.pdf($|[?#])/i.test(dl.href)),
      part_image_url: img ? abs(img.getAttribute('src')) : null,
      part_description: clean(card.querySelector('.c-productCard4__header__link__name')?.textContent) || null,
      description: clean(card.querySelector('.c-productCard4__details__shortDesc')?.textContent) || null,
      // Cards without a short description (e.g. USB drives) list it as bullets; part number / links excluded.
      long_description: [...card.querySelectorAll('.c-productCard4__details__content__longDesc li')]
        .filter(li => !li.className.includes('partNumber') && !li.querySelector('a'))
        .map(li => clean(li.textContent)).filter(Boolean).join('; ') || null,
    };
  });
  return {
    url: location.href,
    description: clean(document.querySelector('.l-inner h1.u-h3')?.textContent) || null,
    grid_found: !!document.querySelector('ul.s-productGallery3__grid.l-gridFlex.l-gridFlex--list.gallery-loadmore.DisableMode'),
    cards,
  };
}
""" % MEMORY_CARD_SELECTOR


# ---------------------------------------------------------------- data model
@dataclass
class PartRow:
    row_no: int
    data: dict[str, str]

    def get(self, column: str) -> str:
        value = self.data.get(column)
        return "" if value is None else str(value).strip()


@dataclass
class PartCheck:
    column: str
    csv_value: Any
    site_value: Any
    status: PartStatus
    comment: str


@dataclass
class PartResult:
    row_no: int
    server_description: str
    part_number: str
    host_url: str
    overall: PartStatus
    checks: list[PartCheck] = field(default_factory=list)
    screenshots: list[str] = field(default_factory=list)


@dataclass
class PartsPage:
    host_url: str
    rows: list[PartRow]


# ---------------------------------------------------------------- input
def load_part_rows(path: Path, category: str | None = "memory", start: int = 1, limit: int | None = None,
                   only: frozenset[int] = frozenset()) -> list[PartRow]:
    """Read CSV, or an ODS/XLSX workbook (even one saved with a .csv name), strictly read-only."""
    import pandas as pd  # local: only the parts flow needs pandas

    with path.open("rb") as fh:
        is_zip = fh.read(2) == b"PK"
    if is_zip:
        with zipfile.ZipFile(path) as zf:
            engine = "odf" if "mimetype" in zf.namelist() and b"opendocument" in zf.read("mimetype") else "openpyxl"
        df = pd.read_excel(path, engine=engine, dtype=str, keep_default_na=False)
    else:
        df = pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8-sig")
    missing = [c for c in PART_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"{path} is missing required columns: {missing}")
    rows = [PartRow(i, rec) for i, rec in enumerate(df.to_dict("records"), start=1)]
    if category:
        skipped = [r.row_no for r in rows if r.get("category").lower() != category]
        if skipped:
            log.info("Ignoring %d non-%s rows", len(skipped), category)
        rows = [r for r in rows if r.get("category").lower() == category]
    rows = [r for r in rows if r.row_no >= start and (not only or r.row_no in only)]
    return rows[:limit] if limit else rows


def group_by_page(rows: list[PartRow]) -> list[PartsPage]:
    pages: dict[str, PartsPage] = {}
    for row in rows:
        pages.setdefault(row.get("host_url"), PartsPage(row.get("host_url"), [])).rows.append(row)
    return list(pages.values())


# ---------------------------------------------------------------- comparison helpers
def _show(value: Any) -> str:
    return "(blank)" if value in (None, "", [], {}) else str(value)


def compare_value(csv_value: str, site_value: str | None, label: str) -> tuple[PartStatus, str]:
    exp, act = norm_ws(csv_value), norm_ws(site_value)
    if not exp and not act:
        return PartStatus.MATCHED, f"Both CSV and website have no {label}."
    if not act:
        return PartStatus.UNMATCHED, f"CSV shows '{exp}', but no {label} was found on the website."
    if not exp:
        return PartStatus.UNMATCHED, f"CSV {label} is blank, but the website shows '{act}'."
    if exp == act:
        return PartStatus.MATCHED, "Both values are identical."
    return PartStatus.MISMATCHED, f"CSV shows '{exp}', website shows '{act}'."


def category_key(card: dict) -> tuple[str | None, str]:
    """(CSV-style key, human description of where it came from)."""
    if card.get("data_category"):
        raw, source = card["data_category"], f"data-category '{card['data_category']}'"
    elif card.get("is_memory_card"):
        # Memory tabs are labelled per product line ('System-specific memory', 'Server Premier', ...),
        # so the DRAM card type is the reliable category signal when data-category is empty.
        tab = f", tab '{card['tab_label']}'" if card.get("tab_label") else ""
        raw, source = "memory", f"memory (DRAM) card type (data-category is empty{tab})"
    elif card.get("tab_label"):
        raw, source = card["tab_label"], f"tab label '{card['tab_label']}' (data-category is empty)"
    else:
        return None, "no data-category, tab label or memory card type"
    return CATEGORY_KEYS.get(raw.lower(), raw.lower()), source


def site_datasheet(card: dict) -> tuple[str | None, str]:
    url = card.get("first_download_url")
    if not url:
        return None, "Card has no downloads list."
    if not card.get("first_download_is_datasheet"):
        return None, f"First download link is not a datasheet ({url})."
    return url, ""


def card_description(card: dict) -> str | None:
    return card.get("description") or card.get("long_description")


def site_spec(card: dict, category: str | None) -> dict:
    return {"General": {"part_number": [card.get("part_number") or ""],
                        "part_description": [card.get("part_description") or ""],
                        "category": [category or ""]},
            "Description": {"description": [card_description(card) or ""]}}


# ---------------------------------------------------------------- per-column validation
def _check_page_columns(row: PartRow, snap: dict) -> list[PartCheck]:
    checks = []
    exp, act = row.get("server_description"), snap.get("description")
    st, comment = compare_value(exp, act, "server description")
    if st is PartStatus.MISMATCHED and _squash(exp) == _squash(act):
        st, comment = PartStatus.MATCHED, f"Matched; only spacing differs (CSV '{norm_ws(exp)}', website '{norm_ws(act)}')."
    checks.append(PartCheck("server_description", exp, act, st, comment))

    exp, act = row.get("host_url"), snap.get("url")
    if norm_url(exp) == norm_url(act):
        note = "" if exp.rstrip("/") == act.rstrip("/") else " (ignoring the site-added '?status=active')"
        checks.append(PartCheck("host_url", exp, act, PartStatus.MATCHED, f"Input URL equals browser URL{note}."))
    else:
        checks.append(PartCheck("host_url", exp, act, PartStatus.MISMATCHED,
                                f"CSV shows '{exp}', browser is on '{act}' (redirect?)."))
    return checks


def _category_check(row: PartRow, key: str | None, source: str) -> PartCheck:
    st, comment = compare_value(row.get("category"), key, "category")
    if st is PartStatus.MATCHED and key:
        comment = f"Both values are identical; website category taken from {source}."
    elif st is PartStatus.MISMATCHED:
        comment += f" Website category taken from {source}."
    return PartCheck("category", row.get("category"), key or "", st, comment)


def _spec_check(row: PartRow, card: dict, key: str | None) -> PartCheck:
    expected, err = parse_json(row.get("part_specification"), dict)
    actual_text = json.dumps(site_spec(card, key), ensure_ascii=False)
    if err:
        return PartCheck("part_specification", row.get("part_specification"), actual_text,
                         PartStatus.UNMATCHED, f"CSV value cannot be compared: {err}.")
    st, issues = compare_spec(expected, site_spec(card, key), Status.FAIL)
    if st is Status.PASS:
        return PartCheck("part_specification", row.get("part_specification"), actual_text, PartStatus.MATCHED,
                         "All 4 fields (part number, part description, category, description) are identical.")
    return PartCheck("part_specification", row.get("part_specification"), actual_text, PartStatus.MISMATCHED,
                     "Differences: " + "; ".join(issues))


def _check_card_columns(row: PartRow, card: dict, page_url: str) -> list[PartCheck]:
    key, source = category_key(card)
    checks = [_category_check(row, key, source)]

    st, comment = compare_value(row.get("part_number"), card.get("part_number"), "part number")
    checks.append(PartCheck("part_number", row.get("part_number"), card.get("part_number") or "", st, comment))

    datasheet, ds_note = site_datasheet(card)
    expected = datasheet or page_url
    rule = "the datasheet URL" if datasheet else "the current page URL (no datasheet on website)"
    csv_part = row.get("part_url")
    if not csv_part:
        st, comment = PartStatus.UNMATCHED, f"CSV part URL is blank; website rule gives {rule}."
    elif norm_url(csv_part) == norm_url(expected):  # ignores the site-added ?status=active
        st, comment = PartStatus.MATCHED, f"Both values are identical; part URL should be {rule}."
    else:
        st, comment = PartStatus.MISMATCHED, f"CSV shows '{csv_part}', but the part URL should be {rule} '{expected}'."
    checks.append(PartCheck("part_url", csv_part, expected, st, comment))

    st, comment = compare_value(row.get("datasheet_url"), datasheet, "datasheet URL")
    if ds_note and st is not PartStatus.MISMATCHED:
        comment = f"{comment} {ds_note}"
    checks.append(PartCheck("datasheet_url", row.get("datasheet_url"), datasheet or "", st, comment))

    for column, label in (("part_image_url", "image URL"), ("part_description", "part description")):
        st, comment = compare_value(row.get(column), card.get(column), label)
        checks.append(PartCheck(column, row.get(column), card.get(column) or "", st, comment))
    desc = card_description(card)
    st, comment = compare_value(row.get("description"), desc, "short description")
    if not card.get("description") and desc:
        comment += " Website value = the card's description bullets joined with '; ' (no short-description element)."
    checks.append(PartCheck("description", row.get("description"), desc or "", st, comment))
    checks.append(_spec_check(row, card, key))
    return checks


def _unit_free(value: str | None) -> str:
    """'128GB' and '128' compare equal (data-capacity carries no unit)."""
    return re.sub(r"\s*gb$", "", norm_ws(value).lower())


def _attr_checks(row: PartRow, card: dict) -> list[PartCheck]:
    """Capacity / form factor: compare the CSV with the card attribute; a blank CSV only matches a blank website."""
    checks = []
    for column, key in ATTR_COLUMNS.items():
        label, attr = column.replace("_", " "), key.replace("data_", "data-").replace("_", "")
        csv_value, site = row.get(column), card.get(key)
        st, comment = compare_value(csv_value, site, label)
        if st is PartStatus.MISMATCHED and _unit_free(csv_value) == _unit_free(site):
            st, comment = PartStatus.MATCHED, f"Both values are identical (CSV '{csv_value}', card {attr} '{site}'; GB unit implied)."
        elif site:
            comment += f" Website value from the card's {attr} attribute."
        else:
            comment += f" The card has no {attr} attribute."
        checks.append(PartCheck(column, csv_value, site or "", st, comment))
    return checks


def _store_check(row: PartRow) -> PartCheck:
    st, comment = compare_value(row.get("store"), STORE_NAME, "store")
    if st is PartStatus.MATCHED:
        comment = f"Both values are identical (fixed value '{STORE_NAME}')."
    return PartCheck("store", row.get("store"), STORE_NAME, st, comment)


def _skipped_checks(row: PartRow) -> list[PartCheck]:
    return [PartCheck(c, row.get(c), "", PartStatus.SKIPPED, why) for c, why in SKIPPED_COLUMNS.items()]


def overall_status(checks: list[PartCheck], page_blocked: bool) -> PartStatus:
    statuses = {c.status for c in checks}
    if page_blocked:
        return PartStatus.BLOCKED
    for status in (PartStatus.MISMATCHED, PartStatus.UNMATCHED, PartStatus.BLOCKED):
        if status in statuses:
            return status
    return PartStatus.MATCHED


def _ordered(checks: list[PartCheck]) -> list[PartCheck]:
    order = {c: i for i, c in enumerate(PART_COLUMNS + ("reload_consistency",))}
    return sorted(checks, key=lambda c: order.get(c.column, 99))


def _find_card(cards: list[dict], part_number: str) -> tuple[dict | None, int]:
    matches = [c for c in cards if norm_ws(c.get("part_number")).upper() == norm_ws(part_number).upper()]
    matches.sort(key=lambda c: not c.get("is_memory_card"))  # prefer memory (DRAM) list-view cards
    return (matches[0] if matches else None), len(matches)


def validate_parts_page(page: PartsPage, kp: KingstonPage, shots_dir: Path) -> list[PartResult]:
    """Open the page once, validate every part row on it, then reload once to confirm consistency."""
    started = time.monotonic()
    nav = kp.open(page.host_url)
    slug = re.sub(r"[^A-Za-z0-9_-]+", "_", page.host_url.rsplit("/", 1)[-1])[:60]
    shots: list[str] = []

    def shoot(suffix: str) -> None:
        if (path := kp.screenshot(shots_dir / f"parts_{slug}_{suffix}.png")):
            shots.append(path)

    results: list[PartResult] = []
    # Step 1: page not reachable -> card and page columns are BLOCKED; fixed/blank columns still checked.
    if not nav.ok:
        shoot("blocked")
        reason = _blocked_comment(nav.reason)
        for row in page.rows:
            checks = [_store_check(row)] + _skipped_checks(row) + [
                PartCheck(c, row.get(c), "", PartStatus.BLOCKED, reason) for c in PAGE_COLUMNS + CARD_COLUMNS + tuple(ATTR_COLUMNS)]
            results.append(PartResult(row.row_no, row.get("server_description"), row.get("part_number"),
                                      page.host_url, PartStatus.BLOCKED, _ordered(checks), shots))
        log.warning("BLOCKED %s: %s", page.host_url, nav.reason)
        return results

    # Step 2: read every card on the page in one call, then reload once for the consistency check.
    first = kp.page.evaluate(EXTRACT_PARTS_JS)
    if not first["grid_found"]:
        log.info("Memory list grid selector not present on %s; matching cards by part number", page.host_url)
    reload_nav = kp.reload()
    second = kp.page.evaluate(EXTRACT_PARTS_JS) if reload_nav.ok else None

    # Step 3: for each CSV row, find its card by part number and compare every column.
    for row in page.rows:
        checks = [_store_check(row)] + _skipped_checks(row) + _check_page_columns(row, first)
        card, count = _find_card(first["cards"], row.get("part_number"))
        if card is None:
            msg = f"Part number '{row.get('part_number')}' not found on the website."
            checks += [PartCheck(c, row.get(c), "", PartStatus.UNMATCHED, msg) for c in CARD_COLUMNS + tuple(ATTR_COLUMNS)]
        else:
            checks += _check_card_columns(row, card, first["url"]) + _attr_checks(row, card)
            if count > 1:
                log.info("Part %s appears %d times on %s; validated the first card", row.get("part_number"), count, page.host_url)
        # Step 4: reload consistency, then the record's overall status.
        checks.append(_reload_check(row, card, first, second, reload_nav.reason))
        overall = overall_status(checks, False)
        results.append(PartResult(row.row_no, row.get("server_description"), row.get("part_number"),
                                  page.host_url, overall, _ordered(checks), shots))

    if any(r.overall is not PartStatus.MATCHED for r in results):
        shoot("issues")
    counts = {s.value: sum(r.overall is s for r in results) for s in PartStatus if any(r.overall is s for r in results)}
    log.info("%s -> %d parts %s (%.1fs)", page.host_url, len(results), counts, time.monotonic() - started)
    return results


def _reload_check(row: PartRow, card: dict | None, first: dict, second: dict | None, reload_reason: str) -> PartCheck:
    if second is None:
        return PartCheck("reload_consistency", "", "", PartStatus.BLOCKED,
                         f"Reload was blocked, so consistency could not be verified ({reload_reason}).")
    again, _ = _find_card(second["cards"], row.get("part_number"))
    before = {"page": norm_ws(first.get("description")), "card": card}
    after = {"page": norm_ws(second.get("description")), "card": again}
    if before == after:
        return PartCheck("reload_consistency", "First load", "After reload", PartStatus.MATCHED,
                         "All extracted values are identical after reloading the page.")
    changed = [k for k in (before["card"] or {}) if (after["card"] or {}).get(k) != (before["card"] or {}).get(k)]
    if before["page"] != after["page"]:
        changed.append("server_description")
    return PartCheck("reload_consistency", json.dumps(before, ensure_ascii=False), json.dumps(after, ensure_ascii=False),
                     PartStatus.MISMATCHED, f"Values changed after reload: {', '.join(changed) or 'card presence'}.")


def _blocked_comment(reason: str) -> str:
    low = reason.lower()
    if "cloudflare" in low:
        return f"Cloudflare verification prevented validation ({reason})."
    if "captcha" in low:
        return "CAPTCHA prevented validation (requires a human)."
    if "timeout" in low:
        return f"Page timed out, so validation was not possible ({reason})."
    return f"Website was not accessible ({reason})."
