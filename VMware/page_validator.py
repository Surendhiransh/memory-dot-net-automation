"""
Playwright page interaction: navigation, human-verification detection,
and stable field extraction.

Selector strategy (confirmed by DOM inspection of two live product pages):
    Each "Model Details" attribute is rendered as:
        <div class="mb-2 col-4">
          <span class="text-size-md"><b>{Label}: </b></span>
          <div class="text-size-md">{Value}</div>
        </div>
    We locate rows by their bold label text (accessible, stable) rather than
    by position/nth-child, so field order changes on the site do not break
    extraction.
"""

import re

CAPTCHA_KEYWORDS = [
    "verify you are human",
    "are you a human",
    "human verification",
    "captcha",
    "just a moment",
    "checking your browser",
    "unusual traffic",
    "access denied",
    "attention required",
]

# Rows in the "Model Details" grid use varying Bootstrap column widths
# (col-4 for most fields, col-6 for the last one or two e.g. "Part Number" /
# "Note(s)") but always share the "mb-2" wrapper with a direct
# span.text-size-md > b label followed by a sibling value div. Matching on
# that shape (rather than a specific col-N class) avoids silently dropping
# fields whose column width differs.
MODEL_DETAILS_ROW_SELECTOR = "div.card-type-asset div.mb-2:has(> span.text-size-md > b)"


def open_part_page(page, url, timeout_ms=45000):
    """Navigate to url. Returns a dict describing the outcome; never raises."""
    result = {
        "status": None,
        "final_url": None,
        "title": None,
        "load_ok": False,
        "error": None,
    }
    try:
        response = page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        try:
            page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            pass  # some pages keep background polling; don't fail on this alone
        result["status"] = response.status if response else None
        result["final_url"] = page.url
        result["title"] = page.title()
        result["load_ok"] = True
    except Exception as exc:
        result["error"] = str(exc)
    return result


def detect_human_verification(page):
    """Best-effort, content-based CAPTCHA/human-verification detection.
    Does not attempt to bypass anything - only reports what is on screen."""
    try:
        title = (page.title() or "").lower()
        if any(k in title for k in CAPTCHA_KEYWORDS):
            return True
        body_text = page.locator("body").inner_text(timeout=3000).lower()
        if any(k in body_text for k in CAPTCHA_KEYWORDS):
            return True
    except Exception:
        pass
    return False


def wait_for_manual_verification(page, category, part_number, url, poll_seconds=5, max_polls=120):
    """Blocks on operator input (headed browser) until verification is
    resolved. Returns True once the challenge is no longer detected, False if
    it gives up after max_polls checks."""
    print("\nHUMAN VERIFICATION REQUIRED")
    print(f"Category: {category}")
    print(f"Part Number: {part_number}")
    print(f"URL: {url}")
    print("Please complete the verification manually in the browser, then "
          "press Enter here to continue...")
    try:
        input()
    except EOFError:
        pass

    for _ in range(max_polls):
        if not detect_human_verification(page):
            return True
        page.wait_for_timeout(poll_seconds * 1000)
    return False


def extract_model_details(page):
    """Extract the Model Details label/value grid into a dict.
    Returns (labels_dict, found) - found is False if the grid itself is not
    present on the page (e.g. unexpected page structure)."""
    rows = page.locator(MODEL_DETAILS_ROW_SELECTOR)
    try:
        count = rows.count()
    except Exception:
        return {}, False

    if count == 0:
        return {}, False

    labels = {}
    for i in range(count):
        row = rows.nth(i)
        try:
            label_text = row.locator("b").first.inner_text(timeout=3000)
        except Exception:
            continue
        label = re.sub(r":\s*$", "", label_text).strip()

        try:
            value_text = row.locator("div.text-size-md").first.inner_text(timeout=3000)
        except Exception:
            value_text = ""
        labels[label] = value_text.strip()

    return labels, True
