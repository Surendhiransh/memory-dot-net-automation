# VMware / Broadcom Compatibility Guide Part Validator

Validates a CSV of SSD/HDD part records scraped from the Broadcom VMware
Compatibility Guide (https://compatibilityguide.broadcom.com) — both
offline (the CSV against itself) and live (the CSV against the product
pages on the site) — and reports, per record and per field, what is
correct, wrong, or missing.

## About This Task

The source CSV (`vmware_broadcom_playwright_no_dup.csv`) holds one row per
Compatibility Guide storage part: flat columns (`part_description`, `oem`,
`oem_part_number`, `part_number`, `capacity`, `interface`, `form_factor`,
`DWPD`, `category`, `store`), the `part_url` of the product page, and a
`part_specifications` JSON blob scraped from that page's "Model Details"
grid. Before the file is trusted downstream it needs checking for:

1. **Internal consistency** — does every flat column agree with the same
   value inside its own row's `part_specifications`?
2. **Duplicates** — are any rows repeated (ignoring `part_url`)?
3. **Part URL** — do the row's values appear in its `part_url`?
4. **Live accuracy** — does a random sample still match the live site?

Checks 1–3 are fast and browser-free, so they run over every row. Check 4
drives a real browser and is run on a sample.

## Setup

```
pip install playwright openpyxl
playwright install chromium
```

Place the source CSV in this folder as
`vmware_broadcom_playwright_no_dup.csv` (not included in the repo — data
files are git-ignored).

## Usage

**Field consistency — flat columns vs `part_specifications` (all rows):**

```
python check_field_consistency.py
python check_field_consistency.py "path/to/other.csv"
```

Compares each flat column with its `part_specifications` key
(`part_description`↔`Model`, `oem`↔`Partner Name`,
`oem_part_number`↔`Part Number`, `part_number`↔`Product Id`,
`capacity`↔`Capacity`, `interface`↔`Interface Speed`,
`form_factor`↔`Form Factor`, `DWPD`↔`DWPD`), checks `category` against the
Part URL's `program` parameter and `store` against the default `VMware`.
The optional argument points it at any source CSV. Writes:

- `vmware_field_consistency_report.csv` — one row per record with
  Expected / Actual / Result per field, `Overall Result`, and `Comments`.
- `vmware_field_consistency_report.xlsx` — same data with coloured
  headers (Expected = dark blue, Actual = blue, Result = green), coloured
  result cells, frozen header, and filters.

**Duplicate rows (all rows):**

```
python check_duplicates.py
```

Writes `vmware_duplicate_rows_report.csv` — only the duplicate rows, with
their group id and the rows they duplicate.

**Part URL validation (all rows):**

```
python check_part_url_validation.py
```

Checks every column value (and every `part_specifications` key) for
presence in the Part URL. Writes
`vmware_no_dup_part_url_validation_report.csv`.

**Live validation against the Broadcom site (sample):**

```
python main.py
```

Randomly samples 25 SSD + 25 HDD rows (`seed=42`, reproducible), opens each
`part_url` in a visible Chromium window, reads the "Model Details" grid,
and compares it field by field with the CSV. If the site shows a
human-verification / CAPTCHA page, the run pauses so it can be solved in
the browser window. Also runs the duplicate + Part URL pre-check over the
whole CSV first. Writes:

- `vmware_part_validation_result_50_25SSD_25HDD.xlsx` / `.csv` — per-record
  results plus a Summary sheet.
- `vmware_duplicate_and_url_validation_report.csv`

Sample sizes, seed, and headless mode are set at the top of `main.py`
(`TEST_COUNT_SSD`, `TEST_COUNT_HDD`, `RANDOM_SEED`, `HEADLESS`).

**Tests:**

```
python -m unittest test_row_level_validator
```

Two smoke tests read the real source CSV, so they need it present in this
folder.

Generated reports are run output, not part of this repo — run the scripts
locally to produce them.

## Modules

| File | Role |
|---|---|
| `csv_reader.py` | Loads the CSV; maps logical field names to actual column names |
| `field_matcher.py` | Normalisation + comparison rules per field type |
| `row_level_validator.py` | Duplicate detection and Part URL checks |
| `report_generator.py` | CSV / Excel report writers |
| `page_validator.py` | Playwright page loading, CAPTCHA detection, Model Details extraction |
| `sample_selector.py` | Reproducible random SSD/HDD sampling |

## Notes

- Result values: `MATCHED`, `UNMATCHED`, `NOT FOUND`, `NOT APPLICABLE`,
  and per record `PARTIAL MATCH` when some but not all fields match.
- Placeholder values (`-`, `N/A`, `nan`, blank) are treated as empty, not
  as mismatches.
- Column names are not symmetric with the site labels: CSV `part_number`
  is the site's **Product Id**, and CSV `oem_part_number` is the site's
  **Part Number**.
- Cross-reference report rows by `csv_row_index` (0-based data row; add 2
  for the spreadsheet line number) or by `part_url`, not by position.
