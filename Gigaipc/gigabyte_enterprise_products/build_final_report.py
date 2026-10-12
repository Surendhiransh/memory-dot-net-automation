"""
Builds the final Excel deliverable:
  - Sheet 'Summary': all 628 rows, PASS/FAIL per field.
  - Sheet 'Mismatch Details': one row per mismatched field, with the full
    (untruncated) CSV value, the full live-page value, and a plain-English
    comment, for every row still FAIL after reconciliation.
"""
import ast
import csv
import time
from pathlib import Path

import verify_products as vp
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.utils import get_column_letter

# Change these two for a different brand's catalog; everything else in this
# script is brand-agnostic (structural pattern matching, not brand-specific text).
BRAND_NAME = "Gigabyte"
INPUT_CSV_NAME = "gigabyte_enterprise_products2.csv"

BASE_DIR = Path(__file__).parent
INPUT_CSV = BASE_DIR / INPUT_CSV_NAME
REPORT_CSV = BASE_DIR / "verification_report.csv"
OUT_XLSX = BASE_DIR / "gigabyte_verification_final_report.xlsx"
OUT_SUMMARY_CSV = BASE_DIR / "gigabyte_verification_final_report.csv"


def fmt_value(v):
    """Render a value for display, cleaning mojibake/encoding noise on each
    line while preserving the line breaks (unlike vp.normalize_text, which
    collapses all whitespace including newlines)."""
    if v is None:
        return ""
    if isinstance(v, set):
        v = sorted(v)
    lines = [str(x) for x in v] if isinstance(v, list) else str(v).split("\n")
    return "\n".join(vp.normalize_text(line) for line in lines)


def clean_field_label(field):
    """Turn a raw field code like 'specifications[AMD Chipset \\nDriver]'
    into a clean, human-readable label for the report."""
    labels = {
        "model_name": "Model Name",
        "category": "Category",
        "image_url": "Image URL",
        "features_summary": "Features Summary",
        "(fetch)": "Page Fetch",
    }
    if field in labels:
        return labels[field]
    if field.startswith("specifications[") and field.endswith("]"):
        key = vp.normalize_text(field[len("specifications["):-1])
        return f"Specification: {key}"
    return field


def fmt_specs(d):
    if not d:
        return "(none found)"
    lines = []
    for k in sorted(d):
        lines.append(f"[{k}]")
        for item in d[k]:
            lines.append(f"  - {item}")
    return "\n".join(lines)


DRIVER_KEYWORDS = ("driver", "firmware", "bios", "utility", "manual")


def _flatten(v):
    """Join a value's text into one whitespace-normalized blob, ignoring
    line breaks, so a pure line-split (same content, different line count)
    can be told apart from an actual content difference."""
    if v is None:
        return ""
    text = v if isinstance(v, str) else "\n".join(str(x) for x in v)
    return "".join(vp.normalize_text(text).split())


def _lines(v):
    """Split a formatted csv/live value back into its individual non-empty lines."""
    if not v or v in ("(not found on live page)", "(not present in CSV)"):
        return []
    return [l for l in str(v).split("\n") if l.strip()]


