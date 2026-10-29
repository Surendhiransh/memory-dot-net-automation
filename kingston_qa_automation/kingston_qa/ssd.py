"""SSD part validation.

Purpose : the flow for each server page:
  1. open host_url, find each SSD card (data-category "Solid-State Drives") and its "Learn more" link
  2. open the detail page with the variant selected (?partnum=<part>) and read the part number,
     selected capacity / second option, datasheet, first thumbnail, heading and spec table
     (each variant is loaded once and cached, because many server pages list the same SSD)
  3. compare every column; the comment states any rule used (inch marks, image size, ...)
  4. reload the server page and the detail page once and confirm nothing changed
Usage   : python -m pytest tests/test_kingston_ssd.py --row-delay 10 --reports-dir reports/ssd
"""
from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, urlsplit

from playwright.sync_api import TimeoutError as PlaywrightTimeout

from .browser import KingstonPage
from .comparators import compare_spec, norm_ws, parse_json
from .models import Status
from .parts import (CARD_COLUMNS, EXTRACT_PARTS_JS, PAGE_COLUMNS, PartCheck, PartResult, PartRow, PartsPage,
                    PartStatus, _blocked_comment, _check_page_columns, _find_card, _ordered, _skipped_checks,
                    _store_check, category_key, compare_value, overall_status)
from .validators import norm_url

log = logging.getLogger("kingston_qa.ssd")

DETAIL_READY = "div.s-ecommerce__details__heading h1"
DETAIL_COLUMNS = ("part_number", "capacity", "form_factor", "datasheet_url", "part_image_url",
                  "part_description", "part_specification")
_INCH = re.compile(r'["”″]')  # straight / curly / double-prime inch marks
_RENDITION = re.compile(r"-(tn|sm|md|lg|zm-lg|zm)(?=\.\w+$)", re.I)

DETAIL_JS = r"""
() => {
  const clean = s => (s || '').replace(/\s+/g, ' ').trim();
  const radios = [...document.querySelectorAll('.s-ecommerce__details__selections input[type=radio]')];
  const selected = {};
  radios.filter(r => r.checked).forEach(r => { selected[r.dataset.name] = r.dataset.value; });
  const firstFeature = document.querySelector('.s-ecommerce__details__features.ecommerce-features li');
  const thumb = document.querySelector('div.c-imageCarousel__thumbnails button img');
  const ds = document.querySelector('a.link-item.datasheet-link');
  // Cell text with <br> as a space; works for hidden tabs too (innerText does not).
  const cellText = (td, dropSup) => {
    const c = td.cloneNode(true);
    if (dropSup) c.querySelectorAll('sup').forEach(e => e.remove());  // footnote markers are not part of the label
    c.querySelectorAll('br').forEach(br => br.replaceWith(' '));
    c.querySelectorAll('p, li, div').forEach(e => e.append(' '));
    return clean(c.textContent);
  };
  const tables = [...document.querySelectorAll('div.c-table__main table')].map(t => [...t.querySelectorAll('tr')].map(tr => {
    const cells = tr.querySelectorAll('td');
    return cells.length < 2 ? null : [cellText(cells[0], true), cellText(cells[1], false)];
  }).filter(Boolean));
  // Spec tables are tabbed per form factor; use the one matching the selected form factor.
  const ff = s => (s || '').replace(/["”″\s]/g, '').toLowerCase();
  const formFactorOf = rows => (rows.find(r => /^form factor$/i.test(r[0])) || [])[1];
  const pick = tables.findIndex(rows => selected['Form Factor'] && ff(formFactorOf(rows)) === ff(selected['Form Factor']));
  const spec = tables[pick >= 0 ? pick : 0] || [];
  return {
    url: location.href,
    h1: clean(document.querySelector('div.s-ecommerce__details__heading h1')?.innerText) || null,
    selected,
    capacity: selected['Capacity'] || null,
    form_factor: selected['Form Factor'] || null,
    options: radios.map(r => [r.dataset.name, r.dataset.value]),
    part_number_text: firstFeature ? clean(firstFeature.innerText) : null,
    datasheet_url: ds ? ds.href : null,
    part_image_url: thumb ? new URL(thumb.getAttribute('src'), location.href).href : null,
    spec,
  };
}
"""


