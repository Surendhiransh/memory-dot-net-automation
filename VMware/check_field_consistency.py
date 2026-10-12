"""
Field-consistency checker: cross-validates each flat CSV column against the
SAME data already scraped into that row's part_specifications JSON blob -
no browser/Playwright needed, since part_specifications already holds what
would otherwise have to be scraped live from the "Model Details" grid.

Column <-> part_specifications key mapping (matches the mapping already
used for live-page scraping in main.py's FIELD_WEBSITE_LABEL):

    part_description  <-> Model
    oem                <-> Partner Name
    oem_part_number    <-> Part Number
    part_number        <-> Product Id
    capacity           <-> Capacity
    interface          <-> Interface Speed
    form_factor        <-> Form Factor
    DWPD               <-> DWPD

Special cases:
    category - has no specifications counterpart; validated against the
               Part URL's 'program' query parameter instead.
    store    - has no specifications/URL counterpart; validated against
               the fixed expected default 'VMware'.
    part_url - not validated itself (it is the row identifier / the source
               of the category check).
    part_specifications - besides the 8 mapped keys above, every other key
               (VID, DID, SVID, SSID, Endurance, ...) has no flat-column
               counterpart to compare against, so it is only shown for
               reference; keys whose value is the placeholder '-' (or any
               other empty spelling) are skipped/omitted, not reported as
               a mismatch.

Reuses field_matcher.py's existing per-field comparison functions (the
same ones main.py uses for the live scrape) instead of reimplementing
normalization/comparison rules.

Usage:
    python check_field_consistency.py                     (default source CSV)
    python check_field_consistency.py "<path to csv>"     (any other source CSV)

Output:
    vmware_field_consistency_report.csv   - plain data (no formatting possible)
    vmware_field_consistency_report.xlsx  - same data, coloured headers/results
"""

import csv
import os
import sys
from urllib.parse import urlparse, parse_qs

import csv_reader
import field_matcher as fm
import report_generator as rg
import row_level_validator as rlv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SOURCE_CSV = csv_reader.CSV_PATH  # default; overridden by the first command-line argument
REPORT_CSV = os.path.join(BASE_DIR, "vmware_field_consistency_report.csv")
REPORT_XLSX = os.path.join(BASE_DIR, "vmware_field_consistency_report.xlsx")

# Long-text report columns that get a wider column in the Excel report.
WIDE_REPORT_COLUMNS = (
    "Part URL", "Comments", "CSV Part Specifications",
    "Actual Part Specifications", "Part Specifications Comments",
)

STORE_DEFAULT = "VMware"

# logical CSV_COLUMNS key -> (part_specifications key, field_matcher compare fn)
FIELD_SPEC_MAP = {
    "part_description": ("Model", fm.compare_description),
    "oem": ("Partner Name", fm.compare_exact_normalized),
    "mfr_part_number": ("Part Number", fm.compare_exact_normalized),
    "part_number": ("Product Id", fm.compare_exact_normalized),
    "capacity": ("Capacity", fm.compare_capacity),
    "interface": ("Interface Speed", fm.compare_interface),
    "form_factor": ("Form Factor", fm.compare_form_factor),
    "dwpd": ("DWPD", fm.compare_dwpd),
}

REPORT_COLUMNS = [
    "csv_row_index",
    "Part URL",
    "Expected Part Description", "Actual Part Description (spec: Model)", "Part Description Result",
    "Expected OEM", "Actual OEM (spec: Partner Name)", "OEM Result",
    "Expected OEM Part Number", "Actual OEM Part Number (spec: Part Number)", "OEM Part Number Result",
    "Expected Part Number", "Actual Part Number (spec: Product Id)", "Part Number Result",
    "Expected Capacity", "Actual Capacity (spec: Capacity)", "Capacity Result",
    "Expected Interface", "Actual Interface (spec: Interface Speed)", "Interface Result",
    "Expected Form Factor", "Actual Form Factor (spec: Form Factor)", "Form Factor Result",
    "Expected DWPD", "Actual DWPD (spec: DWPD)", "DWPD Result",
    "Expected Category", "Actual Category (from Part URL)", "Category Result",
    "Expected Store", "Actual Store (default)", "Store Result",
    "Overall Result",
    "Comments",
    "CSV Part Specifications",
    "Actual Part Specifications",
    "Part Specifications Comments",
]


