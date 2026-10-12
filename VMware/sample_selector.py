"""
Reproducible random sampling of SSD/HDD records for validation.
Never mutates the source CSV rows list beyond reading it.
"""

import random

from csv_reader import CSV_COLUMNS, get_rows_by_category


def select_sample(rows, ssd_count, hdd_count, seed):
    """Return (selected_records, warnings).

    selected_records: list of dicts, each the original CSV row dict plus
        '_category_norm' set to 'ssd' or 'hdd'.
    warnings: list of human-readable strings describing any shortfall
        (e.g. fewer than requested records available for a category).
    """
    rng = random.Random(seed)
    warnings = []
    selected = []

    for category, count in (("ssd", ssd_count), ("hdd", hdd_count)):
        pool = get_rows_by_category(rows, category)
        available = len(pool)
        if available < count:
            warnings.append(
                f"Only {available} '{category}' records available in the CSV; "
                f"requested {count}. Selecting all {available} available records "
                f"WITHOUT duplication."
            )
            picked = pool
        else:
            picked = rng.sample(pool, count)

        for r in picked:
            r = dict(r)
            r["_category_norm"] = category
            selected.append(r)

    # Sanity check: no duplicate rows (by csv_row_index).
    seen = set()
    deduped = []
    for r in selected:
        idx = r["csv_row_index"]
        if idx in seen:
            continue
        seen.add(idx)
        deduped.append(r)

    return deduped, warnings


def print_selected_samples(selected):
    part_no_col = CSV_COLUMNS["part_number"]
    url_col = CSV_COLUMNS["part_url"]
    cat_col = CSV_COLUMNS["category"]

    print(f"{'S.No':<6}{'Category':<10}{'Part Number':<30}Part URL")
    for i, r in enumerate(selected, start=1):
        cat = (r.get(cat_col) or "").strip()
        part_no = (r.get(part_no_col) or "").strip()
        url = (r.get(url_col) or "").strip()
        print(f"{i:<6}{cat:<10}{part_no:<30}{url}")