"""
VMware / Broadcom Compatibility Guide part-data validation automation.

Reads vmware_playwright.csv (NEVER modified), randomly samples SSD/HDD
records, opens each part_url in Chromium via Playwright, extracts the
"Model Details" fields from the live page, compares them against the CSV,
and produces an Excel + CSV report with per-field and summary results.

Modules:
    csv_reader.py       - CSV loading/inspection
    sample_selector.py  - reproducible random sampling
    page_validator.py   - Playwright navigation, CAPTCHA detection, extraction
    field_matcher.py    - normalization + comparison per field type
    report_generator.py - Excel/CSV report writing
"""

import os
import sys
import traceback

from playwright.sync_api import sync_playwright

import csv_reader
import field_matcher as fm
import page_validator as pv
import report_generator as rg
import row_level_validator as rlv
import sample_selector

# ---------------------------------------------------------------------------
# CONFIGURATION - smoke test values. Change to 25/25 only after the 4-record
# smoke test succeeds end to end.
# ---------------------------------------------------------------------------
TEST_COUNT_SSD = 25
TEST_COUNT_HDD = 25
RANDOM_SEED = 42
HEADLESS = False

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Report filenames encode the sample composition (total / SSD / HDD counts)
# so a run's scope is identifiable from the filename alone.
_TOTAL_COUNT = TEST_COUNT_SSD + TEST_COUNT_HDD
_REPORT_BASENAME = f"vmware_part_validation_result_{_TOTAL_COUNT}_{TEST_COUNT_SSD}SSD_{TEST_COUNT_HDD}HDD"
REPORT_XLSX = os.path.join(BASE_DIR, f"{_REPORT_BASENAME}.xlsx")
REPORT_CSV = os.path.join(BASE_DIR, f"{_REPORT_BASENAME}.csv")

# Duplicate-row + Part URL validation report: runs over the ENTIRE source
# CSV (not just the sampled subset) as a fast, browser-free pre-check.
DUPLICATE_URL_REPORT_CSV = os.path.join(BASE_DIR, "vmware_duplicate_and_url_validation_report.csv")

# CSV column (logical name) -> label shown in the "Model Details" grid on the
# live broadcom.com product page. Confirmed by direct DOM inspection of two
# sample pages (one SSD, one HDD) before writing this mapping.
#
# NOTE: the naming is NOT symmetric with the CSV column names:
#   - CSV 'part_number'     matches the website's 'Product Id' field
#   - CSV 'mfr_part_number' matches the website's 'Part Number' field
#   - CSV 'part_description' matches the website's 'Model' field
#   - CSV 'interface'        matches the website's 'Interface Speed' field
FIELD_WEBSITE_LABEL = {
    "part_number": "Product Id",
    "dwpd": "DWPD",
    "form_factor": "Form Factor",
    "capacity": "Capacity",
    "mfr_part_number": "Part Number",
    "part_description": "Model",
    "interface": "Interface Speed",
}


def print_selector_strategy():
    print("\nSELECTOR STRATEGY (confirmed via live DOM inspection):")
    print(f"  Row selector : {pv.MODEL_DETAILS_ROW_SELECTOR}")
    print("  Label        : row.locator('b').first.inner_text()  (bold label text, e.g. 'Model: ')")
    print("  Value        : row.locator('div.text-size-md').first.inner_text()")
    print("  Rationale    : rows are matched by their accessible label text, not")
    print("                 position/nth-child or generated class hashes, so field")
    print("                 reordering on the site will not break extraction.")
    print("\nFIELD -> WEBSITE LABEL MAPPING:")
    for k, v in FIELD_WEBSITE_LABEL.items():
        print(f"  {k:<20} -> {v}")


def compute_overall_result(field_results):
    applicable = [r for r in field_results if r != fm.RESULT_NOT_APPLICABLE]
    if not applicable:
        return fm.RESULT_NOT_APPLICABLE
    matched = sum(1 for r in applicable if r == fm.RESULT_MATCHED)
    if matched == len(applicable):
        return fm.RESULT_MATCHED
    if matched == 0:
        if all(r == fm.RESULT_NOT_FOUND for r in applicable):
            return fm.RESULT_NOT_FOUND
        return fm.RESULT_UNMATCHED
    return "PARTIAL MATCH"