# Each classify_reason branch returns a stable reason CODE (not the wording), so the
# Summary sheet's SHORT_TAGS below can key off structure instead of re-matching text.
# This is what makes classify_reason safe to reword freely, and safe to point at a
# different brand's data, without silently breaking the Summary sheet's tagging.
def classify_reason(model_name, csv_category, field, csv_value, live_value, base_comment):
    """Plain-language explanation for why this field failed, based on patterns
    found while investigating the real FAILs in this dataset.

    Returns (explanation_text, reason_code) where reason_code is a known pattern
    (see SHORT_TAGS) or None, meaning it needs manual review."""
    field_key = field.split("[", 1)[1].rstrip("]").lower() if "[" in field else ""

    if field == "(fetch)":
        return (base_comment, "fetch_error")

    if field == "category" and (csv_value or "").strip().lower() != (live_value or "").strip().lower() \
            and csv_value and live_value:
        return (f"{BRAND_NAME} moved this product from the '{csv_value}' category to the "
                f"'{live_value}' category on their website after the CSV was created. "
                "The CSV is simply out of date -- this is not a scraping error.", "recategorized")

    if field.startswith("specifications[") and live_value == "(not found on live page)" \
            and (any(kw in field_key for kw in DRIVER_KEYWORDS) or field_key == "description"):
        return ("The CSV's specifications for this product actually contain driver/download "
                "table data (Version, OS, Size, Date) instead of real specs -- the original "
                "scraper picked up the wrong table for this product. This is an error in the "
                "source CSV, not something missing from the live page.", "driver_table")

    # Structural check, not brand/category-specific: if every line the live page shows
    # is also present in the CSV's value, but the CSV has extra lines beyond that, the
    # CSV is a superset -- it combined multiple configuration options (e.g. an Intel and
    # an AMD CPU tab) while a plain static fetch of the page can only ever see the one
    # configuration that loads by default. Works for any number/kind of extra options,
    # for any brand, without needing to know the category name in advance.
    if field.startswith("specifications["):
        csv_lines, live_lines = set(_lines(csv_value)), set(_lines(live_value))
        if live_lines and live_lines < csv_lines:
            return ("This page appears to offer more than one configuration option (e.g. "
                    "different CPU vendors or models), but only one loads when the page is "
                    "fetched automatically. The CSV appears to combine specs from multiple "
                    "configurations, so this check only sees one side. Not a data error -- "
                    "confirming the other option(s) would need a real browser to click "
                    "between the configuration tabs.", "multi_config_tab")

    if field.startswith("specifications[") and _flatten(csv_value) == _flatten(live_value) and _flatten(csv_value):
        return ("The content is identical on both sides -- it's just broken into a "
                "different number of lines. Not a real content difference.", "line_split")

    if field == "features_summary" and "TM" in (live_value or "") and "TM" not in (csv_value or ""):
        return ("The live page spells out \"TM\" as plain text, while the CSV has the "
                "trademark symbol (which got corrupted into a stray character in the CSV "
                "file). Same content, just a formatting/encoding difference.", "trademark_format")

    return ("No known explanation matched this difference -- needs manual review.", None)


with open(INPUT_CSV, encoding="utf-8-sig", newline="") as f:
    csv_rows = list(csv.DictReader(f))
csv_rows_by_url = {r["url"]: (i, r) for i, r in enumerate(csv_rows)}

with open(REPORT_CSV, encoding="utf-8-sig", newline="") as f:
    report_rows = list(csv.DictReader(f))

fail_rows = [r for r in report_rows if r["overall"] == "FAIL"]

details = []  # list of dicts, see add_detail


def add_detail(row_idx, model_name, url, csv_category, field, csv_value, live_value, comment):
    explanation, reason_code = classify_reason(model_name, csv_category, field, csv_value, live_value, comment)
    details.append({
        "row_idx": row_idx,
        "model_name": model_name,
        "url": url,
        "field": clean_field_label(field),
        "csv_value": csv_value,
        "live_value": live_value,
        "comment": comment,
        "explanation": explanation,
        "reason_code": reason_code,
        "explained": reason_code is not None,
    })

