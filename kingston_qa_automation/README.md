# Kingston QA Automation

Python + Playwright + pytest framework that checks the Kingston CSV exports against the live
[kingston.com](https://www.kingston.com) website. For every CSV row it compares each column with
the value on the website and writes a report saying, per column, whether they match.

It covers four categories:

| Category | Input | What is checked |
|---|---|---|
| Servers | `kingston_servers_30.csv` | server description, URL, model, type, processor, sockets, specification JSON |
| Memory | `kingston memory.csv` | memory part cards on each server page |
| SSD | `ssd` rows of `kingston_parts_30.csv` | SSD cards + each variant's detail page ("Learn more") |
| USB drives | `datatraveler usb drives` rows of `kingston_parts_30.csv` | USB part cards on each server page |

The original CSV files and the website are never modified.

---

## 1. Setup

```bash
pip install -r requirements.txt
python -m playwright install chromium   # Chrome itself is used by default (see 4.3)
```

Requirements: Python 3.11+, Google Chrome installed.

## 2. Running

Every run opens a visible Chrome window (headless browsers are blocked by Cloudflare).

```bash
# Offline unit tests for the comparison rules (no browser, < 1 second)
python -m pytest tests/test_unit_validators.py tests/test_unit_parts.py

# Servers (30 rows)
python -m pytest tests/test_kingston_servers.py --reports-dir reports/server --row-delay 10

# Memory parts
python -m pytest tests/test_kingston_parts.py --reports-dir reports/memory --row-delay 10

# USB drives (same procedure as memory, different category)
KQA_PARTS_CATEGORY="datatraveler usb drives" python -m pytest tests/test_kingston_parts.py \
    --parts-csv kingston_parts_30.csv --reports-dir reports/usb --row-delay 10

# SSD parts
python -m pytest tests/test_kingston_ssd.py --reports-dir reports/ssd --row-delay 10
```

On Windows PowerShell set the category first: `$env:KQA_PARTS_CATEGORY = "datatraveler usb drives"`.

### Useful options

| Option | Meaning |
|---|---|
| `--rows 5` | validate only the first 5 rows (quick sample) |
| `--only-rows 11,12,19` | validate only these CSV row numbers (e.g. to re-run BLOCKED rows) |
| `--row-delay 10` | seconds to pause between pages (avoids Cloudflare rate limiting) |
| `--reports-dir reports/ssd` | where the `run_<timestamp>` folder is created |
| `--csv`, `--parts-csv`, `--ssd-csv` | use a different input file |
| `--headless` | run without a window (usually blocked by Cloudflare) |
| `--nav-timeout 60000` | page load timeout in ms |

Environment variables (prefix `KQA_`): `MAX_RETRIES` (default 2), `RETRY_BACKOFF_S` (5),
`CHALLENGE_WAIT_MS` (30000), `ELEMENT_TIMEOUT_MS` (15000), `VARIANT_DELAY_S` (5),
`PARTS_CATEGORY` (memory), `PROFILE_DIR` (`.browser_profile`).

### Re-running blocked rows and merging

A full run can have some pages BLOCKED by Cloudflare. Re-run only those rows, then merge;
the latest run wins per row:

```bash
python -m pytest tests/test_kingston_ssd.py --only-rows 432,433,434 --reports-dir reports/ssd --row-delay 20
python merge_reports.py --parts --prefix ssd --title "Kingston SSD Parts QA" \
    reports/ssd/run_A/ssd_results.json reports/ssd/run_B/ssd_results.json -o reports/ssd/final

# Servers use the same command without --parts:
python merge_reports.py reports/server/run_A/results.json reports/server/run_B/results.json -o reports/server/final
```

## 3. Output

Each run creates `reports/<category>/run_<timestamp>/` with:

| File | Content |
|---|---|
| `<prefix>_results.xlsx` | **Summary** sheet (live counts) + **Results** sheet |
| `<prefix>_results.csv` | the Results table as CSV |
| `<prefix>_report.html` | the same table as a web page |
| `<prefix>_results.json` | full detail (used by `merge_reports.py`) |
| `run.log` | every navigation, retry and result |
| `screenshots/` | full-page screenshots of blocked or failing pages |

All outputs use one **row-wise layout**: one line per CSV record, and for every field three
columns, **CSV | Website | Status**, followed by a **Comments** column that explains any field that
did not match (or says "All columns matched.").

### Statuses (memory / SSD / USB)

| Status | Meaning |
|---|---|
| MATCHED | CSV and website values match |
| MISMATCHED | both values exist but differ, e.g. CSV `16GB`, website `32GB` |
| UNMATCHED | product or value missing on one side, e.g. CSV blank but website shows `128` |
| BLOCKED | website not reachable (Cloudflare, HTTP 403/404/429/5xx, timeout) |

Overall status per record: any MISMATCHED → MISMATCHED, else any UNMATCHED → UNMATCHED,
else any BLOCKED → BLOCKED, else MATCHED.

Servers use PASS / FAIL / BLOCKED / NEEDS REVIEW per row and Matched / Mismatched / Unmatched per field.

## 4. How it works

### 4.1 Flow per page

1. Open the server page (`host_url`) once.
2. Read everything needed from the page in one JavaScript call.
3. Compare every CSV column independently (one failing column never hides another).
4. Reload the page once and confirm the values did not change.
5. Write the result; continue with the next page even if this one failed.

SSD adds one step: for each SSD card, open its "Learn more" detail page with the variant selected
(`?partnum=<part>`), read it and reload it once. Each variant is loaded once and reused for every
server page that lists it.

### 4.2 Validation rules

**Servers**
- `store` = fixed "Kingston"; `server_description` = `.l-inner h1.u-h3`; `host_url` = browser URL.
- `server_model` / `server_type` are derived from the description (split on " - ", keyword match).
- `processor` / `processor_sockets` / `server_specification` come from `.c-configuratorResultsCard`
  (`h3` heading, `h4` label or "Details", `p` values).

**Memory / USB** (card = `div.c-productCard4`, matched by part number)
- `category`: `data-category`; memory cards have none, so the DRAM card type means `memory`.
- `capacity` / `form_factor`: the card's `data-capacity` / `data-formfactor` attribute
  (USB cards have a capacity; memory cards have neither).
- `part_url`: datasheet URL if the card has one, otherwise the page URL.
- `datasheet_url`: first link in `ul.c-productCard4__details__downloads` (only if it is a PDF datasheet).
- `part_image_url`: card image `src`; `part_description`: card name; `description`: short description,
  or the card's bullet list joined with "; " when there is no short description (USB).
- `part_specification`: `{"General": {part_number, part_description, category}, "Description": {description}}`.

**SSD** (detail page reached through "Learn more")
- `part_number`: first item of `.s-ecommerce__details__features`.
- `capacity`: selected Capacity option; `form_factor`: the selected second option
  ("Form Factor", or e.g. "Heatsink" / "Security Features"; an option named "None" means blank).
- `part_url`: the "Learn more" link; `datasheet_url`: `a.link-item.datasheet-link`.
- `part_image_url`: first thumbnail in `.c-imageCarousel__thumbnails`.
- `part_description`: detail heading + capacity + second option (how the CSV builds it).
- `part_specification`: the spec table `div.c-table__main`; the tab matching the selected form factor is used.

### 4.3 Comparison choices

These are applied openly; the Comments column says when one was used.

- Extra whitespace and JSON key order are ignored.
- Server description: `Acer- X` vs `Acer - X` (spacing only) counts as matched.
- `?status=active`, which the site adds to the URL after load, is ignored.
- SSD: `2.5` vs `2.5"` (inch mark only) counts as matched; an image counts as the same image when
  only the size suffix differs (`-zm-lg` vs `-tn`).

### 4.4 Cloudflare and errors

- The browser is a normal, visible Chrome with a saved profile (`.browser_profile/`).
- A Cloudflare challenge is only given time to clear on its own; it is never solved or bypassed.
- HTTP 429 / 5xx / timeouts are retried up to 2 times; 403 / 404 / CAPTCHA are BLOCKED immediately.
- BLOCKED is never reported as a data mismatch, and `store` is still validated.

## 5. Project structure

```
kingston_qa/
  config.py         settings from pytest options / KQA_* env vars
  models.py         server data types and statuses
  csv_loader.py     read-only servers CSV loader
  browser.py        Playwright wrapper: navigation, retries, Cloudflare handling, screenshots
  comparators.py    whitespace / JSON comparison helpers
  validators.py     server column rules
  runner.py         server row flow (open, validate, reload)
  parts.py          memory / USB flow and rules (+ shared helpers)
  ssd.py            SSD flow, detail-page variants and rules
  wide.py           shared row-wise CSV / Excel / HTML writer
  reporter.py       server reports
  parts_reporter.py memory / SSD / USB reports
tests/
  conftest.py              pytest options, fixtures, report writing
  test_kingston_servers.py live server validation
  test_kingston_parts.py   live memory / USB validation
  test_kingston_ssd.py     live SSD validation
  test_unit_*.py           offline unit tests
merge_reports.py           merge runs into one final report
```

Every script starts with a header describing its purpose, who uses it and how to run it; the main
flows (`runner.py`, `parts.py`, `ssd.py`) are commented step by step.

## 6. Troubleshooting

| Symptom | What to do |
|---|---|
| Many BLOCKED rows with HTTP 429 | Cloudflare rate limit: wait a few minutes, re-run those rows with `--only-rows` and a larger `--row-delay`, then merge |
| Every page BLOCKED | do not use `--headless`; make sure Chrome is installed |
| `Unknown config option: log_cli...` warning | harmless when running with `-p no:logging` |
| Excel counts show 0 in a previewer | open the file in Excel / LibreOffice so the formulas calculate |
