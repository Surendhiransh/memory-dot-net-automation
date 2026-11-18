import pandas as pd
import re

input_csv = "asrock_db_import.csv"
output_csv = "module_capacity_mb_report.csv"


def convert_capacity_to_mb(capacity):
    if pd.isna(capacity):
        return None

    capacity = str(capacity).strip().upper()

    match = re.match(r"^(\d+)\s*(GB|TB)$", capacity)
    if not match:
        return None

    value = int(match.group(1))
    unit = match.group(2)

    if unit == "GB":
        return value * 1024

    if unit == "TB":
        return value * 1024 * 1024

    return None


def parse_actual_mb(actual_module_capacity_mb):
    """Return module_capacity_mb as an int, or None if it is not a whole number."""
    try:
        value = float(str(actual_module_capacity_mb).strip())
    except (TypeError, ValueError):
        return None
    if value != value or not value.is_integer():  # NaN or fractional MB
        return None
    return int(value)


def validate_row(capacity, actual_module_capacity_mb):
    """
    Validate one row. Returns (expected_mb, actual_mb, status, issue_reason).

    A row is Verified only when both values parse and are equal. Any value
    that cannot be parsed is reported as an Issue, never as Verified.
    """
    expected_mb = convert_capacity_to_mb(capacity)

    if pd.isna(actual_module_capacity_mb) or str(actual_module_capacity_mb).strip() == "":
        return expected_mb, "", "Issue", "module_capacity_mb missing"

    actual_mb = parse_actual_mb(actual_module_capacity_mb)

    reasons = []
    if expected_mb is None:
        reasons.append(f"capacity unparseable: {capacity!r}")
    if actual_mb is None:
        reasons.append(f"module_capacity_mb unparseable: {actual_module_capacity_mb!r}")
    if reasons:
        return expected_mb, actual_mb, "Issue", "; ".join(reasons)

    if expected_mb == actual_mb:
        return expected_mb, actual_mb, "Verified", ""
    return expected_mb, actual_mb, "Issue", "module_capacity_mb mismatch"


def build_report(df):
    results = []
    for index, row in df.iterrows():
        capacity = row.get("capacity")
        expected_mb, actual_mb, status, issue_reason = validate_row(
            capacity, row.get("module_capacity_mb")
        )
        results.append({
            "Row Index": index,
            "capacity": capacity,
            "Expected module_capacity_mb": expected_mb,
            "Actual module_capacity_mb": actual_mb,
            "Status": status,
            "Issue Reason": issue_reason
        })
    return pd.DataFrame(results)


if __name__ == "__main__":
    report = build_report(pd.read_csv(input_csv))
    report.to_csv(output_csv, index=False)
    print(f"Validation report saved to: {output_csv}")
    print(report["Status"].value_counts().to_string())