def extract_category_from_url(url):
    if fm.is_empty(url):
        return ""
    try:
        query = parse_qs(urlparse(url).query)
    except ValueError:
        return ""
    values = query.get("program") or []
    return values[0] if values else ""


def compare_category(expected, url):
    actual = extract_category_from_url(url)
    if fm.is_empty(expected):
        return fm.RESULT_NOT_APPLICABLE, actual, "No expected value in CSV."
    if fm.is_empty(actual):
        return fm.RESULT_NOT_FOUND, actual, "No 'program' query parameter in Part URL."
    if fm.normalize_text_ci(expected) == fm.normalize_text_ci(actual):
        return fm.RESULT_MATCHED, actual, ""
    return fm.RESULT_UNMATCHED, actual, ""


def compare_store(expected):
    if fm.is_empty(expected):
        return fm.RESULT_NOT_FOUND, "Store column is empty."
    if fm.normalize_text_ci(expected) == fm.normalize_text_ci(STORE_DEFAULT):
        return fm.RESULT_MATCHED, ""
    return fm.RESULT_UNMATCHED, f"Expected default '{STORE_DEFAULT}'."


def build_csv_part_specifications(row):
    """The specification dict IMPLIED by the flat CSV columns, keyed by
    the same spec-label names used in part_specifications, so it can be
    displayed/compared side by side with the actual scraped blob."""
    return {
        spec_key: fm.display_value(row.get(csv_reader.CSV_COLUMNS[logical_col], ""))
        for logical_col, (spec_key, _) in FIELD_SPEC_MAP.items()
    }


def build_actual_part_specifications(spec_dict):
    """The full actual part_specifications dict (every key), with
    placeholder ('-', empty, etc.) values omitted."""
    if not spec_dict:
        return {}
    result = {}
    for key, value in spec_dict.items():
        first_value = rlv.spec_first_value(value)
        if fm.is_empty(first_value):
            continue
        result[key] = fm.display_value(first_value)
    return result


def resolve_both_empty_as_matched(col_result, expected, actual):
    """field_matcher's compare_* functions return NOT APPLICABLE whenever
    the CSV (expected) side is empty, without checking whether the
    part_specifications (actual) side is also empty. Here, CSV vs.
    part_specifications is a same-row consistency check, so both sides
    being empty/N-A is not an issue - it should read as MATCHED, not
    NOT APPLICABLE."""
    if col_result == fm.RESULT_NOT_APPLICABLE and fm.is_empty(actual):
        return fm.RESULT_MATCHED
    return col_result


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


