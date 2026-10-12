# Gigabyte Enterprise Products Verifier

Verifies an existing Gigabyte enterprise-product catalog CSV
(`gigabyte_enterprise_products2.csv`) against the live product pages on
https://www.gigabyte.com/Enterprise, field by field, and builds an Excel
report explaining every mismatch.

## About This Task

The catalog CSV (servers, motherboards, ARM servers, GIGAPOD racks, ...) was
scraped at some point in the past. Gigabyte keeps updating its site, so the
CSV can drift. This tool re-fetches every row's `url`, re-extracts the same
fields from the live HTML, and reports what still matches, what changed, and
*why* it changed — so known, harmless differences (a renamed category, a
line-wrap difference, a trademark-symbol encoding issue) are separated from
real data errors that need manual review.

Fields checked per row: `model_name`, `category`, `specifications`,
`features_summary`, `image_url`.

## Setup

```
pip install -r requirements.txt
```

`curl` must also be on your `PATH`. Gigabyte's site is behind Akamai, which
fingerprints the TLS handshake and returns 403 to Python's `requests`, so
pages are fetched by calling `curl` via `subprocess`.

## Input

Place the source CSV in this folder as `gigabyte_enterprise_products2.csv`
(not committed — see the repo-level conventions). Expected columns:

```
model_name,category,url,specifications,features_summary,image_url
```

`specifications` is a Python `repr()` of a dict (`{label: [lines]}`) and
`features_summary` a `repr()` of a set; both are parsed with
`ast.literal_eval`.

## Usage

**Step 1 — verify every row against the live site:**

```
python verify_products.py                     # full run, all rows
python verify_products.py --limit 10          # smoke test on the first 10 rows
python verify_products.py --rows 0,5,120      # specific 0-based row indices
python verify_products.py --resume            # skip rows already in the report
```

Writes:

- `verification_report.csv` — one row per input row with
  `PASS` / `FAIL (...)` per field plus an `overall` result.
- `verification_errors.csv` — rows that couldn't be fetched or parsed
  (re-run them with `--resume` or `--rows`).

**Step 2 — build the final Excel deliverable:**

```
python build_final_report.py
```

Re-fetches every `FAIL` row, breaks each failure down field by field, and
classifies it with a plain-English explanation. Writes:

- `gigabyte_verification_final_report.xlsx`
  - **Summary** — every row with PASS/FAIL per field. Rows whose only
    differences have a known, harmless cause are reported as PASS; rows
    still FAIL are highlighted yellow (needs manual review) or grey
    (page couldn't be re-fetched — re-run).
  - **Mismatch Details** — one row per mismatched field with the full CSV
    value, the full live-page value, and the explanation.
- `gigabyte_verification_final_report.csv` — the Summary sheet as plain CSV.

### Known mismatch causes

| Reason | Meaning |
|---|---|
| Recategorized | Gigabyte moved the product to a different category after the CSV was made |
| Driver table | The CSV's specs were scraped from the driver/download table instead of the spec table |
| Multi-config tab | Page has several configuration tabs (e.g. Intel/AMD CPU); a static fetch only sees the default one |
| Line split | Same text, wrapped onto a different number of lines |
| Trademark format | Live page writes "TM" as text; the CSV has a corrupted ™ symbol |
| Fetch error | Page failed to load during the report run — re-run, not a pass or fail |

## Reusing for another brand

`build_final_report.py` is brand-agnostic apart from `BRAND_NAME` and
`INPUT_CSV_NAME` at the top. The DOM selectors in
`verify_products.py` (`extract_live_fields`) are specific to gigabyte.com.

## Notes

- Requests are throttled (~1.2–2 s between pages) with retry/backoff on
  403/429/5xx and transient network failures.
- Generated CSV/XLSX reports are run output and are not committed — run the
  scripts locally to produce them.
