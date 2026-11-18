# Dell Memory Automation Scripts

QA automation scripts for verifying Dell memory compatibility data on memory.net.

| Script | Purpose |
|---|---|
| `dell_dimmranks.py` | Verifies supported DIMM ranks (single/dual/quad) |
| `dell_dimmslot.py` | Verifies the number of DIMM slots per system |
| `dell_normalization.py` | Normalizes and verifies Dell product and memory data |

## Running `dell_dimmslot.py`

Set the input locations with environment variables (defaults: `/mnt/data/dell.csv` and `/mnt/data`):

```
DELL_CSV_PATH=path/to/dell.csv DELL_PDF_DIR=path/to/pdfs python -m pytest dell_dimmslot.py
```

A missing referenced PDF counts as a failure. The test reports every failing row together at the end.
