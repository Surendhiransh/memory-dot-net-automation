# Oracle Memory Specification Comparator

This project checks the memory specifications in `oracle_host_memory_specification.csv` against the text in Oracle and Fujitsu server datasheet PDFs. It produces a detailed CSV report and a formatted Excel workbook showing matches, mismatches, comments, and the source text used as evidence.

## What the project contains

```text
.
|-- compare_memory_specs.py              Main comparison program
|-- oracle_host_memory_specification.csv Expected values for each server PDF (not committed)
|-- requirements.txt                     Pinned Python dependencies
|-- Oracle pdfs/                         Source Oracle/Fujitsu datasheets
|-- tests/
|   |-- test_compare_memory_specs.py     Automated tests (unittest)
|   |-- build_test_cases.py              Rebuilds the QA test-case workbook from a fresh run
|   `-- Oracle_Memory_Spec_Test_Cases.xlsx  QA test cases, results, traceability, defects (generated)
`-- output/                              Generated reports (not committed)
    |-- memory_spec_comparison.csv       Machine-readable comparison results
    `-- memory_spec_comparison.xlsx      Formatted results, summary, and evidence
```

The extra `Oracle pdfs/Oracle pdfs/` nesting in this repository is supported because the program searches the PDF directory recursively.

**Source data is not committed** (data privacy, as for the other projects in this repository). Place the input CSV in this folder as `oracle_host_memory_specification.csv` (a browser-download name such as `oracle_host_memory_specification (1).csv` also works). Without it, the comparison cannot run and the CSV-dependent tests are skipped; the parsing, extraction, rule and documentation tests still run. Generated files - `output/` and the QA workbook - are not committed either; produce them with the commands below.

## Requirements

- Python 3.9 or newer
- [PyMuPDF](https://pypi.org/project/PyMuPDF/) for extracting PDF text
- [openpyxl](https://pypi.org/project/openpyxl/) for creating the Excel workbook

Install the pinned dependencies:

```powershell
python -m pip install -r requirements.txt
```

## Run the comparison

From the project directory, run:

```powershell
python compare_memory_specs.py
```

The default locations are:

- Input CSV: `oracle_host_memory_specification.csv`; if that file does not exist, the newest browser-download copy such as `oracle_host_memory_specification (1).csv` is used. The console prints the `Input:` file that was used.
- PDF directory: `Oracle pdfs`
- Output directory: `output`

You can override any location:

```powershell
python compare_memory_specs.py --csv .\data\specs.csv --pdf-dir .\datasheets --out-dir .\reports
```

The script prints the number of matching, mismatching and error rows, the input CSV that was used, and the two output paths.

## Input CSV format

The input must contain these columns:

| Column | Meaning | Example |
|---|---|---|
| `dimm_slots` | DIMM slot count | `['24']` |
| `maximum_memory` | Maximum installed memory | `['1.5 TB']` |
| `maximum_LRDIMM` | Largest LRDIMM option | `['64 GB']` |
| `maximum_RDIMM` | Largest RDIMM option | `['32 GB']` |
| `maximum_UDIMM` | Largest UDIMM option | `['16 GB']` |
| `maximum_SODIMM` | Largest SO-DIMM option | `['32 GB']` |
| `file_name` | Exact PDF filename, matched case-insensitively | `x8-2l-datasheet.pdf` |

Cells currently use Python-list-like text such as `['24']`. Missing values may be written as `['nan']`, `none`, `null`, `n/a`, `-`, or left empty. Memory sizes must use `GB`, `TB`, or `PB`.

## Comparison rules

- A DIMM slot value matches either the total system slot count or the per-processor slot count. If only per-processor slots and processor count are found, the system total is derived.
- For maximum memory, a specification-table value has priority over marketing or summary text.
- If the CSV agrees with another PDF value but not the authoritative specification-table maximum, the result is a `Mismatch`. The comment explains that the CSV value appears elsewhere in the PDF.
- For each DIMM type, the largest size mentioned in the PDF is compared with the CSV.
- If both the CSV and PDF omit a value, it is a `Match`. If only one side has a value, it is a `Mismatch`.
- A missing PDF or a PDF without extractable text produces an `Error` for that server while processing continues for the other rows.
- A CSV that lacks any required column is rejected before comparing, with a message naming the missing column(s). A missing column is never treated as empty.

## Output files

`memory_spec_comparison.csv` contains one row per datasheet. Each checked field has a CSV value, PDF value, and status. It also contains an overall status and combined comments.

`memory_spec_comparison.xlsx` contains three worksheets:

| Worksheet | Contents |
|---|---|
| `Results` | The detailed comparison; green is Match, red is Mismatch, and gray is Error. Maximum-memory conflicts are resolved as Match or Mismatch |
| `Summary` | Counts by field and overall status, plus processed-file and error totals |
| `Evidence` | PDF page numbers and nearby extracted text supporting each detected value |

## Code walkthrough

Each row names one definition in `compare_memory_specs.py` and the lines it occupies. The test
`TestReadme.test_walkthrough_line_numbers_match_the_code` fails if a row points at the wrong line,
so after editing the script, update the ranges below (the failing test names the rows to fix).

### Setup, constants and value helpers

| Lines | Name | What it does |
|---:|---|---|
| 1-24 | module docstring | Explains fields, comparison rules and command usage; `argparse` reuses it as `--help` text. |
| 26-36 | imports | Standard library (`argparse`, `ast`, `csv`, `re`, `dataclasses`, `pathlib`) plus PyMuPDF for PDF text and openpyxl for Excel. |
| 38-38 | `BASE_DIR` | Folder containing the script, so default paths work from any current directory. |
| 40-47 | `FIELDS` | The six compared fields, in report order. |
| 49-49 | `REQUIRED_COLUMNS` | `FIELDS` plus `file_name`; the CSV header must contain all of them. |
| 50-50 | `DEFAULT_CSV_NAME` | Input file used when `--csv` is not given. |
| 52-52 | `MATCH` | The two comparison statuses, `Match` and `Mismatch` (rows that cannot be compared are `Error`). |
| 56-60 | `_UNITS` | Number words zero-nineteen (plus `single`) mapped to integers. |
| 61-64 | `_TENS` | Number words twenty-ninety. |
| 65-68 | `NUM_WORD` | Regex fragment for digits or number words such as `forty-eight`. |
| 69-69 | `SIZE` | Regex for a memory size: a number followed by GB, TB or PB. |
| 70-70 | `_UNIT_TO_GB` | Conversion factors; all sizes are compared in GB (1 TB = 1024 GB). |
| 73-80 | `word_to_int` | Converts `"96"`, `"sixteen"` or `"Twenty-four"` to an integer. |
| 83-84 | `size_to_gb` | Converts a captured number and unit to GB. |
| 87-94 | `format_size` | Turns GB back into display text (`768 GB`, `1.5 TB`); `None` becomes empty. |
| 97-101 | `parse_csv_size` | Reads a CSV size such as `1.5 TB`; raises `ValueError` for anything else. |
| 104-113 | `parse_csv_cell` | Decodes list-style cells like `['24']` with `ast.literal_eval` and drops missing markers (`nan`, `none`, `null`, `n/a`, `-`, empty). |

### PDF extraction

| Lines | Name | What it does |
|---:|---|---|
| 119-123 | `Finding` | One detected value: number, page, evidence snippet and kind (`spec`, `summary`, `total`, `per_processor`). |
| 127-135 | `PdfSpecs` | Everything found in one PDF: page texts, slot counts, processor counts, memory maxima, DIMM sizes. |
| 138-142 | `normalise` | Removes soft hyphens, turns bullet glyphs (including the broken U+FFFD bullet) into `•` separators, straightens quotes/dashes and collapses whitespace. |
| 145-147 | `snippet` | About 70 characters of text around a match, used as evidence. |
| 150-154 | `SLOT_NOUN` | The ways datasheets name slots: "dual inline memory module (DIMM) slots", "DIMM slots", "DIMMs", with optional DDR prefix. |
| 155-158 | `RE_SLOTS` | A count plus slot wording, with optional "per processor/socket/CPU". Rejects numbers glued to a hyphen (`DDR4-2400 DIMMs`) and "per channel" prose. |
| 159-161 | `RE_SLOTS_PAREN` | Parenthetical per-CPU counts such as `(12 per CPU module)`. |
| 162-167 | `RE_PROCS` | Four patterns for the processor count ("four-processor system", "up to 8 CPUs", ...), ignoring processor *cores*. |
| 168-173 | `RE_MEM_SPEC` | Spec-table wording for maximum memory ("3 TB maximum memory configuration", "DIMM slots provide up to ..."). |
| 174-178 | `RE_MEM_SUMMARY` | Summary/marketing wording ("slots with maximum memory of ...", "up to ... of memory"). |
| 181-198 | `RE_DIMM` | Two patterns per DIMM type: an "options:" clause and an inline "32 GB ... LRDIMMs" phrase. A look-behind stops `RDIMM` matching inside `LRDIMM`. |
| 199-199 | `RE_CLAUSE_END` | Where an options clause stops (next bullet, another DIMM type, "Oracle", end of sentence). |
| 200-200 | `RE_DIMM_SIZE` | Each `NN GB` inside an options clause. |
| 201-201 | `RE_GENERIC_DIMM` | "with 64 GB DIMMs" - a size whose DIMM type is not stated (SPARC/Fujitsu sheets). |
| 204-216 | `extract_slots` | Records total and per-processor slot counts; ignores counts below 2. |
| 219-224 | `extract_processors` | Records every processor count found. |
| 227-238 | `extract_memory` | Records spec-table and summary memory maxima separately so the spec table can win later. |
| 241-257 | `extract_dimms` | Cuts each options clause at `RE_CLAUSE_END`, records its sizes, adds inline matches and generic DIMM sizes. |
| 260-271 | `extract_pdf` | Opens the PDF, normalises every page and runs the four extractors on each page. |

### Comparison rules

| Lines | Name | What it does |
|---:|---|---|
| 277-282 | `FieldResult` | Result for one field: CSV value, PDF value, status, comment, evidence. |
| 285-286 | `distinct` | Unique sorted values from a list of findings. |
| 289-298 | `slot_values` | Largest total, largest per-processor count, processor count, and a derived total (per-processor x processors) when no total is printed. |
| 301-311 | `slot_display` | PDF slot text such as `96 total; 12 per processor`. |
| 314-319 | `system_total_text` | Explicit or derived total for comments. |
| 322-348 | `compare_slots` | Match if the CSV equals the total, the per-processor count or the derived total; handles missing values on either side. |
| 351-356 | `select_memory_value` | Largest spec-table value; falls back to summary text only when the spec table has none. |
| 359-376 | `memory_notes` | Notes smaller configurations and spec-vs-summary contradictions. |
| 379-409 | `compare_memory` | Match when the CSV equals the spec-table maximum (contradictions are only noted); Mismatch otherwise, including when the CSV matches only a summary value. |
| 412-431 | `compare_dimm` | Compares one DIMM type with the largest size listed for it. |
| 434-458 | `compare_row` | Rejects rows missing a column, missing PDFs and image-only PDFs; otherwise compares all six fields and adds the generic-DIMM note. |
| 461-463 | `overall` | Row is Mismatch if any field is Mismatch, else Match. |

### Reports and command line

| Lines | Name | What it does |
|---:|---|---|
| 468-472 | `FILLS` | Excel colours: green Match, red Mismatch, grey Error. |
| 473-473 | `HEADER_FILL` | Dark-blue header background. |
| 474-474 | `HEADER_FONT` | Bold white header text. |
| 477-481 | `result_header` | Results columns: file name, CSV/PDF/status per field, overall status, comments. |
| 484-499 | `write_sheet` | Writes rows, styles the header, freezes panes, adds filters, sizes columns and colours status cells. |
| 502-508 | `safe_compare_row` | Runs one row; any exception becomes that row's error so other rows continue. |
| 511-515 | `error_output_row` | Results row for an Error: CSV values kept, every status `Error`, message in comments. |
| 518-530 | `append_evidence` | Adds de-duplicated page/snippet evidence to the Evidence sheet. |
| 533-546 | `success_output_row` | Results row for a compared file; updates counts and joins comments. |
| 549-563 | `build_report_rows` | Loops over all CSV rows and builds Results and Evidence data plus counts. |
| 566-570 | `build_summary_rows` | Summary sheet: Match/Mismatch counts per field and overall, files processed, files with errors. |
| 573-588 | `write_reports` | Writes `memory_spec_comparison.csv` and the three-sheet `memory_spec_comparison.xlsx`. |
| 591-597 | `print_summary` | Console summary: counts, input file used and output paths. |
| 600-606 | `find_default_csv` | Default input; falls back to the newest `oracle_host_memory_specification (N).csv` download. |
| 609-633 | `main` | Parses options, validates the CSV path, PDF folder and CSV header (exit code 2 with a message on failure), then builds and writes the reports. |
| 636-637 | `__main__` guard | Runs `main()` only when the file is executed directly. |

## Processing flow

```text
CSV row
  -> find PDF by filename
  -> extract and normalize every PDF page
  -> detect slots, processors, memory maxima, and DIMM sizes
  -> compare six fields using the project rules
  -> calculate overall status
  -> collect page-level evidence
  -> write CSV and Excel reports
