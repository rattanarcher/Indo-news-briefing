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
        resp = page.goto(URL, timeout=45000, wait_until="networkidle")
        page.wait_for_timeout(3000)
        for frac in (0.3, 0.6, 1.0):
            page.evaluate(f"window.scrollTo(0, document.body.scrollHeight * {frac})")
            page.wait_for_timeout(1200)

        status = resp.status if resp else "?"
        title = page.title()
        body = (page.inner_text("body") or "")
        anchors = page.query_selector_all("a[href]")
        read_links = [a.get_attribute("href") for a in anchors
                      if re.search(r'tempo\.co/read/\d+/', a.get_attribute("href") or "")]

        print(f"HTTP status   : {status}")
        print(f"Page title    : {title!r}")
        print(f"Body length   : {len(body)} chars")
        print(f"Total anchors : {len(anchors)}")
        print(f"/read/ links  : {len(read_links)}")

        blocked = any(m in body.lower() or m in title.lower() for m in CHALLENGE_MARKERS)
        print(f"Challenge page: {'YES - bot wall detected' if blocked else 'no markers found'}")

        tag = "headless" if headless else "headed"
        page.screenshot(path=f"tempo_{tag}.png", full_page=True)
        with open(f"tempo_{tag}.html", "w", encoding="utf-8") as f:
            f.write(page.content())
        print(f"Saved tempo_{tag}.png and tempo_{tag}.html")
        if read_links[:3]:
            print("Sample links:")
            for l in read_links[:3]:
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
    print("If headless shows a challenge page but headed finds /read/ links,")
    print("  Tempo is blocking automated browsers -> need stealth or an alternative source.")
    print("If both find 0 links but no challenge markers,")
    print("  the link selector or timing is wrong -> inspect tempo_headless.html.")
    print("If both find links,")
    print("  the scraper works locally and the GitHub Actions runner IP is the blocked party.")
