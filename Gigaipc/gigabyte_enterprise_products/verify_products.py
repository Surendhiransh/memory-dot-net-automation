"""
Verifies gigabyte_enterprise_products2.csv against the live product pages.

For every row, re-fetches row['url'], re-extracts model_name / category /
specifications / features_summary / image_url from the live HTML using the
same DOM locations the original CSV appears to have been scraped from, and
diffs them against the CSV values.

Usage:
    python verify_products.py                      # full run, all rows
    python verify_products.py --limit 10            # smoke test on first 10 rows
    python verify_products.py --rows 0,5,120,400    # specific row indices (0-based, header excluded)
    python verify_products.py --resume              # skip rows already present in the output report

Output: verification_report.csv (one row per input row, PASS/FAIL per field + diff notes)
        verification_errors.csv (rows that couldn't be fetched/parsed, for re-run)
"""
import argparse
import ast
import csv
import random
import re
import subprocess
import time
import unicodedata
from pathlib import Path

from bs4 import BeautifulSoup

BASE_DIR = Path(__file__).parent
INPUT_CSV = BASE_DIR / "gigabyte_enterprise_products2.csv"
REPORT_CSV = BASE_DIR / "verification_report.csv"
ERRORS_CSV = BASE_DIR / "verification_errors.csv"

# NOTE: Akamai fingerprints the TLS ClientHello, not just HTTP headers.
# Python's `requests`/urllib3 gets a 403 even with an identical header set to
# curl, because its TLS stack signature is flagged. curl (verified working)
# is used via subprocess instead of requests.
HEADERS = [
    ("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"),
    ("Accept", "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8"),
    ("Accept-Language", "en-US,en;q=0.9"),
    ("Accept-Encoding", "gzip, deflate, br"),
    ("Referer", "https://www.google.com/"),
    ("sec-ch-ua", '"Chromium";v="128", "Not;A=Brand";v="24", "Google Chrome";v="128"'),
    ("sec-ch-ua-mobile", "?0"),
    ("sec-ch-ua-platform", '"Windows"'),
    ("sec-fetch-dest", "document"),
    ("sec-fetch-mode", "navigate"),
    ("sec-fetch-site", "cross-site"),
    ("sec-fetch-user", "?1"),
    ("upgrade-insecure-requests", "1"),
]

MAX_RETRIES = 3
BASE_DELAY = 1.2   # seconds between requests (politeness)
RETRY_BACKOFF = 5  # seconds, multiplied by attempt number


def normalize_text(s):
    """Collapse whitespace and fold known mojibake / trademark-symbol noise
    so encoding artifacts in the CSV don't register as false mismatches."""
    if s is None:
        return ""
    s = str(s)
    s = s.replace("�", "")  # the CSV's mangled (R)/(TM)/(deg) replacement char
    s = re.sub(r"&nbsp;?", " ", s)  # some pages use malformed &nbsp (no ';'), which lxml won't decode
    # NFKC first: decomposes single-char symbols like U+2103 (℃) into "°C" so
    # both that form and a literal "°C" strip to the same thing below.
    s = unicodedata.normalize("NFKC", s)
    for ch in ("™", "®", "°", "’", "‘"):
        s = s.replace(ch, "")
    s = re.sub(r"\s+", " ", s).strip()
    return s


def normalize_image_url(url):
    """Reduce an image URL down to the /Image/<hash>/<Variant>/<id> identity,
    ignoring domain, leading slash, and trailing format/size suffixes."""
    if not url:
        return ""
    m = re.search(r"/Image/([^/]+)/(\w+)/(\d+)", url)
    if m:
        return f"{m.group(1)}/{m.group(2)}/{m.group(3)}"
    return url.strip()


def parse_csv_literal(s):
    """CSV stores specifications/features_summary as Python repr() of a
    dict/set, not JSON. ast.literal_eval is required to parse it back."""
    if not s or not s.strip():
        return None
    try:
        return ast.literal_eval(s)
    except (ValueError, SyntaxError):
        return None