```

## Notes and limitations

- The extractor reads the PDF text layer; scanned/image-only documents need OCR before this program can compare them.
- Extraction is regex-based. Datasheets with substantially different wording may require another pattern.
- PDF filenames must be unique within the PDF directory tree because the lowercase filename is used as the lookup key.
- Only the first non-missing value in each input field is compared, even if a CSV cell contains several values.
- TB and PB use binary conversion: 1 TB = 1024 GB and 1 PB = 1,048,576 GB.

## Troubleshooting

- `ModuleNotFoundError`: install the two packages listed under Requirements.
- `input CSV not found` / `PDF folder not found`: check the path, or pass `--csv` / `--pdf-dir`.
- `missing required column(s)`: add the named column(s) to the CSV header (see Input CSV format).

## Tests

```powershell
python -m unittest discover -s tests -v
```

`tests/Oracle_Memory_Spec_Test_Cases.xlsx` documents the same checks as QA test cases, with results, traceability and defects. Rebuild it after changing the script or the tests; every case is executed again and Actual Result / Status are filled from the run:

```powershell
python tests/build_test_cases.py
```

If LibreOffice is installed, the summary formulas are calculated before saving; otherwise Excel calculates them on open.
- `PDF file not found`: check that `file_name` exactly matches a PDF's filename. Directory names do not matter.
- `PDF has no extractable text`: run OCR on that PDF or verify it manually.
- `Unrecognised size value in CSV`: use a number followed by `GB`, `TB`, or `PB`, for example `['768 GB']`.
- Unexpected mismatch: open the Excel `Evidence` sheet and inspect the captured page and surrounding PDF text.