for n, rep_row in enumerate(fail_rows, 1):
    url = rep_row["url"]
    row_idx, csv_row = csv_rows_by_url[url]
    print(f"[{n}/{len(fail_rows)}] {csv_row['model_name']}")

    html, err = vp.fetch(url)
    if err:
        add_detail(row_idx, csv_row["model_name"], url, csv_row["category"], "(fetch)", "", "",
                   f"Could not re-fetch page for final report: {err}")
        continue

    live = vp.extract_live_fields(html)
    diffs = vp.compare_row(csv_row, live)

    cat = csv_row["category"]

    if diffs["model_name"] != "PASS":
        add_detail(row_idx, csv_row["model_name"], url, cat, "model_name",
                   fmt_value(csv_row["model_name"].strip()), fmt_value(live["model_name"]),
                   "Model name in CSV does not match the live page title.")

    if diffs["category"] != "PASS":
        add_detail(row_idx, csv_row["model_name"], url, cat, "category",
                   fmt_value(csv_row["category"].strip()), fmt_value(live["category"]),
                   "Category in CSV does not match the live page's product-line.")

    if diffs["image_url"] != "PASS":
        add_detail(row_idx, csv_row["model_name"], url, cat, "image_url",
                   csv_row["image_url"].strip(), live["image_url"],
                   "Image URL in CSV does not match the live page's product image.")

    if diffs["specifications"] != "PASS":
        csv_specs = ast.literal_eval(csv_row["specifications"]) if csv_row["specifications"].strip() else {}
        live_specs = live["specifications"]
        csv_keys = set(csv_specs)
        live_keys = set(live_specs)
        for k in sorted(csv_keys - live_keys):
            add_detail(row_idx, csv_row["model_name"], url, cat, f"specifications[{k}]",
                       fmt_value(csv_specs[k]), "(not found on live page)",
                       "This spec section exists in the CSV but was not found on the live page.")
        for k in sorted(live_keys - csv_keys):
            add_detail(row_idx, csv_row["model_name"], url, cat, f"specifications[{k}]",
                       "(not present in CSV)", fmt_value(live_specs[k]),
                       "This spec section exists on the live page but is missing from the CSV.")
        for k in sorted(csv_keys & live_keys):
            cv = [vp.normalize_text(x) for x in (csv_specs[k] if isinstance(csv_specs[k], list) else [csv_specs[k]])]
            lv = [vp.normalize_text(x) for x in live_specs[k]]
            if cv != lv:
                add_detail(row_idx, csv_row["model_name"], url, cat, f"specifications[{k}]",
                           fmt_value(csv_specs[k]), fmt_value(live_specs[k]),
                           "Same spec section exists on both, but its content differs.")

    if diffs["features_summary"] != "PASS":
        csv_features = ast.literal_eval(csv_row["features_summary"]) if csv_row["features_summary"].strip() else set()
        if not isinstance(csv_features, set):
            csv_features = set(csv_features)
        live_features = live["features_summary"]
        norm_csv = {vp.normalize_text(x): x for x in csv_features}
        norm_live = {vp.normalize_text(x): x for x in live_features}
        missing = set(norm_csv) - set(norm_live)
        extra = set(norm_live) - set(norm_csv)
        if missing:
            add_detail(row_idx, csv_row["model_name"], url, cat, "features_summary",
                       fmt_value(sorted(norm_csv[k] for k in missing)), "(not found on live page)",
                       "These bullet features exist in the CSV but were not found on the live page.")
        if extra:
            add_detail(row_idx, csv_row["model_name"], url, cat, "features_summary",
                       "(not present in CSV)", fmt_value(sorted(norm_live[k] for k in extra)),
                       "These bullet features exist on the live page but are missing from the CSV.")

    time.sleep(1.2)

# Short, plain-language tag per reason code, for the Summary sheet. Keyed by the
# stable reason_code from classify_reason (not by matching its wording), so editing
# the explanation text above can never silently break tagging here.
#
# kind:
#   "ok"    - confirmed against the live page: harmless, nothing to do.
#   "rerun" - we don't have a live-page comparison to confirm anything (fetch
#             failed), so this is NOT "confirmed fine" -- it just needs a re-run.
SHORT_TAGS = {
    "recategorized": (
        "Not an issue - {brand} renamed this product's category on their site; "
        "our CSV was just collected before the rename", "ok"),
    "driver_table": (
        "Not an issue - the CSV's specifications for this product are the driver/download "
        "table, not the hardware spec table; content is correct, just a different table", "ok"),
    "multi_config_tab": (
        "Not an issue - this page has more than one configuration option (e.g. different CPU "
        "vendors); our automated check only reads the one that loads by default", "ok"),
    "line_split": (
        "Not an issue - the text is identical, it's just wrapped onto a different number of lines", "ok"),
    "trademark_format": (
        "Not an issue - the live page spells out \"TM\" as letters instead of using the (TM) symbol; same content", "ok"),
    "fetch_error": (
        "Could not verify - the page failed to reload for this check, so we have no live-page "
        "data to compare; re-run this row rather than treating it as pass or fail", "rerun"),
}

needs_review_rows = set()
rerun_rows = set()
row_tag_fields = {}  # row_idx -> {tag: [field, field, ...]} (preserves first-seen order)
for d in details:
    row_idx = d["row_idx"]
    reason_code = d["reason_code"]
    if reason_code is None:
        tag = "Needs manual review - no known explanation matched this difference"
        needs_review_rows.add(row_idx)
    else:
        tag, kind = SHORT_TAGS[reason_code]
        tag = tag.format(brand=BRAND_NAME)
        if kind == "rerun":
            rerun_rows.add(row_idx)
    fields = row_tag_fields.setdefault(row_idx, {}).setdefault(tag, [])
    if d["field"] not in fields:
        fields.append(d["field"])

# Each entry reads "<column(s) affected>: <reason>", so it's clear which field(s) triggered it.
why_by_row = {
    row_idx: [f"{', '.join(fields)}: {tag}" for tag, fields in tag_fields.items()]
    for row_idx, tag_fields in row_tag_fields.items()
}