def _no_inch(value: str | None) -> str:
    return norm_ws(_INCH.sub("", value or ""))


def _image_key(url: str | None) -> str:
    """Image identity without its size rendition suffix (-tn, -sm, -zm-lg ...)."""
    return _RENDITION.sub("", urlsplit(url or "").path).lower()


def second_option(d: dict) -> tuple[str | None, str | None]:
    """(value, option name) of the selected non-capacity option: 'Form Factor', else e.g. 'Heatsink' / encryption."""
    selected = d.get("selected") or ({"Form Factor": d["form_factor"]} if d.get("form_factor") else {})
    name = "Form Factor" if "Form Factor" in selected else next((k for k in selected if k != "Capacity"), None)
    value = selected.get(name) if name else None
    # An option labelled "None" (e.g. Security Features: None) means no value; the CSV leaves it blank.
    return (None if (value or "").strip().lower() == "none" else value), name


def detail_part_number(text: str | None) -> str | None:
    if not text:
        return None
    return norm_ws(re.sub(r"^\s*part\s*number\s*:\s*", "", text, flags=re.I)) or None


def spec_dict(rows: list[list[str]]) -> dict[str, list[str]]:
    spec: dict[str, list[str]] = {}
    for key, value in rows:
        spec.setdefault(key, []).append(value)
    return spec


# ---------------------------------------------------------------- variant cache
@dataclass
class Variant:
    ok: bool
    reason: str = ""
    data: dict | None = None
    reload_data: dict | None = None
    reload_reason: str = ""


class VariantCache:
    """Opens each SSD detail variant once (plus one reload) and shares the result across server pages."""

    def __init__(self, kp: KingstonPage, delay_s: float):
        self.kp, self.delay_s = kp, delay_s
        self._cache: dict[tuple[str, str], Variant] = {}

    def get(self, learn_more_url: str, row: PartRow) -> Variant:
        key = (norm_url(learn_more_url), row.get("part_number").upper())
        if key not in self._cache:
            self._cache[key] = self._fetch(learn_more_url, row)
            time.sleep(self.delay_s)
        return self._cache[key]

    def _fetch(self, learn_more_url: str, row: PartRow) -> Variant:
        pn = row.get("part_number")
        base = learn_more_url.split("?", 1)[0]
        nav = self.kp.open(f"{base}?partnum={quote(pn.lower(), safe='')}", DETAIL_READY)
        if not nav.ok:
            return Variant(False, nav.reason)
        data = self._settled(pn)
        if (detail_part_number(data["part_number_text"]) or "").upper() != pn.upper():
            data = self._select_by_options(row) or data
        time.sleep(min(self.delay_s, 3))
        reload_nav = self.kp.reload(DETAIL_READY)
        reload_data = self._settled(pn) if reload_nav.ok else None
        log.info("SSD variant %s -> %s (selected %s / %s)", pn, detail_part_number(data["part_number_text"]),
                 data["capacity"], data["form_factor"])
        return Variant(True, data=data, reload_data=reload_data, reload_reason=reload_nav.reason)

    def _settled(self, pn: str) -> dict:
        """The variant's part number and datasheet are filled in by JS after load; wait for them first."""
        try:
            self.kp.page.wait_for_function(
                """pn => {
                    const li = document.querySelector('.s-ecommerce__details__features.ecommerce-features li');
                    return li && li.textContent.toUpperCase().includes(pn)
                        && document.querySelector('a.link-item.datasheet-link');
                }""", arg=pn.upper(), timeout=10000)
        except PlaywrightTimeout:
            log.info("Detail page did not show %s with a datasheet link within 10s; reading as-is", pn)
        self.kp.page.wait_for_timeout(500)
        return self.kp.page.evaluate(DETAIL_JS)

    def _select_by_options(self, row: PartRow) -> dict | None:
        """Fallback when ?partnum= did not select the variant: click the capacity / form factor options."""
        page = self.kp.page
        for name, wanted in (("Form Factor", row.get("form_factor")), ("Capacity", row.get("capacity"))):
            for radio in page.locator(f".s-ecommerce__details__selections input[type=radio][data-name='{name}']").all():
                if _no_inch(radio.get_attribute("data-value")).lower() == _no_inch(wanted).lower():
                    page.locator(f"label[for='{radio.get_attribute('id')}']").click()
                    page.wait_for_timeout(2000)
                    break
        log.info("Selected %s / %s by clicking options for %s", row.get("capacity"), row.get("form_factor"),
                 row.get("part_number"))
        return page.evaluate(DETAIL_JS)


