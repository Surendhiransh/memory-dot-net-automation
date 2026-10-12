# Memory Data Automation

QA automation tools that scrape vendor product / compatibility data and
validate existing datasets against it. Each folder is an independent
project with its own README, setup, and usage.

| Folder | What it does |
|---|---|
| [`Gigaipc/`](Gigaipc/README.md) | Scrapes the GIGAIPC industrial-PC catalog and audits an existing catalog CSV against the live site |
| [`Gigaipc/gigabyte_enterprise_products/`](Gigaipc/gigabyte_enterprise_products/README.md) | Verifies a Gigabyte enterprise-product catalog CSV against gigabyte.com and builds an Excel mismatch report |
| [`Hpe/`](Hpe/README.md) | Extracts processor models/descriptions from HPE QuickSpecs PDFs |
| [`Hpe/spec_verifier/`](Hpe/spec_verifier/README.md) | Verifies HPE server specifications from PDF/HTML URLs against CSV data |
| [`kingston_scraper/`](kingston_scraper/README.md) | Playwright agent that scrapes Kingston system pages for specs and compatible memory/SSD parts |
| `vmware/` | Validates Broadcom VMware Compatibility Guide SSD/HDD part data (on branch `add-vmware-part-validation` until merged) |

## Conventions

- **Generated output is not committed.** `output/` folders and generated
  report CSV/XLSX files are git-ignored — run the scripts locally to
  produce them.
- **Source data may be excluded** for data-privacy reasons; each project's
  README says where its input file should be placed.
- Each project is set up separately — see the **Setup** section of its
  README for dependencies and how to run it.
