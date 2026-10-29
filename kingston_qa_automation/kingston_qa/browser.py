"""Playwright page wrapper: navigation with block detection and retries, plus DOM extraction.

Purpose :
  - open()/reload(): load a page and wait for its key element.
      * Cloudflare challenge -> wait for it to clear on its own (never solved or bypassed)
      * HTTP 429 / 5xx / timeout -> retry up to max_retries (default 2) with a back-off
      * HTTP 403 / 404 / CAPTCHA -> BLOCKED immediately (no retry)
  - snapshot(): read the server page (h1 + configurator cards) in one JavaScript call.
  - screenshot(): full-page evidence for failed or blocked pages.
Used by : runner.py (servers), parts.py (memory / USB), ssd.py (SSD).
"""
from __future__ import annotations

import logging
import time
from typing import Callable

from playwright.sync_api import BrowserContext, Error as PlaywrightError, Page, Response
from playwright.sync_api import TimeoutError as PlaywrightTimeout

from .config import Settings
from .models import NavResult, PageSnapshot

log = logging.getLogger("kingston_qa.browser")

DESCRIPTION_SELECTOR = ".l-inner h1.u-h3"
CARD_SELECTOR = ".c-configuratorResultsCard"
CHALLENGE_SELECTORS = ("iframe[src*='challenges.cloudflare.com']", "#challenge-form",
                       "#cf-challenge-running", "#challenge-running", ".cf-turnstile")
CAPTCHA_SELECTORS = ("iframe[src*='recaptcha']", "iframe[src*='hcaptcha']", ".g-recaptcha", ".h-captcha")
CHALLENGE_TITLES = ("just a moment", "attention required", "access denied", "verify you are human")
TRANSIENT_HTTP = {408, 425, 429, 500, 502, 503, 504, 520, 521, 522, 523, 524}

# Mirrors the CSV extraction: every list item in a card. A labelled item (h4) is one value with its
# <p> texts joined by " / "; an unlabelled item yields one value per <p> (or its own text when it has
# no <p>). textContent is used because headings are CSS-uppercased and some lists sit in collapsed
# accordions.
EXTRACT_JS = """
() => {
  const clean = s => (s || '').replace(/\\s+/g, ' ').trim();
  const h1 = document.querySelector('.l-inner h1.u-h3');
  const cards = [...document.querySelectorAll('.c-configuratorResultsCard')].map(card => {
    const h3 = card.querySelector('h3');
    const items = [...card.querySelectorAll('ul > li')].flatMap(li => {
      const h4 = li.querySelector(':scope > h4');
      const label = h4 ? clean(h4.textContent) : null;
      const ps = [...li.querySelectorAll(':scope > p')].map(p => clean(p.textContent)).filter(Boolean);
      if (ps.length) return label ? [{label, value: ps.join(' / ')}] : ps.map(value => ({label, value}));
      const copy = li.cloneNode(true);
      copy.querySelectorAll(':scope > h4').forEach(e => e.remove());
      return [{label, value: clean(copy.textContent)}];
    });
    return {heading: h3 ? clean(h3.textContent) : null, items};
  });
  return {description: h1 ? clean(h1.textContent) : null, cards};
}
"""


class _Retryable(Exception):
    pass


class _Blocked(Exception):
    pass