# ---------------------------------------------------------------- per-column checks
def _detail_checks(row: PartRow, d: dict) -> list[PartCheck]:
    checks = []
    site_pn = detail_part_number(d.get("part_number_text"))
    st, comment = compare_value(row.get("part_number"), site_pn, "part number")
    checks.append(PartCheck("part_number", row.get("part_number"), site_pn or "", st, comment))

    st, comment = compare_value(row.get("capacity"), d.get("capacity"), "selected capacity")
    if st is PartStatus.MATCHED:
        comment = "Both values are identical (capacity option selected on the detail page)."
    checks.append(PartCheck("capacity", row.get("capacity"), d.get("capacity") or "", st, comment))

    ff, ff_name = second_option(d)
    ff_check = _form_factor_check(row, ff)
    if ff_name is None:
        ff_check.comment += " Product has only a capacity option, so there is no form factor to select."
    elif ff_name != "Form Factor":
        ff_check.comment += f" Website value is the selected '{ff_name}' option (product has no 'Form Factor' option)."
    checks.append(ff_check)

    st, comment = compare_value(row.get("datasheet_url"), d.get("datasheet_url"), "datasheet link")
    checks.append(PartCheck("datasheet_url", row.get("datasheet_url"), d.get("datasheet_url") or "", st, comment))

    checks.append(_image_check(row, d.get("part_image_url")))
    checks.append(_description_check(row, d))
    checks.append(_spec_check(row, d.get("spec") or []))
    return checks


def _form_factor_check(row: PartRow, site: str | None) -> PartCheck:
    csv_ff = row.get("form_factor")
    st, comment = compare_value(csv_ff, site, "selected form factor")
    if st is PartStatus.MISMATCHED and _no_inch(csv_ff).lower() == _no_inch(site).lower():
        st, comment = PartStatus.MATCHED, f"Matched; only the inch mark differs (CSV '{csv_ff}', website '{site}')."
    elif st is PartStatus.MATCHED:
        comment = "Both values are identical (form factor option selected on the detail page)."
    return PartCheck("form_factor", csv_ff, site or "", st, comment)


def _image_check(row: PartRow, site: str | None) -> PartCheck:
    csv_img = row.get("part_image_url")
    st, comment = compare_value(csv_img, site, "first thumbnail image")
    if st is PartStatus.MISMATCHED and _image_key(csv_img) == _image_key(site):
        st, comment = PartStatus.MATCHED, ("Same product image; CSV stores the large '-zm-lg' rendition, "
                                           "website thumbnail button uses '-tn'.")
    elif st is PartStatus.MISMATCHED:
        comment += " These are different product images, not just different sizes."
    return PartCheck("part_image_url", csv_img, site or "", st, comment)


def _description_check(row: PartRow, d: dict) -> PartCheck:
    """CSV part_description = detail <h1> + selected capacity + selected second option (inch mark dropped)."""
    h1, cap = d.get("h1"), d.get("capacity")
    ff, _ = second_option(d)
    site = norm_ws(" ".join(x for x in (h1, cap, _no_inch(ff)) if x)) if h1 else None
    st, comment = compare_value(row.get("part_description"), site, "part description")
    if st is PartStatus.MATCHED:
        comment = f"Both values are identical (website = heading '{h1}' + selected capacity + form factor)."
    elif st is PartStatus.MISMATCHED:
        comment += f" Website value built from heading '{h1}' + selected capacity + form factor."
    return PartCheck("part_description", row.get("part_description"), site or "", st, comment)


