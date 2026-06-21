"""
Tempo /politik/politik section probe - SINGLE request only.

A section page is structurally different from the homepage and may carry the
article feed as plain links. But Tempo has been returning 429 (rate limited),
so this makes exactly ONE request and reports clearly whether the problem is
rate-limiting (429) or page structure (200 but no links).

Run this only after waiting a few minutes since the last Tempo request, so any
active rate-limit window has expired. Do NOT run it repeatedly.

Usage:  python probe_tempo_politik.py
"""

import re
from playwright.sync_api import sync_playwright

URL = "https://www.tempo.co/politik/politik"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")


def _dismiss_overlay(page):
    try:
        page.keyboard.press("Escape"); page.wait_for_timeout(400)
    except Exception:
        pass
    try:
        page.evaluate("""() => {
            ['iframe[src*="ad"]','[class*="interstitial" i]','[id*="interstitial" i]',
             '[class*="ad-overlay" i]','[class*="popup" i]','[class*="modal" i]'].forEach(
                s => document.querySelectorAll(s).forEach(e => e.remove()));
            document.body && (document.body.style.overflow='auto');
        }""")
        page.wait_for_timeout(300)
    except Exception:
        pass


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled",
                  "--no-sandbox", "--disable-dev-shm-usage"],
        )
        ctx = browser.new_context(user_agent=UA, locale="id-ID",
                                  viewport={"width": 1920, "height": 1080})
        page = ctx.new_page()

        resp = page.goto(URL, timeout=45000, wait_until="domcontentloaded")
        status = resp.status if resp else "?"
        page.wait_for_timeout(2500)
        _dismiss_overlay(page)
        for frac in (0.3, 0.6, 1.0):
            page.evaluate(f"window.scrollTo(0, document.body.scrollHeight * {frac})")
            page.wait_for_timeout(1200)

        title = page.title()
        html = page.content()

        # All /read/ links, and the subset on tempo.co specifically
        all_read = re.findall(r'href="([^"]*/read/\d+/[^"]*)"', html)
        tempo_read = [u for u in all_read if "tempo.co/read/" in u
                      and not any(x in u for x in ["cantika.com", "indonesiana.id"])]
        # Links rendered into the live DOM (catches JS-built anchors)
        dom_read = page.evaluate(
            "() => Array.from(document.querySelectorAll(\"a[href*='/read/']\")).map(a => a.href)")
        dom_tempo = [u for u in dom_read if "tempo.co/read/" in u
                     and not any(x in u for x in ["cantika.com", "indonesiana.id"])]

        print(f"status            : {status}")
        print(f"title             : {title!r}")
        print(f"html size         : {len(html)} chars")
        print(f"/read/ in HTML     : {len(all_read)}  (tempo.co only: {len(tempo_read)})")
        print(f"/read/ in live DOM : {len(dom_read)}  (tempo.co only: {len(dom_tempo)})")

        page.screenshot(path="tempo_politik.png", full_page=True)
        with open("tempo_politik.html", "w", encoding="utf-8") as f:
            f.write(html)
        print("saved tempo_politik.png and tempo_politik.html")

        print("\nSample tempo.co article links:")
        for u in (dom_tempo or tempo_read)[:12]:
            print("  ", u)

        print("\n--- READING ---")
        if status == 429:
            print("429 = rate limited. Problem is your IP, not the page. Section pages")
            print("  won't help while throttled. Lean toward retiring/quieting Tempo.")
        elif len(dom_tempo) >= 10 or len(tempo_read) >= 10:
            print("Good — this section page exposes the article feed. Tell Claude and")
            print("  he'll point the scraper at the section pages instead of the homepage.")
        else:
            print("200 but few tempo.co links — the feed isn't in plain anchors here")
            print("  either. Open tempo_politik.html / .png and tell Claude what you see.")

        browser.close()


if __name__ == "__main__":
    main()