def validate_record(page, sno, row):
    category = (row.get(csv_reader.CSV_COLUMNS["category"]) or "").strip()
    url = (row.get(csv_reader.CSV_COLUMNS["part_url"]) or "").strip()
    part_number_expected = row.get(csv_reader.CSV_COLUMNS["part_number"], "")
    dwpd_expected = row.get(csv_reader.CSV_COLUMNS["dwpd"], "")
    form_factor_expected = row.get(csv_reader.CSV_COLUMNS["form_factor"], "")
    capacity_expected = row.get(csv_reader.CSV_COLUMNS["capacity"], "")
    mfr_part_expected = row.get(csv_reader.CSV_COLUMNS["mfr_part_number"], "")
    description_expected = row.get(csv_reader.CSV_COLUMNS["part_description"], "")
    interface_expected = row.get(csv_reader.CSV_COLUMNS["interface"], "")
    spec_expected_raw = row.get(csv_reader.CSV_COLUMNS["part_specification"], "")

    result = {c: "" for c in rg.MAIN_COLUMNS}
    result["S.No"] = sno
    result["Category"] = category
    result["Part URL"] = url
    result["Part Number"] = fm.display_value(part_number_expected)
    result["Expected DWPD"] = fm.display_value(dwpd_expected)
    result["Expected Form Factor"] = fm.display_value(form_factor_expected)
    result["Expected Capacity"] = fm.display_value(capacity_expected)
    result["Expected MFR Part No"] = fm.display_value(mfr_part_expected)
    result["Expected Part Description"] = fm.display_value(description_expected)
    result["Expected Interface"] = fm.display_value(interface_expected)
    result["Expected Part Specification"] = fm.display_value(spec_expected_raw)
    comments = []

    nav = pv.open_part_page(page, url)
    if not nav["load_ok"]:
        result["Page Status"] = f"PAGE ERROR: {nav['error']}"
        result["Overall Result"] = "ERROR"
        result["Comments"] = "Page failed to load; skipped field validation."
        return result

    result["Page Status"] = f"HTTP {nav['status']} | {nav['final_url']} | title={nav['title']!r}"

    if pv.detect_human_verification(page):
        resolved = pv.wait_for_manual_verification(page, category, part_number_expected, url)
        if not resolved:
            result["Overall Result"] = "BLOCKED"
            result["Comments"] = "Human verification could not be completed."
            return result
        nav = pv.open_part_page(page, url)
        if not nav["load_ok"]:
            result["Page Status"] = f"PAGE ERROR after verification: {nav['error']}"
            result["Overall Result"] = "ERROR"
            result["Comments"] = "Page failed to reload after verification."
            return result
        result["Page Status"] = f"HTTP {nav['status']} | {nav['final_url']} | title={nav['title']!r}"

    labels, found = pv.extract_model_details(page)

    if not found:
        result["Overall Result"] = "NOT FOUND"
        result["Comments"] = "Model Details block not found on the page."
        return result

    part_number_actual = labels.get(FIELD_WEBSITE_LABEL["part_number"])
    part_number_result, part_number_comment = fm.compare_exact_normalized(
        part_number_expected, part_number_actual)

    dwpd_actual = labels.get(FIELD_WEBSITE_LABEL["dwpd"])
    dwpd_result, dwpd_comment = fm.compare_dwpd(dwpd_expected, dwpd_actual)

    ff_actual = labels.get(FIELD_WEBSITE_LABEL["form_factor"])
    ff_result, ff_comment = fm.compare_form_factor(form_factor_expected, ff_actual)

    cap_actual = labels.get(FIELD_WEBSITE_LABEL["capacity"])
    cap_result, cap_comment = fm.compare_capacity(capacity_expected, cap_actual)

    mfr_actual = labels.get(FIELD_WEBSITE_LABEL["mfr_part_number"])
    mfr_result, mfr_comment = fm.compare_exact_normalized(mfr_part_expected, mfr_actual)

    desc_actual = labels.get(FIELD_WEBSITE_LABEL["part_description"])
    desc_result, desc_comment = fm.compare_description(description_expected, desc_actual)

    iface_actual = labels.get(FIELD_WEBSITE_LABEL["interface"])
    iface_result, iface_comment = fm.compare_interface(interface_expected, iface_actual)

    spec_result, spec_comment, _ = fm.compare_specification(spec_expected_raw, labels)

    result["_part_number_result"] = part_number_result
    result["Actual DWPD"] = fm.display_value(dwpd_actual)
    result["DWPD Result"] = dwpd_result
    result["Actual Form Factor"] = fm.display_value(ff_actual)
    result["Form Factor Result"] = ff_result
    result["Actual Capacity"] = fm.display_value(cap_actual)
    result["Capacity Result"] = cap_result
    result["Actual MFR Part No"] = fm.display_value(mfr_actual)
    result["MFR Part No Result"] = mfr_result
    result["Actual Part Description"] = fm.display_value(desc_actual)
    result["Part Description Result"] = desc_result
    result["Actual Interface"] = fm.display_value(iface_actual)
    result["Interface Result"] = iface_result
    result["Actual Part Specification"] = str({k: v for k, v in labels.items()})
    result["Part Specification Result"] = spec_result

    if part_number_comment:
        comments.append(f"Part Number: {part_number_comment}")
    for label, c in (("DWPD", dwpd_comment), ("Form Factor", ff_comment),
                      ("Capacity", cap_comment), ("MFR Part No", mfr_comment),
                      ("Part Description", desc_comment), ("Interface", iface_comment),
                      ("Part Specification", spec_comment)):
        if c:
            comments.append(f"{label}: {c}")

    overall = compute_overall_result([
        part_number_result, dwpd_result, ff_result, cap_result,
        mfr_result, desc_result, iface_result, spec_result,
    ])
    result["Overall Result"] = overall
    result["Comments"] = " | ".join(comments) if comments else "All applicable fields matched."
    return result