def _spec_check(row: PartRow, rows: list[list[str]]) -> PartCheck:
    raw = row.get("part_specification")
    actual = spec_dict(rows)
    actual_text = json.dumps(actual, ensure_ascii=False)
    expected, err = parse_json(raw, dict)
    if err:
        return PartCheck("part_specification", raw, actual_text, PartStatus.UNMATCHED, f"CSV value cannot be compared: {err}.")
    if not actual:
        return PartCheck("part_specification", raw, "", PartStatus.UNMATCHED, "No specification table found on the detail page.")
    # SSD specs are flat {label: [value]}; wrap both sides as one section for the shared comparator.
    st, issues = compare_spec({"Specification": expected}, {"Specification": actual}, Status.FAIL)
    if st is Status.PASS:
        return PartCheck("part_specification", raw, actual_text, PartStatus.MATCHED,
                         f"All {len(actual)} specification rows are identical.")

    def strip(d: dict) -> dict:
        return {k: [_no_inch(v) for v in (vs if isinstance(vs, list) else [vs])] for k, vs in d.items()}

    st2, issues2 = compare_spec({"Specification": strip(expected)}, {"Specification": strip(actual)}, Status.FAIL)
    if st2 is Status.PASS:
        return PartCheck("part_specification", raw, actual_text, PartStatus.MATCHED,
                         f"All {len(actual)} specification rows match; only inch marks (\") differ.")
    return PartCheck("part_specification", raw, actual_text, PartStatus.MISMATCHED,
                     f"{len(issues2)} difference(s): " + "; ".join(issues2))


def _blank_description(row: PartRow) -> PartCheck:
    value = row.get("description")
    if value:
        return PartCheck("description", value, "N/A", PartStatus.MISMATCHED,
                         f"CSV shows '{value}', but SSD parts have no description (must be blank).")
    return PartCheck("description", "", "N/A", PartStatus.MATCHED, "Blank as expected; SSD parts have no description.")


def _part_url_check(row: PartRow, card: dict) -> PartCheck:
    site = card.get("learn_more_url")
    st, comment = compare_value(row.get("part_url"), site, "'Learn more' link")
    if site and row.get("part_url") and norm_url(row.get("part_url")) == norm_url(site):
        st, comment = PartStatus.MATCHED, "Both values are identical ('Learn more' link on the server page card)."
    return PartCheck("part_url", row.get("part_url"), site or "", st, comment)


def _reload_check(card, card_after, variant: Variant | None) -> PartCheck:
    if card_after is False:
        return PartCheck("reload_consistency", "", "", PartStatus.BLOCKED, "Server page reload was blocked; consistency not verified.")
    if variant is not None and variant.ok and variant.reload_data is None:
        return PartCheck("reload_consistency", "", "", PartStatus.BLOCKED,
                         f"Detail page reload was blocked; consistency not verified ({variant.reload_reason}).")
    changed = []
    if card != card_after:
        changed.append("server page card")
    if variant is not None and variant.ok and variant.data != variant.reload_data:
        changed += [f"detail '{k}'" for k in variant.data if variant.data.get(k) != variant.reload_data.get(k)]
    if changed:
        return PartCheck("reload_consistency", "First load", "After reload", PartStatus.MISMATCHED,
                         f"Values changed after reload: {', '.join(changed)}.")
    scope = "server page and detail page" if variant is not None and variant.ok else "server page"
    return PartCheck("reload_consistency", "First load", "After reload", PartStatus.MATCHED,
                     f"All extracted values are identical after reloading the {scope}.")


def _find_ssd_card(cards: list[dict], row: PartRow) -> tuple[dict | None, str]:
    """Card listing the part; else the card whose 'Learn more' page offers it as another variant."""
    card, _ = _find_card(cards, row.get("part_number"))
    if card is not None:
        return card, ""
    family = [c for c in cards if c.get("learn_more_url") and row.get("part_url")
              and norm_url(c["learn_more_url"]) == norm_url(row.get("part_url"))]
    if not family:
        return None, ""
    return family[0], (f"Part is a variant on the detail page of card '{family[0].get('part_number')}' "
                       f"(not listed as its own card on the server page).")