class KingstonPage:
    def __init__(self, context: BrowserContext, settings: Settings):
        self.settings = settings
        self.page: Page = context.pages[0] if context.pages else context.new_page()
        self.page.set_default_timeout(settings.element_timeout_ms)
        self.page.set_default_navigation_timeout(settings.nav_timeout_ms)

    # ---------- navigation ----------
    def open(self, url: str, ready_selector: str = DESCRIPTION_SELECTOR) -> NavResult:
        return self._with_retries(lambda: self.page.goto(url, wait_until="domcontentloaded"), f"open {url}",
                                  ready_selector)

    def reload(self, ready_selector: str = DESCRIPTION_SELECTOR) -> NavResult:
        return self._with_retries(lambda: self.page.reload(wait_until="domcontentloaded"), "reload", ready_selector)

    def _with_retries(self, action: Callable[[], Response | None], label: str, ready_selector: str) -> NavResult:
        attempts = self.settings.max_retries + 1
        last_reason, http = "", None
        for attempt in range(1, attempts + 1):
            try:
                response = action()
                http = response.status if response else None
                self._ensure_ready(http, ready_selector)
                log.info("%s -> ready (HTTP %s, attempt %d)", label, http, attempt)
                return NavResult(True, self.page.url, http, attempt)
            except _Blocked as exc:
                log.warning("%s -> BLOCKED: %s", label, exc)
                return NavResult(False, self.page.url, http, attempt, str(exc))
            except (_Retryable, PlaywrightTimeout, PlaywrightError) as exc:
                last_reason = _short(exc)
                log.warning("%s -> attempt %d/%d failed: %s", label, attempt, attempts, last_reason)
                if attempt < attempts:
                    time.sleep(self.settings.retry_backoff_s * attempt)
        return NavResult(False, self.page.url, http, attempts, f"Inaccessible after {attempts} attempts: {last_reason}")

    def _ensure_ready(self, http: int | None, ready_selector: str) -> None:
        """Wait for the page's key element; classify anything else as blocked or transient."""
        if self._challenge_present():
            log.info("Cloudflare challenge shown; waiting up to %d ms for it to clear on its own",
                     self.settings.challenge_wait_ms)
            if not self._wait_for(ready_selector, self.settings.challenge_wait_ms):
                if self._captcha_present():
                    raise _Blocked("CAPTCHA requires human interaction")
                raise _Retryable(f"Cloudflare challenge did not clear (HTTP {http})")
            return
        if self._captcha_present():
            raise _Blocked("CAPTCHA requires human interaction")
        if http in TRANSIENT_HTTP:
            raise _Retryable(f"HTTP {http}")
        if http is not None and http >= 400:
            raise _Blocked(f"HTTP {http}")
        if not self._wait_for(ready_selector, self.settings.element_timeout_ms):
            if self._challenge_present():
                raise _Retryable("Cloudflare challenge appeared after load")
            # Page served normally but without the key element: validated as data, not blocked.
            log.warning("%s not found on %s", ready_selector, self.page.url)
            return
        if ready_selector != DESCRIPTION_SELECTOR:
            return
        try:  # cards render with the page; a short wait covers late hydration
            self.page.wait_for_selector(CARD_SELECTOR, state="attached", timeout=5000)
        except PlaywrightTimeout:
            log.warning("No configurator cards found on %s", self.page.url)

    def _wait_for(self, selector: str, timeout_ms: int) -> bool:
        try:
            self.page.wait_for_selector(selector, state="attached", timeout=timeout_ms)
            return True
        except PlaywrightTimeout:
            return False

    def _any(self, selectors) -> bool:
        return any(self.page.locator(s).count() for s in selectors)

    def _challenge_present(self) -> bool:
        try:
            title = self.page.title().lower()
        except PlaywrightError:
            title = ""
        return any(t in title for t in CHALLENGE_TITLES) or "__cf_chl" in self.page.url or self._any(CHALLENGE_SELECTORS)

    def _captcha_present(self) -> bool:
        return self._any(CAPTCHA_SELECTORS)

    # ---------- extraction ----------
    def snapshot(self) -> PageSnapshot:
        data = self.page.evaluate(EXTRACT_JS)
        return PageSnapshot(url=self.page.url, description=data["description"], cards=data["cards"])

    def screenshot(self, path) -> str | None:
        try:
            self.page.screenshot(path=str(path), full_page=True)
            return str(path)
        except PlaywrightError as exc:
            log.warning("Screenshot failed: %s", _short(exc))
            return None


def _short(exc: Exception) -> str:
    return str(exc).strip().splitlines()[0][:300] if str(exc).strip() else type(exc).__name__
