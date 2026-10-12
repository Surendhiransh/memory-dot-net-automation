"""
Row-level, pre-Playwright validation:

1. Entire-row duplicate detection (all columns except Part URL).
2. Part URL validation - checks whether relevant column values are present
   in the Part URL's 'redirectFrom' query parameter (the only part of a
   Broadcom compatibility-guide URL that carries readable text; see
   URL_CHECK_COLUMNS below).
3. Specification-key exception: if a part_specifications key name (e.g.
   'SVID') is itself present in the Part URL, that key's VALUE is not
   required to also be present (URL_KEY_VALUE_SKIP_KEYS).

Pure string/dict logic - no Playwright/browser dependency, so it can run
fast over the entire source CSV, independent of the (slow, manual-captcha)
browser-based validation in main.py.
"""

import json
import re
from collections import defaultdict
from urllib.parse import urlparse, parse_qs

from field_matcher import is_empty, normalize_whitespace, normalize_text_ci
from csv_reader import CSV_COLUMNS

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Specification keys (from the part_specifications dict) whose VALUE check
# against the Part URL is skipped when the key NAME itself is found in the
# Part URL. Add more keys here as needed - no other code needs to change.
URL_KEY_VALUE_SKIP_KEYS = ["SVID"]

# Logical CSV_COLUMNS keys whose values are actively checked for presence in
# the Part URL's redirectFrom text. Broadcom Part URLs are query strings
# (…&productId=63686&redirectFrom=<description>…), not descriptive slugs, so
# only fields realistically expected to appear there are checked; every other
# column is reported NOT APPLICABLE instead of being silently skipped or
# falsely failed.
URL_CHECK_COLUMNS = ["part_description"]

NON_COMPARABLE_ROW_KEYS = {"csv_row_index"}

RESULT_MATCHED = "MATCHED"
RESULT_NOT_FOUND = "NOT FOUND"
RESULT_NOT_APPLICABLE = "NOT APPLICABLE"
RESULT_SKIPPED = "SKIPPED"

DUPLICATE_YES = "DUPLICATE"
DUPLICATE_NO = "UNIQUE"


# ---------------------------------------------------------------------------
# 1. Entire-row duplicate detection (all columns except Part URL)
# ---------------------------------------------------------------------------

def get_duplicate_compare_columns(fieldnames, url_col):
    """All row columns except Part URL (and internal bookkeeping keys)."""
    return [c for c in fieldnames if c != url_col and c not in NON_COMPARABLE_ROW_KEYS]


def normalize_for_duplicate(value):
    """Trim whitespace and canonicalize all 'empty' spellings (blank, 'nan',
    'null', '-', etc.) to the same value, so empty/null values compare
    consistently. Case is preserved: duplicate detection should not treat
    genuinely different values (e.g. differing brand casing) as identical
    just because letter case differs."""
    if is_empty(value):
        return ""
    return normalize_whitespace(value)


def build_duplicate_signature(row, compare_columns):
    return tuple(normalize_for_duplicate(row.get(c)) for c in compare_columns)


def find_duplicates(rows, fieldnames, url_col=None):
    """Return {csv_row_index: info} for every row, where info is:
        {"is_duplicate": bool, "group_id": int or None,
         "duplicate_of": [other csv_row_index in the same group]}

    A group is only assigned when 2+ rows share the same signature (all
    columns except Part URL, normalized). Never mutates the input rows.
    """
    url_col = url_col or CSV_COLUMNS["part_url"]
    compare_columns = get_duplicate_compare_columns(fieldnames, url_col)

    groups = defaultdict(list)
    for row in rows:
        signature = build_duplicate_signature(row, compare_columns)
        groups[signature].append(row["csv_row_index"])

    info = {}
    group_id = 0
    for signature, indices in groups.items():
        is_dup = len(indices) > 1
        gid = None
        if is_dup:
            group_id += 1
            gid = group_id
        for idx in indices:
            others = [i for i in indices if i != idx]
            info[idx] = {
                "is_duplicate": is_dup,
                "group_id": gid,
                "duplicate_of": others,
            }
    return info


# ---------------------------------------------------------------------------
# 2 & 3. Part URL validation, with the specification-key skip exception
# ---------------------------------------------------------------------------

def extract_redirect_from(url):
    """Decode the 'redirectFrom' query parameter from a Part URL. Returns
    '' if absent or the URL cannot be parsed."""
    if is_empty(url):
        return ""
    try:
        query = parse_qs(urlparse(url).query)
    except ValueError:
        return ""
    values = query.get("redirectFrom") or []
    return values[0] if values else ""


def key_token_in_url(key, url):
    """Word-boundary, case-insensitive check that `key` appears as a whole
    token in `url` - e.g. 'SVID' matches '...&svid=1' but NOT the 'VID'
    substring inside 'productId' or 'SVID' itself when checking for 'VID'."""
    if is_empty(key) or is_empty(url):
        return False
    pattern = r"\b" + re.escape(str(key).strip()) + r"\b"
    return re.search(pattern, url, re.IGNORECASE) is not None