# ---------------------------------------------------------------- page runner
def validate_ssd_page(page: PartsPage, kp: KingstonPage, variants: VariantCache, shots_dir: Path) -> list[PartResult]:
    started = time.monotonic()
    slug = re.sub(r"[^A-Za-z0-9_-]+", "_", page.host_url.rsplit("/", 1)[-1])[:60]
    shots: list[str] = []

    def shoot(suffix: str) -> None:
        if (path := kp.screenshot(shots_dir / f"ssd_{slug}_{suffix}.png")):
            shots.append(path)

    # Step 1: open the server page; if unreachable every web-based column is BLOCKED.
    nav = kp.open(page.host_url)
    if not nav.ok:
        shoot("blocked")
        reason = _blocked_comment(nav.reason)
        results = []
        for row in page.rows:
            checks = [_store_check(row), _blank_description(row)] + _skipped_checks(row) + [
                PartCheck(c, row.get(c), "", PartStatus.BLOCKED, reason)
                for c in PAGE_COLUMNS + CARD_COLUMNS + ("capacity", "form_factor") if c != "description"]
            results.append(PartResult(row.row_no, row.get("server_description"), row.get("part_number"),
                                      page.host_url, PartStatus.BLOCKED, _ordered(checks), shots))
        log.warning("BLOCKED %s: %s", page.host_url, nav.reason)
        return results

    # Step 2: read every card (part number, category, 'Learn more' link), then reload once.
    first = kp.page.evaluate(EXTRACT_PARTS_JS)
    shoot("issues")  # captured now (no extra navigation); deleted below if every part matches
    reload_nav = kp.reload()
    second = kp.page.evaluate(EXTRACT_PARTS_JS) if reload_nav.ok else None

    results = []
    for row in page.rows:
        checks = [_store_check(row), _blank_description(row)] + _skipped_checks(row) + _check_page_columns(row, first)
        card, via = _find_ssd_card(first["cards"], row)
        card_after = _find_ssd_card(second["cards"], row)[0] if second else False
        variant = None
        if card is None:
            msg = f"Part number '{row.get('part_number')}' not found on the website."
            checks += [PartCheck(c, row.get(c), "", PartStatus.UNMATCHED, msg)
                       for c in ("category", "part_url") + DETAIL_COLUMNS]
        else:
            key, source = category_key(card)
            st, comment = compare_value(row.get("category"), key, "category")
            if st is PartStatus.MATCHED:
                comment = f"Both values are identical; website category taken from {source}."
            checks.append(PartCheck("category", row.get("category"), key or "", st, comment))
            part_url = _part_url_check(row, card)
            if via:
                part_url.comment += f" {via}"
            checks.append(part_url)
            if not card.get("learn_more_url"):
                checks += [PartCheck(c, row.get(c), "", PartStatus.UNMATCHED,
                                     "Card has no 'Learn more' link, so the detail page cannot be reached.")
                           for c in DETAIL_COLUMNS]
            else:
                # Step 3: open (or reuse from cache) the detail page with this variant selected.
                variant = variants.get(card["learn_more_url"], row)
                if variant.ok:
                    checks += _detail_checks(row, variant.data)
                else:
                    reason = "Detail page: " + _blocked_comment(variant.reason)
                    checks += [PartCheck(c, row.get(c), "", PartStatus.BLOCKED, reason) for c in DETAIL_COLUMNS]
        # Step 4: server page + detail page must give the same values after reload.
        checks.append(_reload_check(card, card_after, variant))
        results.append(PartResult(row.row_no, row.get("server_description"), row.get("part_number"), page.host_url,
                                  overall_status(checks, False), _ordered(checks), shots))

    if all(r.overall is PartStatus.MATCHED for r in results):
        for path in shots:
            Path(path).unlink(missing_ok=True)
        shots.clear()
    counts = {s.value: sum(r.overall is s for r in results) for s in PartStatus if any(r.overall is s for r in results)}
    log.info("%s -> %d SSD parts %s (%.1fs)", page.host_url, len(results), counts, time.monotonic() - started)
    return results