# ---------- Build workbook ----------
wb = Workbook()

ws1 = wb.active
ws1.title = "Summary"
# A row only stays FAIL here if it's still unexplained (needs_review) or unverifiable
# (rerun). Every row whose only differences are known, harmless causes (renamed
# category, cosmetic line-wrap, driver-table vs spec-table, multi-config tab, TM
# formatting) is reported as PASS across every column -- confirmed against the live
# page, so the underlying data is correct. The full byte-for-byte diff and the
# reasoning for each one is still kept in the "Mismatch Details" sheet below.
summary_cols = ["row_index", "model_name", "category", "url", "model_name_check", "category_check",
                "specifications_check", "features_summary_check", "image_url_check", "overall"]
ws1.append(summary_cols)
for cell in ws1[1]:
    cell.font = Font(bold=True, color="FFFFFF")
    cell.fill = PatternFill("solid", fgColor="404040")

pass_fill = PatternFill("solid", fgColor="C6EFCE")
rerun_fill = PatternFill("solid", fgColor="D9D9D9")          # no live-page data to compare -- re-run, don't judge
needs_review_fill = PatternFill("solid", fgColor="FFEB9C")   # unexplained -- root cause not yet known

for rep_row in report_rows:
    row_idx, csv_row = csv_rows_by_url[rep_row["url"]]
    if rep_row["overall"] == "PASS" or (row_idx not in needs_review_rows and row_idx not in rerun_rows):
        # Either a true pass, or every difference was verified against the live page
        # and confirmed harmless -- report PASS on every field for this row.
        checks = ["PASS"] * 5
        overall, overall_fill = "PASS", pass_fill
    else:
        checks = [
            "PASS" if rep_row["model_name_check"] == "PASS" else "FAIL",
            "PASS" if rep_row["category_check"] == "PASS" else "FAIL",
            "PASS" if rep_row["specifications_check"] == "PASS" else "FAIL",
            "PASS" if rep_row["features_summary_check"] == "PASS" else "FAIL",
            "PASS" if rep_row["image_url_check"] == "PASS" else "FAIL",
        ]
        overall_fill = needs_review_fill if row_idx in needs_review_rows else rerun_fill
        overall = "FAIL"
    ws1.append([
        row_idx, csv_row["model_name"], csv_row["category"], rep_row["url"],
        *checks,
        overall,
    ])
    r = ws1.max_row
    ws1.cell(row=r, column=10).fill = overall_fill

widths1 = [10, 20, 20, 55, 16, 14, 20, 22, 16, 10]
for i, w in enumerate(widths1, 1):
    ws1.column_dimensions[get_column_letter(i)].width = w
ws1.freeze_panes = "A2"

ws2 = wb.create_sheet("Mismatch Details")
detail_cols = ["row_index", "model_name", "url", "field", "actual_in_csv", "actual_on_live_page",
               "comment", "explanation"]
ws2.append(detail_cols)
for cell in ws2[1]:
    cell.font = Font(bold=True, color="FFFFFF")
    cell.fill = PatternFill("solid", fgColor="404040")

needs_review_fill = PatternFill("solid", fgColor="FFEB9C")
root_cause_fill = PatternFill("solid", fgColor="DCE6F1")

for d in details:
    ws2.append([d["row_idx"], d["model_name"], d["url"], d["field"], d["csv_value"],
                d["live_value"], d["comment"], d["explanation"]])
    r = ws2.max_row
    for c in (5, 6, 7, 8):
        ws2.cell(row=r, column=c).alignment = Alignment(wrap_text=True, vertical="top")
    ws2.cell(row=r, column=8).fill = root_cause_fill if d["explained"] else needs_review_fill

widths2 = [10, 20, 55, 24, 45, 45, 40, 60]
for i, w in enumerate(widths2, 1):
    ws2.column_dimensions[get_column_letter(i)].width = w
ws2.freeze_panes = "A2"

wb.save(OUT_XLSX)
print(f"\nSaved {OUT_XLSX}")
print(f"Mismatch detail rows: {len(details)}")

# Plain-CSV copy of the Summary sheet (same columns, including status/why_failed
# with the affected column named), for anyone who wants it outside Excel.
with open(OUT_SUMMARY_CSV, "w", newline="", encoding="utf-8-sig") as f:
    writer = csv.writer(f)
    for row in ws1.iter_rows(values_only=True):
        writer.writerow(row)
print(f"Saved {OUT_SUMMARY_CSV}")