def fetch(url):
    cmd = ["curl", "-s", "--compressed", "-w", "\n__HTTP_STATUS__:%{http_code}", "--max-time", "25"]
    for k, v in HEADERS:
        cmd += ["-H", f"{k}: {v}"]
    cmd.append(url)

    last_exc = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            proc = subprocess.run(cmd, capture_output=True, timeout=30)
            out = proc.stdout.decode("utf-8", errors="replace")
            marker = "__HTTP_STATUS__:"
            idx = out.rfind(marker)
            if idx == -1:
                last_exc = f"curl failed: {proc.stderr.decode(errors='replace')[:200]}"
                time.sleep(RETRY_BACKOFF * attempt)
                continue
            body = out[:idx].rstrip("\n")
            status = out[idx + len(marker):].strip()
            if status == "200":
                return body, None
            # "000" means curl never got an HTTP response at all (DNS hiccup, TLS
            # reset, connection refused) -- a transient network failure, not a real
            # site error, so it must be retried like a timeout rather than failed fast.
            if status in ("000", "403", "429", "500", "502", "503", "504"):
                last_exc = f"HTTP {status}"
                time.sleep(RETRY_BACKOFF * attempt)
                continue
            return None, f"HTTP {status}"
        except subprocess.TimeoutExpired:
            last_exc = "timeout"
            time.sleep(RETRY_BACKOFF * attempt)
    return None, last_exc or "unknown error"


FOOTNOTE_SUFFIX_RE = re.compile(r"\[#\d+\]\s*$")


def desc_to_lines(desc_el):
    """Reproduce the apparent original scraper logic: <br> and </li> are line
    breaks, everything else (sup/b/a/span) stays inline so that e.g. a
    trademark symbol in its own <sup> doesn't split its word onto a new line."""
    inner = desc_el.decode_contents()
    inner = re.sub(r"<br\s*/?>", "\n", inner)
    inner = re.sub(r"</li\s*>", "\n", inner)
    inner = re.sub(r"<li[^>]*>", "", inner)
    inner = re.sub(r"</?ul[^>]*>", "\n", inner)
    text = BeautifulSoup(inner, "lxml").get_text()
    lines = [normalize_text(line) for line in text.split("\n")]
    return [l for l in lines if l]


