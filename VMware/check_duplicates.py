"""
Standalone duplicate-row checker for vmware_broadcom_playwright (1).csv.

Reuses the existing duplicate-detection logic in row_level_validator.py
(entire row compared, Part URL excluded) - does not reimplement it.

Usage:
    python check_duplicates.py

Output:
    vmware_duplicate_rows_report.csv - one row per DUPLICATE record (full
    original columns + group id), sorted by group so duplicate sets sit
    together. Non-duplicate rows are not included in this report.
"""

import csv
import os

import csv_reader
import row_level_validator as rlv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DUPLICATES_REPORT_CSV = os.path.join(BASE_DIR, "vmware_duplicate_rows_report.csv")


def run():
    print("=" * 80)
    print("DUPLICATE-ROW CHECK")
    print("=" * 80)
    print(f"Source CSV: {csv_reader.CSV_PATH}")

    rows = csv_reader.load_csv()
    fieldnames = [c for c in rows[0].keys()] if rows else []
    print(f"Total rows: {len(rows)}")

    dup_info = rlv.find_duplicates(rows, fieldnames)

    duplicate_rows = [row for row in rows if dup_info[row["csv_row_index"]]["is_duplicate"]]
    duplicate_rows.sort(key=lambda row: (
        dup_info[row["csv_row_index"]]["group_id"],
        row["csv_row_index"],
    ))

    group_count = len({dup_info[r["csv_row_index"]]["group_id"] for r in duplicate_rows})

    print(f"Duplicate rows found: {len(duplicate_rows)}")
    print(f"Duplicate groups:     {group_count}")
    print(f"Unique (non-duplicate) rows: {len(rows) - len(duplicate_rows)}")

    output_columns = [c for c in fieldnames if c != "csv_row_index"]
    output_columns = ["csv_row_index", "Duplicate Group Id", "Duplicate Of Row(s)"] + output_columns

    with open(DUPLICATES_REPORT_CSV, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=output_columns)
        writer.writeheader()
        for row in duplicate_rows:
            info = dup_info[row["csv_row_index"]]
            out_row = dict(row)
            out_row["Duplicate Group Id"] = info["group_id"]
            out_row["Duplicate Of Row(s)"] = ", ".join(str(i) for i in info["duplicate_of"])
            writer.writerow({k: out_row.get(k, "") for k in output_columns})

    print(f"\nDuplicate rows report written to: {DUPLICATES_REPORT_CSV}")


if __name__ == "__main__":
    run()
