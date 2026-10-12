"""
Normalization and comparison functions for each validated field type.

Result values used throughout: MATCHED, UNMATCHED, NOT FOUND, NOT APPLICABLE.
"""

import ast
import re

EMPTY_TOKENS = {"", "nan", "none", "null", "-", "n/a", "na"}

RESULT_MATCHED = "MATCHED"
RESULT_UNMATCHED = "UNMATCHED"
RESULT_NOT_FOUND = "NOT FOUND"
RESULT_NOT_APPLICABLE = "NOT APPLICABLE"


def is_empty(value):
    if value is None:
        return True
    text = str(value).strip().lower()
    return text in EMPTY_TOKENS


def display_value(value, placeholder="N/A"):
    """Clean value for report display: blanks and raw placeholder tokens
    from the source data (e.g. the literal text 'nan') are shown as a
    single consistent 'N/A' instead of leaking the raw token."""
    if is_empty(value):
        return placeholder
    return str(value).strip()


def normalize_whitespace(value):
    """Collapse whitespace (incl. non-breaking space, tabs, newlines)."""
    if value is None:
        return ""
    text = str(value)
    text = text.replace(" ", " ")  # non-breaking space
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def normalize_text_ci(value):
    return normalize_whitespace(value).lower()


# ---------------------------------------------------------------------------
# Part Number / MFR Part No (exact, whitespace/case-insensitive, no digit loss)
# ---------------------------------------------------------------------------

def compare_exact_normalized(expected, actual):
    if is_empty(expected):
        return RESULT_NOT_APPLICABLE, "No expected value in CSV."
    if is_empty(actual):
        return RESULT_NOT_FOUND, "Field not found on the webpage."

    exp_n = normalize_text_ci(expected)
    act_n = normalize_text_ci(actual)

    # mfr_part_number can contain several comma-separated values on the site;
    # treat as a match if the expected value is one of the listed values, or
    # the full normalized strings match.
    if exp_n == act_n:
        return RESULT_MATCHED, ""

    exp_parts = {p.strip() for p in exp_n.split(",") if p.strip()}
    act_parts = {p.strip() for p in act_n.split(",") if p.strip()}
    if exp_parts and act_parts and exp_parts == act_parts:
        return RESULT_MATCHED, ""
    if exp_parts and act_parts and exp_parts.issubset(act_parts):
        return RESULT_MATCHED, "Expected value found among multiple listed values."

    return RESULT_UNMATCHED, ""


# ---------------------------------------------------------------------------
# Capacity - decimal (industry standard) unit conversion: 1 TB = 1000 GB
# ---------------------------------------------------------------------------

_CAPACITY_UNITS_DECIMAL = {
    "b": 1,
    "kb": 1000,
    "mb": 1000 ** 2,
    "gb": 1000 ** 3,
    "tb": 1000 ** 4,
    "pb": 1000 ** 5,
}


def parse_capacity_bytes(value):
    """Parse a capacity string like '480 GB', '1920GB', '1.92 TB', or a bare
    number like '1920' (assumed GB, the common CSV convention for this
    dataset) into bytes using decimal (1000-based) storage-industry
    convention. Returns None if it cannot be parsed."""
    if is_empty(value):
        return None
    text = normalize_whitespace(value).lower()

    bare_match = re.match(r"^([\d.]+)$", text)
    if bare_match:
        try:
            return float(bare_match.group(1)) * _CAPACITY_UNITS_DECIMAL["gb"]
        except ValueError:
            return None

    match = re.match(r"^([\d.]+)\s*([a-z]+)$", text)
    if not match:
        return None
    number_str, unit = match.groups()
    unit = unit.strip()
    if unit not in _CAPACITY_UNITS_DECIMAL:
        return None
    try:
        number = float(number_str)
    except ValueError:
        return None
    return number * _CAPACITY_UNITS_DECIMAL[unit]


def compare_capacity(expected, actual):
    if is_empty(expected):
        return RESULT_NOT_APPLICABLE, "No expected value in CSV."
    if is_empty(actual):
        return RESULT_NOT_FOUND, "Field not found on the webpage."

    exp_bytes = parse_capacity_bytes(expected)
    act_bytes = parse_capacity_bytes(actual)

    if exp_bytes is None or act_bytes is None:
        # Fall back to normalized text comparison if units are unparseable.
        if normalize_text_ci(expected) == normalize_text_ci(actual):
            return RESULT_MATCHED, "Compared as text (unit could not be parsed)."
        return RESULT_UNMATCHED, "Could not parse capacity units; text differs."

    # Allow tiny float rounding tolerance only (0.1%), not unit ambiguity.
    if exp_bytes == 0:
        return RESULT_UNMATCHED, ""
    diff_ratio = abs(exp_bytes - act_bytes) / exp_bytes
    if diff_ratio <= 0.001:
        return RESULT_MATCHED, ""
    return RESULT_UNMATCHED, ""


# ---------------------------------------------------------------------------
# DWPD - numeric equivalence (1 == 1.0 == 1.00), but 1 != 3
# ---------------------------------------------------------------------------

def compare_dwpd(expected, actual):
    if is_empty(expected):
        return RESULT_NOT_APPLICABLE, "No expected value in CSV (not applicable, e.g. HDD)."
    if is_empty(actual):
        return RESULT_NOT_FOUND, "DWPD not found on the webpage."

    exp_match = re.search(r"[\d.]+", str(expected))
    act_match = re.search(r"[\d.]+", str(actual))
    if not exp_match or not act_match:
        return RESULT_UNMATCHED, "Could not parse numeric DWPD value."

    try:
        exp_val = float(exp_match.group())
        act_val = float(act_match.group())
    except ValueError:
        return RESULT_UNMATCHED, "Could not parse numeric DWPD value."

    if abs(exp_val - act_val) < 1e-9:
        return RESULT_MATCHED, ""
    return RESULT_UNMATCHED, ""