def extract_live_fields(html):
    soup = BeautifulSoup(html, "lxml")

    btn = soup.select_one("[product-name][product-line]") or soup.select_one("[product-name]")
    live_category = btn.get("product-line", "").strip() if btn else ""

    # The H1 (js-titleInfo-ProductGroupName) is the canonical model name.
    # On solution/bundle pages (GIGAPOD-Rack-Scale, ...) the product-name
    # attribute instead holds "<model> <subtitle>", so prefer the H1.
    h1 = soup.select_one("h1.js-titleInfo-ProductGroupName") or soup.select_one("h1")
    live_model = h1.get_text(strip=True) if h1 else (btn.get("product-name", "").strip() if btn else "")

    img_el = soup.select_one("input.compareCheckBox[product-img]")
    if img_el:
        live_image = img_el.get("product-img", "").strip()
    else:
        # Solution/bundle pages (Data-Center, GIGAPOD-Rack-Scale, ...) have no
        # compare checkbox; fall back to the main product-gallery image.
        gallery_el = soup.select_one(".model-gallery-wrapper [data-src*='ProductRemoveBg']") \
            or soup.select_one("[data-fancybox='gallery'][data-src]")
        live_image = gallery_el.get("data-src", "").strip() if gallery_el else ""
    if live_image and not live_image.startswith("http"):
        live_image = "https://www.gigabyte.com" + live_image

    # Scope spec extraction to the actual Specifications section so unrelated
    # tables elsewhere on the page (e.g. a Downloads/Drivers table that reuses
    # generic label/cell classes) never get picked up.
    spec_section = soup.select_one("#Specifications") or soup

    def add_spec(title_raw, lines):
        title = normalize_text(FOOTNOTE_SUFFIX_RE.sub("", title_raw))
        if not title:
            return
        # Some pages render multiple config-variant rows under the same label
        # (e.g. an Intel-CPU row and an AMD-CPU row both titled "CPU"); the
        # source CSV accumulates all of them rather than keeping only one.
        live_specs.setdefault(title, []).extend(lines)

    live_specs = {}
    spec_items = spec_section.select(".SpecItem")
    tab_boxes = spec_section.select(".tab-spec-item-box")
    if spec_items:
        for item in spec_items:
            title_el = item.select_one(".Title")
            desc_el = item.select_one(".Desc")
            if not title_el or not desc_el:
                continue
            add_spec(title_el.get_text(), desc_to_lines(desc_el))
    elif tab_boxes:
        # Multi-component solution pages (GIGAPOD-Rack-Scale NVL72 racks, ...)
        # render a separate labeled box per component, each with its own
        # title/value pairs; the source CSV prefixes each key with the box's
        # component name, e.g. "DLB2-CB1 - CPU".
        for box in tab_boxes:
            box_title_el = box.select_one(".tab-spec-item-box-title")
            box_title = normalize_text(box_title_el.get_text()) if box_title_el else ""
            for item in box.select(".tab-spec-item"):
                title_el = item.select_one(".tab-spec-item-title")
                desc_el = item.select_one(".tab-spec-item-desc")
                if not title_el or not desc_el:
                    continue
                title = title_el.get_text()
                full_title = f"{box_title} - {title}" if box_title else title
                add_spec(full_title, desc_to_lines(desc_el))
    else:
        # Solution/bundle pages (GIGAPOD-Rack-Scale, ...) render specs as a
        # <tr><td class="spec-label">/<td class="spec-cell"> table instead.
        for row_el in spec_section.select("tr:has(.spec-label):has(.spec-cell)"):
            label_el = row_el.select_one(".spec-label")
            cell_el = row_el.select_one(".spec-cell")
            if not label_el or not cell_el:
                continue
            add_spec(label_el.get_text(), desc_to_lines(cell_el))

    live_features = set()
    summary_block = soup.select_one(".content-base-info-summary ul") or soup.select_one(".content-BaseInfo-Application + div ul")
    if summary_block:
        for li in summary_block.select("li"):
            if li.find("li"):
                continue  # skip wrapper <li> that itself contains a nested sub-list
            text = normalize_text(li.get_text())
            if text:
                live_features.add(text)

    return {
        "model_name": live_model,
        "category": live_category,
        "image_url": live_image,
        "specifications": live_specs,
        "features_summary": live_features,
    }


def compare_row(csv_row, live):
    diffs = {}

    csv_model = csv_row["model_name"].strip()
    diffs["model_name"] = "PASS" if csv_model == live["model_name"] else f"FAIL (csv={csv_model!r} live={live['model_name']!r})"

    csv_cat = csv_row["category"].strip()
    diffs["category"] = "PASS" if csv_cat == live["category"] else f"FAIL (csv={csv_cat!r} live={live['category']!r})"

    csv_img = normalize_image_url(csv_row["image_url"])
    live_img = normalize_image_url(live["image_url"])
    diffs["image_url"] = "PASS" if csv_img == live_img and csv_img else f"FAIL (csv={csv_img!r} live={live_img!r})"

    csv_specs = parse_csv_literal(csv_row["specifications"]) or {}
    live_specs = live["specifications"]
    norm_csv_specs = {normalize_text(k): [normalize_text(x) for x in (v if isinstance(v, list) else [v])] for k, v in csv_specs.items()}
    norm_live_specs = {normalize_text(k): [normalize_text(x) for x in v] for k, v in live_specs.items()}
    if norm_csv_specs == norm_live_specs:
        diffs["specifications"] = "PASS"
    else:
        missing_keys = set(norm_csv_specs) - set(norm_live_specs)
        extra_keys = set(norm_live_specs) - set(norm_csv_specs)
        changed = [k for k in (set(norm_csv_specs) & set(norm_live_specs)) if norm_csv_specs[k] != norm_live_specs[k]]
        diffs["specifications"] = f"FAIL (missing_keys={sorted(missing_keys)} extra_keys={sorted(extra_keys)} changed_keys={sorted(changed)})"

    csv_features = parse_csv_literal(csv_row["features_summary"]) or set()
    if not isinstance(csv_features, set):
        csv_features = set(csv_features)
    live_features = live["features_summary"]
    norm_csv_features = {normalize_text(f) for f in csv_features}
    norm_live_features = {normalize_text(f) for f in live_features}
    if norm_csv_features == norm_live_features:
        diffs["features_summary"] = "PASS"
    else:
        missing = norm_csv_features - norm_live_features
        extra = norm_live_features - norm_csv_features
        diffs["features_summary"] = f"FAIL (in_csv_not_live={sorted(missing)} in_live_not_csv={sorted(extra)})"

    return diffs