def check_row(row):
    spec_raw = row.get(csv_reader.CSV_COLUMNS["part_specification"])
    spec_dict = rlv.safe_parse_spec(spec_raw) or {}
    url = row.get(csv_reader.CSV_COLUMNS["part_url"], "")

    result = {"csv_row_index": row["csv_row_index"], "Part URL": url}
    field_results = []
    comments = []

    label_map = {
        "part_description": "Part Description",
        "oem": "OEM",
        "mfr_part_number": "OEM Part Number",
        "part_number": "Part Number",
        "capacity": "Capacity",
        "interface": "Interface",
        "form_factor": "Form Factor",
        "dwpd": "DWPD",
    }
    mapped_spec_keys = set()
    spec_key_results = {}  # spec_key -> (col_result, expected_display, actual_display)

    for logical_col, (spec_key, compare_fn) in FIELD_SPEC_MAP.items():
        label = label_map[logical_col]
        mapped_spec_keys.add(spec_key)
        expected = row.get(csv_reader.CSV_COLUMNS[logical_col], "")
        actual = rlv.spec_first_value(spec_dict.get(spec_key))

        col_result, comment = compare_fn(expected, actual)
        col_result = resolve_both_empty_as_matched(col_result, expected, actual)
        expected_display = fm.display_value(expected)
        actual_display = fm.display_value(actual)
        result[f"Expected {label}"] = expected_display
        result[f"Actual {label} (spec: {spec_key})"] = actual_display
        result[f"{label} Result"] = col_result
        field_results.append(col_result)
        spec_key_results[spec_key] = (col_result, expected_display, actual_display)
        if col_result not in (fm.RESULT_MATCHED, fm.RESULT_NOT_APPLICABLE):
            detail = comment or f"expected='{expected_display}', actual='{actual_display}'"
            comments.append(f"{label} ({col_result}): {detail}")

    cat_expected = row.get(csv_reader.CSV_COLUMNS["category"], "")
    cat_result, cat_actual, cat_comment = compare_category(cat_expected, url)
    cat_result = resolve_both_empty_as_matched(cat_result, cat_expected, cat_actual)
    result["Expected Category"] = fm.display_value(cat_expected)
    result["Actual Category (from Part URL)"] = fm.display_value(cat_actual)
    result["Category Result"] = cat_result
    field_results.append(cat_result)
    if cat_result not in (fm.RESULT_MATCHED, fm.RESULT_NOT_APPLICABLE):
        detail = cat_comment or f"expected='{fm.display_value(cat_expected)}', actual='{fm.display_value(cat_actual)}'"
        comments.append(f"Category ({cat_result}): {detail}")

    store_expected = row.get(csv_reader.CSV_COLUMNS["store"], "")
    store_result, store_comment = compare_store(store_expected)
    result["Expected Store"] = fm.display_value(store_expected)
    result["Actual Store (default)"] = STORE_DEFAULT
    result["Store Result"] = store_result
    field_results.append(store_result)
    if store_result != fm.RESULT_MATCHED:
        detail = store_comment or f"expected='{fm.display_value(store_expected)}', default='{STORE_DEFAULT}'"
        comments.append(f"Store ({store_result}): {detail}")

    result["Overall Result"] = compute_overall_result(field_results)
    result["Comments"] = " | ".join(comments) if comments else "All applicable fields matched."

    result["CSV Part Specifications"] = str(build_csv_part_specifications(row))
    result["Actual Part Specifications"] = str(build_actual_part_specifications(spec_dict))

    spec_total = len(spec_key_results)
    spec_matched = sum(1 for r, _, _ in spec_key_results.values() if r == fm.RESULT_MATCHED)
    spec_comment = f"{spec_matched}/{spec_total} specification keys matched"
    spec_mismatches = [
        f"{key} (csv='{exp}', actual='{act}', result={r})"
        for key, (r, exp, act) in spec_key_results.items() if r != fm.RESULT_MATCHED
    ]
    if spec_mismatches:
        spec_comment += " | Mismatched: " + "; ".join(spec_mismatches)
    result["Part Specifications Comments"] = spec_comment

    return result


def run(source_csv=SOURCE_CSV):
    print("=" * 80)
    print("FIELD CONSISTENCY CHECK (flat CSV columns vs part_specifications)")
    print("=" * 80)
    print(f"Source CSV: {source_csv}")

    rows = csv_reader.load_csv(path=source_csv)  # reuse existing loader with the chosen file
    print(f"Total rows: {len(rows)}")

    results = [check_row(row) for row in rows]

    with open(REPORT_CSV, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=REPORT_COLUMNS)
        writer.writeheader()
        for r in results:
            writer.writerow({k: r.get(k, "") for k in REPORT_COLUMNS})

    # Same rows as the CSV, written to Excel with coloured headers/results.
    # "C2" freezes the header row plus csv_row_index and Part URL columns.
    rg.write_styled_excel_report(
        results, REPORT_COLUMNS, REPORT_XLSX,
        sheet_title="Field Consistency",
        wide_columns=WIDE_REPORT_COLUMNS,
        freeze_panes="C2",
    )

    from collections import Counter
    overall_counts = Counter(r["Overall Result"] for r in results)

    print("\nSUMMARY:")
    for key in ("MATCHED", "PARTIAL MATCH", "UNMATCHED", "NOT FOUND", "NOT APPLICABLE"):
        print(f"  {key}: {overall_counts.get(key, 0)}")
    print(f"\nReport written to: {REPORT_CSV}")
    print(f"Excel report written to: {REPORT_XLSX}")


if __name__ == "__main__":
    # Optional first argument = source CSV path; otherwise use the default.
    run(sys.argv[1] if len(sys.argv) > 1 else SOURCE_CSV)