# ---------------------------------------------------------------------------
# Form Factor - normalize inch notation only
# ---------------------------------------------------------------------------

def normalize_form_factor(value):
    text = normalize_text_ci(value)
    text = text.replace("-inch", "").replace(" inch", "").replace("inch", "")
    text = text.replace('"', "")
    text = normalize_whitespace(text)
    return text


def compare_form_factor(expected, actual):
    if is_empty(expected):
        return RESULT_NOT_APPLICABLE, "No expected value in CSV."
    if is_empty(actual):
        return RESULT_NOT_FOUND, "Form factor not found on the webpage."

    if normalize_form_factor(expected) == normalize_form_factor(actual):
        return RESULT_MATCHED, ""
    return RESULT_UNMATCHED, ""


# ---------------------------------------------------------------------------
# Interface - normalize spacing/unit formatting of a speed value (e.g. Gbps)
# ---------------------------------------------------------------------------

def normalize_interface(value):
    text = normalize_text_ci(value)
    text = text.replace("gb/s", "gbps").replace("gbit/s", "gbps")
    text = text.replace(" ", "")
    return text


def compare_interface(expected, actual):
    if is_empty(expected):
        return RESULT_NOT_APPLICABLE, "No expected value in CSV."
    if is_empty(actual):
        return RESULT_NOT_FOUND, "Interface not found on the webpage."

    if normalize_interface(expected) == normalize_interface(actual):
        return RESULT_MATCHED, ""
    return RESULT_UNMATCHED, ""


# ---------------------------------------------------------------------------
# Description - normalized text equality (no fuzzy/partial matching)
# ---------------------------------------------------------------------------

def normalize_description(value):
    text = normalize_text_ci(value)
    text = re.sub(r"[,\-]", " ", text)
    text = normalize_whitespace(text)
    return text


def compare_description(expected, actual):
    if is_empty(expected):
        return RESULT_NOT_APPLICABLE, "No expected value in CSV."
    if is_empty(actual):
        return RESULT_NOT_FOUND, "Description/model not found on the webpage."

    if normalize_description(expected) == normalize_description(actual):
        return RESULT_MATCHED, ""
    return RESULT_UNMATCHED, ""


# ---------------------------------------------------------------------------
# Part Specification - the CSV stores a Python-literal dict of
# {label: [value, ...]} scraped from the same page. Validate key by key.
# ---------------------------------------------------------------------------

def parse_specification(raw):
    if is_empty(raw):
        return None, "No expected value in CSV."
    try:
        parsed = ast.literal_eval(raw)
        if not isinstance(parsed, dict):
            return None, "Expected part_specification is not a dict."
        return parsed, ""
    except (ValueError, SyntaxError) as exc:
        return None, f"Could not parse expected part_specification: {exc}"


def compare_specification(expected_raw, actual_labels):
    """actual_labels: dict of {label: value_text} extracted from the page.

    Returns (overall_result, comment, per_key_results) where per_key_results
    is a list of (key, expected_value, actual_value, result).
    """
    expected_dict, err = parse_specification(expected_raw)
    if expected_dict is None:
        if err.startswith("No expected"):
            return RESULT_NOT_APPLICABLE, err, []
        return RESULT_NOT_FOUND, err, []

    per_key = []
    for key, exp_values in expected_dict.items():
        exp_value = exp_values[0] if isinstance(exp_values, list) and exp_values else exp_values
        if is_empty(exp_value):
            continue  # placeholder like '-' in the source data; nothing to check

        act_value = actual_labels.get(key)
        if act_value is None:
            per_key.append((key, exp_value, None, RESULT_NOT_FOUND))
            continue

        if normalize_text_ci(str(exp_value)) == normalize_text_ci(str(act_value)):
            per_key.append((key, exp_value, act_value, RESULT_MATCHED))
        else:
            per_key.append((key, exp_value, act_value, RESULT_UNMATCHED))

    if not per_key:
        return RESULT_NOT_APPLICABLE, "No comparable (non-placeholder) keys in specification.", per_key

    matched_keys = [k for k, _, _, r in per_key if r == RESULT_MATCHED]
    unmatched_keys = [(k, e, a) for k, e, a, r in per_key if r == RESULT_UNMATCHED]
    not_found_keys = [k for k, _, _, r in per_key if r == RESULT_NOT_FOUND]
    matched = len(matched_keys)
    total = len(per_key)

    comment = f"{matched}/{total} specification attributes matched"
    if matched < total:
        # Name the exact attributes involved, not just a count, so a
        # PARTIAL MATCH/UNMATCHED row is diagnosable without re-scraping.
        if matched_keys:
            comment += f" | Matched: {', '.join(matched_keys)}"
        if unmatched_keys:
            mismatches = "; ".join(
                f"{k} (expected='{e}', actual='{a}')" for k, e, a in unmatched_keys)
            comment += f" | Mismatched: {mismatches}"
        if not_found_keys:
            comment += f" | Not found on page: {', '.join(not_found_keys)}"

    if matched == total:
        return RESULT_MATCHED, comment, per_key
    if matched == 0:
        return RESULT_UNMATCHED, comment, per_key
    return "PARTIAL MATCH", comment, per_key