def run():
    print("=" * 80)
    print("STEP 1 - CSV INSPECTION")
    print("=" * 80)
    rows = csv_reader.load_csv()
    csv_reader.print_csv_summary(rows)

    print("\n" + "=" * 80)
    print("STEP 1B - DUPLICATE-ROW + PART URL VALIDATION (entire source CSV)")
    print("=" * 80)
    fieldnames = [c for c in rows[0].keys()] if rows else []
    row_level_results = rlv.process_rows(rows, fieldnames)
    rg.write_duplicate_url_csv_report(row_level_results, DUPLICATE_URL_REPORT_CSV)
    dup_count = sum(1 for r in row_level_results if r["Duplicate Row"] == rlv.DUPLICATE_YES)
    print(f"Duplicate rows found: {dup_count} / {len(row_level_results)}")
    print(f"Duplicate + Part URL validation report written to: {DUPLICATE_URL_REPORT_CSV}")

    print("\n" + "=" * 80)
    print(f"STEP 2 - SAMPLE SELECTION (SSD={TEST_COUNT_SSD}, HDD={TEST_COUNT_HDD}, seed={RANDOM_SEED})")
    print("=" * 80)
    selected, warnings = sample_selector.select_sample(rows, TEST_COUNT_SSD, TEST_COUNT_HDD, RANDOM_SEED)
    for w in warnings:
        print(f"WARNING: {w}")
    sample_selector.print_selected_samples(selected)

    print_selector_strategy()

    print("\n" + "=" * 80)
    print("STEP 3-9 - RUNNING VALIDATION")
    print("=" * 80)

    results = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        page = browser.new_page()
        for i, row in enumerate(selected, start=1):
            print(f"\n[{i}/{len(selected)}] {row.get(csv_reader.CSV_COLUMNS['category'])} "
                  f"{row.get(csv_reader.CSV_COLUMNS['part_number'])}")
            try:
                record_result = validate_record(page, i, row)
            except Exception:
                traceback.print_exc()
                record_result = {c: "" for c in rg.MAIN_COLUMNS}
                record_result["S.No"] = i
                record_result["Category"] = row.get(csv_reader.CSV_COLUMNS["category"], "")
                record_result["Part URL"] = row.get(csv_reader.CSV_COLUMNS["part_url"], "")
                record_result["Part Number"] = row.get(csv_reader.CSV_COLUMNS["part_number"], "")
                record_result["Overall Result"] = "ERROR"
                record_result["Comments"] = "Unhandled exception during validation; see console log."
            print(f"  -> Overall Result: {record_result.get('Overall Result')}")
            results.append(record_result)
        browser.close()

    print("\n" + "=" * 80)
    print("STEP 12/13 - REPORT GENERATION")
    print("=" * 80)
    rg.write_csv_report(results, REPORT_CSV)
    summary = rg.write_excel_report(results, REPORT_XLSX)
    print(f"CSV report written to:   {REPORT_CSV}")
    print(f"Excel report written to: {REPORT_XLSX}")

    print("\nSUMMARY:")
    print(f"  Total records tested: {summary['total']}")
    print(f"  SSD records tested:   {summary['ssd']}")
    print(f"  HDD records tested:   {summary['hdd']}")
    for key in ("MATCHED", "PARTIAL MATCH", "UNMATCHED", "NOT FOUND", "BLOCKED", "ERROR"):
        print(f"  {key}: {summary['overall'].get(key, 0)}")


if __name__ == "__main__":
    run()