def load_done_urls():
    if not REPORT_CSV.exists():
        return set()
    with open(REPORT_CSV, encoding="utf-8-sig", newline="") as f:
        return {row["url"] for row in csv.DictReader(f)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None, help="only process the first N rows")
    parser.add_argument("--rows", type=str, default=None, help="comma-separated 0-based row indices to process")
    parser.add_argument("--resume", action="store_true", help="skip rows already in verification_report.csv")
    args = parser.parse_args()

    with open(INPUT_CSV, encoding="utf-8-sig", newline="") as f:
        all_rows = list(csv.DictReader(f))

    if args.rows:
        idxs = sorted({int(x) for x in args.rows.split(",")})
        rows = [(i, all_rows[i]) for i in idxs]
    elif args.limit:
        rows = list(enumerate(all_rows))[: args.limit]
    else:
        rows = list(enumerate(all_rows))

    done_urls = load_done_urls() if args.resume else set()

    report_fields = ["row_index", "model_name", "url", "model_name_check", "category_check",
                      "specifications_check", "features_summary_check", "image_url_check", "overall"]
    error_fields = ["row_index", "model_name", "url", "error"]

    report_exists = REPORT_CSV.exists() and args.resume
    error_exists = ERRORS_CSV.exists() and args.resume

    report_f = open(REPORT_CSV, "a" if report_exists else "w", newline="", encoding="utf-8")
    error_f = open(ERRORS_CSV, "a" if error_exists else "w", newline="", encoding="utf-8")
    report_writer = csv.DictWriter(report_f, fieldnames=report_fields)
    error_writer = csv.DictWriter(error_f, fieldnames=error_fields)
    if not report_exists:
        report_writer.writeheader()
    if not error_exists:
        error_writer.writeheader()

    total = len(rows)
    processed = 0
    passed = 0
    failed = 0
    errored = 0

    for i, row in rows:
        url = row["url"]
        if url in done_urls:
            continue

        html, err = fetch(url)
        if err:
            errored += 1
            error_writer.writerow({"row_index": i, "model_name": row["model_name"], "url": url, "error": err})
            error_f.flush()
            print(f"[{i+1}/{total}] ERROR {row['model_name']}: {err}", flush=True)
        else:
            try:
                live = extract_live_fields(html)
                if not live["model_name"] and not live["category"]:
                    # Occasional soft-block/challenge response comes back as
                    # HTTP 200 with no real page content; treat as a fetch
                    # failure so it gets retried rather than false-FAILed.
                    raise RuntimeError("empty page extraction (likely soft-block/challenge response)")
                diffs = compare_row(row, live)
                overall = "PASS" if all(v == "PASS" for v in diffs.values()) else "FAIL"
                report_writer.writerow({
                    "row_index": i,
                    "model_name": row["model_name"],
                    "url": url,
                    "model_name_check": diffs["model_name"],
                    "category_check": diffs["category"],
                    "specifications_check": diffs["specifications"],
                    "features_summary_check": diffs["features_summary"],
                    "image_url_check": diffs["image_url"],
                    "overall": overall,
                })
                report_f.flush()
                if overall == "PASS":
                    passed += 1
                else:
                    failed += 1
                print(f"[{i+1}/{total}] {overall} {row['model_name']}", flush=True)
            except Exception as e:
                errored += 1
                error_writer.writerow({"row_index": i, "model_name": row["model_name"], "url": url, "error": f"parse error: {e}"})
                error_f.flush()
                print(f"[{i+1}/{total}] PARSE ERROR {row['model_name']}: {e}", flush=True)

        processed += 1
        time.sleep(BASE_DELAY + random.uniform(0, 0.8))

    report_f.close()
    error_f.close()

    print(f"\nDone. processed={processed} passed={passed} failed={failed} errored={errored}")


if __name__ == "__main__":
    main()