def safe_parse_spec(raw):
    """part_specifications is stored as a JSON-ish dict of
    {label: [value, ...]}. Some rows contain raw, unescaped newlines inside
    string values (not valid JSON/Python literal syntax), so fall back to
    escaping bare newlines before giving up. Returns None if unparseable."""
    if is_empty(raw):
        return None
    for candidate in (raw, re.sub(r"(?<!\\)\n", "\\\\n", raw)):
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                return parsed
        except (json.JSONDecodeError, TypeError):
            continue
    return None


def spec_first_value(raw_value):
    return raw_value[0] if isinstance(raw_value, list) and raw_value else raw_value


def validate_part_url(row):
    """Check relevant column values (and, with the skip-key exception,
    part_specifications keys) against the Part URL's redirectFrom text.

    Returns {"field_results": {label: (result, comment)}, "overall": str,
    "comments": str}. field_results covers both top-level CSV_COLUMNS
    fields and any comparable part_specifications keys.
    """
    url = (row.get(CSV_COLUMNS["part_url"]) or "").strip()
    redirect_text = normalize_text_ci(extract_redirect_from(url))

    field_results = {}

    for logical_col, physical_col in CSV_COLUMNS.items():
        if logical_col in ("part_url", "part_specification", "category", "store"):
            continue
        expected = row.get(physical_col)
        if logical_col not in URL_CHECK_COLUMNS:
            field_results[physical_col] = (
                RESULT_NOT_APPLICABLE,
                "Not checked against Part URL: value is not expected to "
                "appear in this URL format (query-string, not a descriptive slug).",
            )
            continue
        if is_empty(expected):
            field_results[physical_col] = (RESULT_NOT_APPLICABLE, "No value in CSV.")
            continue
        if not redirect_text:
            field_results[physical_col] = (RESULT_NOT_FOUND, "No redirectFrom text in Part URL.")
            continue
        if normalize_text_ci(expected) in redirect_text:
            field_results[physical_col] = (RESULT_MATCHED, "")
        else:
            field_results[physical_col] = (RESULT_NOT_FOUND, "Value not found in Part URL.")

    spec_raw = row.get(CSV_COLUMNS["part_specification"])
    spec_dict = safe_parse_spec(spec_raw)
    if spec_dict is None:
        if not is_empty(spec_raw):
            field_results["part_specifications"] = (
                RESULT_NOT_APPLICABLE, "part_specifications could not be parsed; spec-level URL checks skipped.")
    else:
        for key, raw_value in spec_dict.items():
            value = spec_first_value(raw_value)
            if is_empty(value):
                continue  # placeholder value, nothing to check

            if key in URL_KEY_VALUE_SKIP_KEYS and key_token_in_url(key, url):
                field_results[f"spec:{key}"] = (
                    RESULT_SKIPPED,
                    f"'{key}' key present in Part URL; value check skipped "
                    f"per URL_KEY_VALUE_SKIP_KEYS.",
                )
                continue

            if not redirect_text:
                field_results[f"spec:{key}"] = (RESULT_NOT_FOUND, "No redirectFrom text in Part URL.")
                continue

            if normalize_text_ci(str(value)) in redirect_text:
                field_results[f"spec:{key}"] = (RESULT_MATCHED, "")
            else:
                field_results[f"spec:{key}"] = (RESULT_NOT_FOUND, "Value not found in Part URL.")

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

    failing = [label for label, (r, _) in field_results.items() if r == RESULT_NOT_FOUND]
    skipped = [label for label, (r, _) in field_results.items() if r == RESULT_SKIPPED]
    comment_parts = []
    if failing:
        comment_parts.append(f"Not found in URL: {', '.join(failing)}")
    if skipped:
        comment_parts.append(f"Skipped (key present in URL): {', '.join(skipped)}")
    comments = " | ".join(comment_parts) if comment_parts else "All checked values found in Part URL."

    return {"field_results": field_results, "overall": overall, "comments": comments}


# ---------------------------------------------------------------------------
# Orchestration - duplicate detection first, then Part URL validation for
# non-duplicate rows only (duplicates are a terminal result; per-field URL
# validation is skipped for them).
# ---------------------------------------------------------------------------

def process_rows(rows, fieldnames):
    """Run duplicate detection over ALL rows first, then Part URL validation
    for rows that are not duplicates. Returns a list of result dicts, one
    per input row, in the original order."""
    url_col = CSV_COLUMNS["part_url"]
    dup_info = find_duplicates(rows, fieldnames, url_col)

    results = []
    for row in rows:
        idx = row["csv_row_index"]
        info = dup_info[idx]
        result = {
            "csv_row_index": idx,
            "Category": row.get(CSV_COLUMNS["category"], ""),
            "Part URL": row.get(url_col, ""),
            "Duplicate Row": DUPLICATE_YES if info["is_duplicate"] else DUPLICATE_NO,
            "Duplicate Group Id": info["group_id"] if info["group_id"] is not None else "",
            "Duplicate Of Row(s)": ", ".join(str(i) for i in info["duplicate_of"]),
        }
        if info["is_duplicate"]:
            result["Part URL Validation Result"] = "SKIPPED (DUPLICATE ROW)"
            result["Part URL Validation Comments"] = (
                "Row is a duplicate (all columns except Part URL identical); "
                "Part URL validation not performed."
            )
        else:
            url_check = validate_part_url(row)
            result["Part URL Validation Result"] = url_check["overall"]
            result["Part URL Validation Comments"] = url_check["comments"]
        results.append(result)
    return results
