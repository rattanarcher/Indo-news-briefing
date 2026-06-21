"""
Tempo browser diagnostic.

Run this locally (where your network reaches Tempo) to find out WHY the
browser scraper returns zero headlines. It saves a screenshot and the raw
HTML so you can see exactly what Chromium received.

Usage:
    python diagnose_tempo.py

Requires Playwright (already in the project):
    pip install playwright
    playwright install chromium
"""

import re
from playwright.sync_api import sync_playwright

URL = "https://nasional.tempo.co/"

CHALLENGE_MARKERS = [
    "checking your browser", "cloudflare", "captcha", "akamai",
    "access denied", "attention required", "just a moment",
    "verify you are human", "enable javascript",
]

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")


def _dismiss_overlay(page):
    try:
        page.keyboard.press("Escape")
        page.wait_for_timeout(400)
    except Exception:
        pass
    for sel in ["[aria-label*='close' i]", "[aria-label*='tutup' i]",
                "button[class*='close' i]", "[id*='dismiss' i]", "[class*='dismiss' i]",
                "button:has-text('Tutup')", "button:has-text('Lewati')",
                "button:has-text('Close')", "button:has-text('Skip')",
                ".ads-close, .ad-close, .close-ad"]:
        try:
            for el in page.query_selector_all(sel):
                try:
                    if el.is_visible():
                        el.click(timeout=1000); page.wait_for_timeout(300)
                except Exception:
                    continue
        except Exception:
            continue
    try:
        page.evaluate("""() => {
            ['iframe[src*="ad"]','[class*="interstitial" i]','[id*="interstitial" i]',
             '[class*="ad-overlay" i]','[class*="popup" i]'].forEach(
                s => document.querySelectorAll(s).forEach(e => e.remove()));
            document.body && (document.body.style.overflow='auto');
        }""")
        page.wait_for_timeout(300)
    except Exception:
        pass


def run(headless: bool):
    print(f"\n{'='*60}\nMODE: headless={headless}\n{'='*60}")
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=headless,
            args=["--disable-blink-features=AutomationControlled",
                  "--no-sandbox", "--disable-dev-shm-usage"],
        )
        context = browser.new_context(user_agent=UA,
                                      viewport={"width": 1920, "height": 1080},
                                      locale="id-ID")
        page = context.new_page()
        resp = page.goto(URL, timeout=45000, wait_until="domcontentloaded")
        page.wait_for_timeout(2500)

        before = page.evaluate("() => document.querySelectorAll(\"a[href*='/read/']\").length")
        print(f"/read/ links BEFORE dismissing overlay : {before}")

        _dismiss_overlay(page)
        for frac in (0.3, 0.6, 1.0):
            page.evaluate(f"window.scrollTo(0, document.body.scrollHeight * {frac})")
            page.wait_for_timeout(1200)
        for _ in range(4):
            n = page.evaluate("() => document.querySelectorAll(\"a[href*='/read/']\").length")
            if n >= 5:
                break
            _dismiss_overlay(page)
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(2000)

        status = resp.status if resp else "?"
        title = page.title()
        read_links = page.evaluate(
            "() => Array.from(document.querySelectorAll(\"a[href*='/read/']\")).map(a => a.href)")
        print(f"HTTP status   : {status}")
        print(f"Page title    : {title!r}")
        print(f"/read/ links AFTER dismissing overlay  : {len(read_links)}")

        tag = "headless" if headless else "headed"
        page.screenshot(path=f"tempo_{tag}.png", full_page=True)
        with open(f"tempo_{tag}.html", "w", encoding="utf-8") as f:
            f.write(page.content())
        print(f"Saved tempo_{tag}.png and tempo_{tag}.html")
        if read_links[:5]:
            print("Sample links:")
            for l in read_links[:5]:
                print("  ", l)

        browser.close()


if __name__ == "__main__":
    # Headless is what the pipeline uses. Headed shows what a real browser sees.
    run(headless=True)
    try:
        run(headless=False)
    except Exception as e:
        print(f"\n(headed mode unavailable in this environment: {e})")

    print("\n--- INTERPRETATION ---")
    print("BEFORE should be ~1 (overlay blocks the feed); AFTER should be 20+.")
    print("If AFTER is 20+, the overlay dismissal works -> deploy the scraper.")
    print("If AFTER is still ~1, open tempo_headless.png to see what overlay")
    print("  remains, and tell Claude what the close button looks like.")
