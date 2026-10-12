"""
Standalone Part URL validation checker for vmware_broadcom_playwright_no_dup.csv.

For every row, checks whether EVERY header/column value (all top-level
columns, plus every key inside the part_specifications dict) is present
in the Part URL - literally, as a case-insensitive substring of the full
URL text (not restricted to any single query parameter).

Exception: if a part_specifications key name is itself one of
URL_KEY_VALUE_SKIP_KEYS (e.g. 'SVID') AND that key name is found in the
Part URL, that key's VALUE is not required to also be found there.

Reuses the existing helpers in field_matcher.py and row_level_validator.py
(is_empty/normalize_text_ci, safe_parse_spec, key_token_in_url,
URL_KEY_VALUE_SKIP_KEYS) instead of reimplementing them.

Usage:
    python check_part_url_validation.py

Output:
    vmware_no_dup_part_url_validation_report.csv
"""

import csv
import os

import csv_reader
import row_level_validator as rlv
from field_matcher import is_empty, normalize_text_ci

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SOURCE_CSV = os.path.join(BASE_DIR, "vmware_broadcom_playwright_no_dup.csv")
REPORT_CSV = os.path.join(BASE_DIR, "vmware_no_dup_part_url_validation_report.csv")

RESULT_MATCHED = rlv.RESULT_MATCHED
RESULT_NOT_FOUND = rlv.RESULT_NOT_FOUND
RESULT_NOT_APPLICABLE = rlv.RESULT_NOT_APPLICABLE
RESULT_SKIPPED = rlv.RESULT_SKIPPED

# Top-level CSV header columns to show as their own Actual-value + Result
# pair in the report (excludes part_url itself, and part_specifications,
# whose many nested keys stay summarized in the "Fields ..." columns below
# rather than each getting its own pair of report columns).
HEADER_COLUMNS_FOR_REPORT = [
    "part_description", "oem", "oem_part_number", "part_number", "capacity",
    "interface", "form_factor", "DWPD", "category", "part_specifications", "store",
]

REPORT_COLUMNS = (
    ["csv_row_index", "Part URL", "Actual Text In Part URL (redirectFrom, decoded)"]
    + [col for c in HEADER_COLUMNS_FOR_REPORT for col in (c, f"{c} - In URL?")]
    + [
        "Overall Result",
        "Fields Not Found In URL",
        "Fields Skipped (key present in URL)",
        "Fields Not Applicable (empty in CSV)",
    ]
)


def validate_row_against_full_url(row):
    """Check every column (except Part URL) and every part_specifications
    key literally against the full Part URL text."""
    url = (row.get(csv_reader.CSV_COLUMNS["part_url"]) or "").strip()
    url_norm = normalize_text_ci(url)

    field_results = {}

    for column, value in row.items():
        if column in (csv_reader.CSV_COLUMNS["part_url"], "csv_row_index",
                      csv_reader.CSV_COLUMNS["part_specification"]):
            continue
        if is_empty(value):
            field_results[column] = (RESULT_NOT_APPLICABLE, "No value in CSV.")
        elif normalize_text_ci(value) in url_norm:
            field_results[column] = (RESULT_MATCHED, "")
        else:
            field_results[column] = (RESULT_NOT_FOUND, "Value not found in Part URL.")

    spec_raw = row.get(csv_reader.CSV_COLUMNS["part_specification"])
    spec_dict = rlv.safe_parse_spec(spec_raw)
    if spec_dict is None:
        if not is_empty(spec_raw):
            field_results["part_specifications"] = (
                RESULT_NOT_APPLICABLE, "Could not be parsed.")
    else:
        for key, raw_value in spec_dict.items():
            value = rlv.spec_first_value(raw_value)
            label = f"spec:{key}"
            if is_empty(value):
                continue  # placeholder value, nothing to check

            if key in rlv.URL_KEY_VALUE_SKIP_KEYS and rlv.key_token_in_url(key, url):
                field_results[label] = (
                    RESULT_SKIPPED,
                    f"'{key}' key present in Part URL; value check skipped "
                    f"per URL_KEY_VALUE_SKIP_KEYS.",
                )
                continue

            if normalize_text_ci(str(value)) in url_norm:
                field_results[label] = (RESULT_MATCHED, "")
            else:
                field_results[label] = (RESULT_NOT_FOUND, "Value not found in Part URL.")

    applicable = [r for r, _ in field_results.values() if r != RESULT_NOT_APPLICABLE]
    checked = [r for r in applicable if r != RESULT_SKIPPED]
    if not checked:
        overall = RESULT_NOT_APPLICABLE
    elif all(r == RESULT_MATCHED for r in checked):
        overall = RESULT_MATCHED
    elif all(r == RESULT_NOT_FOUND for r in checked):
        overall = RESULT_NOT_FOUND
    else:
        overall = "PARTIAL MATCH"

    not_found = [label for label, (r, _) in field_results.items() if r == RESULT_NOT_FOUND]
    skipped = [label for label, (r, _) in field_results.items() if r == RESULT_SKIPPED]
    not_applicable = [label for label, (r, _) in field_results.items() if r == RESULT_NOT_APPLICABLE]

    return {
        "field_results": field_results,
        "overall": overall,
        "not_found": not_found,
        "skipped": skipped,
        "not_applicable": not_applicable,
    }


def run():
    print("=" * 80)
    print("PART URL VALIDATION CHECK (all columns, literal full-URL match)")
    print("=" * 80)
    print(f"Source CSV: {SOURCE_CSV}")

    rows = csv_reader.load_csv(path=SOURCE_CSV)
    print(f"Total rows: {len(rows)}")

    results = []
    for row in rows:
        check = validate_row_against_full_url(row)
        field_results = check["field_results"]

        result = {
            "csv_row_index": row["csv_row_index"],
            "Part URL": row.get(csv_reader.CSV_COLUMNS["part_url"], ""),
            "Actual Text In Part URL (redirectFrom, decoded)": rlv.extract_redirect_from(
                row.get(csv_reader.CSV_COLUMNS["part_url"], "")),
            "Overall Result": check["overall"],
            "Fields Not Found In URL": ", ".join(check["not_found"]),
            "Fields Skipped (key present in URL)": ", ".join(check["skipped"]),
            "Fields Not Applicable (empty in CSV)": ", ".join(check["not_applicable"]),
        }
        for column in HEADER_COLUMNS_FOR_REPORT:
            result[column] = row.get(column, "")
            if column == "part_specifications":
                # Too many nested keys to give each its own report column;
                # per-key outcomes are already in the "Fields ..." columns
                # above (as 'spec:<key>').
                result[f"{column} - In URL?"] = "See spec:<key> entries in Fields columns"
            else:
                col_result, _ = field_results.get(column, (RESULT_NOT_APPLICABLE, ""))
                result[f"{column} - In URL?"] = col_result

        results.append(result)

    with open(REPORT_CSV, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=REPORT_COLUMNS)
        writer.writeheader()
        for r in results:
            writer.writerow({k: r.get(k, "") for k in REPORT_COLUMNS})

    from collections import Counter
    overall_counts = Counter(r["Overall Result"] for r in results)
    skipped_rows = sum(1 for r in results if r["Fields Skipped (key present in URL)"])

    print("\nSUMMARY:")
    for key in ("MATCHED", "PARTIAL MATCH", "NOT FOUND", "NOT APPLICABLE"):
        print(f"  {key}: {overall_counts.get(key, 0)}")
    print(f"  Rows with at least one skipped key (e.g. SVID): {skipped_rows}")
    print(f"\nReport written to: {REPORT_CSV}")


if __name__ == "__main__":
    run()
