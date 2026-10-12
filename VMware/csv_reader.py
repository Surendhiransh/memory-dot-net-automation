"""
CSV loading and inspection for the VMware/Broadcom Compatibility Guide
part-validation automation.

The source file (vmware_broadcom_playwright_no_dup.csv) is NEVER modified by
this project.
"""

import csv
import os
from collections import Counter

CSV_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "vmware_broadcom_playwright_no_dup.csv")

# Actual CSV column names (confirmed by inspection - do not guess/rename).
#
# NOTE: the source file was swapped (previously vmware_playwright.csv) for
# vmware_broadcom_playwright (1).csv, which renamed two columns:
#   - 'mfr_part_number'    is now physically named 'oem_part_number'
#   - 'part_specification' is now physically named 'part_specifications'
# The logical keys below (used throughout the rest of the project) are kept
# the same so no other module needs to change.
CSV_COLUMNS = {
    "category": "category",
    "oem": "oem",
    "part_number": "part_number",
    "dwpd": "DWPD",
    "form_factor": "form_factor",
    "capacity": "capacity",
    "mfr_part_number": "oem_part_number",
    "part_description": "part_description",
    "interface": "interface",
    "part_specification": "part_specifications",
    "part_url": "part_url",
    "store": "store",
}


def load_csv(path=CSV_PATH):
    """Load the CSV and return a list of row dicts, each tagged with its
    original 0-based row index (csv_row_index) for traceability."""
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        rows = []
        for i, row in enumerate(reader):
            row["csv_row_index"] = i
            rows.append(row)
    return rows


def print_csv_summary(rows, path=CSV_PATH):
    print(f"CSV FILE: {path}")
    if not rows:
        print("CSV is empty.")
        return
    columns = [c for c in rows[0].keys() if c != "csv_row_index"]
    print(f"COLUMNS ({len(columns)}): {columns}")
    print(f"TOTAL ROWS: {len(rows)}")

    cat_counter = Counter((r.get(CSV_COLUMNS["category"]) or "").strip().lower() for r in rows)
    print(f"UNIQUE CATEGORY VALUES: {dict(cat_counter)}")

    missing = [k for k, v in CSV_COLUMNS.items() if v not in columns and k not in ("oem", "store")]
    if missing:
        print(f"REQUESTED FIELDS NOT FOUND IN CSV: {missing}")
    else:
        print("All requested fields are present in the CSV.")


def get_rows_by_category(rows, category):
    category = category.strip().lower()
    col = CSV_COLUMNS["category"]
    return [r for r in rows if (r.get(col) or "").strip().lower() == category]


if __name__ == "__main__":
    rows = load_csv()
    print_csv_summary(rows)
