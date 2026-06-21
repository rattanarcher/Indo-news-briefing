"""
Tempo sitemap probe (via browser, to get past the WAF).

The homepage doesn't expose article links as plain anchors, and direct curl
to the sitemap returns 403. But the browser gets a clean 200 from Tempo, so
this tries to read the sitemap THROUGH the browser. News sitemaps are static
XML lists of recent articles: ideal for scraping, no ads, no JS feed.

Usage:  python probe_tempo_sitemap.py
"""

import re
from playwright.sync_api import sync_playwright

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

# Candidate sitemap locations to try, in order
CANDIDATES = [
    "https://www.tempo.co/sitemap.xml",
    "https://www.tempo.co/sitemap_news.xml",
    "https://www.tempo.co/news-sitemap.xml",
    "https://www.tempo.co/sitemap-news.xml",
    "https://nasional.tempo.co/sitemap.xml",
    "https://www.tempo.co/robots.txt",   # robots.txt usually lists sitemap URLs
]


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled",
                  "--no-sandbox", "--disable-dev-shm-usage"],
        )
        ctx = browser.new_context(user_agent=UA, locale="id-ID")
        page = ctx.new_page()

        for url in CANDIDATES:
            try:
                resp = page.goto(url, timeout=30000, wait_until="domcontentloaded")
                status = resp.status if resp else "?"
                body = page.content()
                # The browser wraps XML in HTML; pull the raw text
                text = page.inner_text("body") if "<body" in body.lower() else body
                locs = re.findall(r'<loc>\s*([^<\s]+)\s*</loc>', body) or \
                       re.findall(r'(https?://[^\s"<>]+/read/\d+/[^\s"<>]+)', body)
                read_locs = [l for l in locs if "/read/" in l]
                print(f"\n{'='*70}\n{url}\n  status={status}  body={len(body)} chars  "
                      f"<loc>={len(locs)}  /read/ urls={len(read_locs)}")
                if url.endswith("robots.txt"):
                    for line in text.splitlines():
                        if "sitemap" in line.lower():
                            print("  robots:", line.strip())
                for l in (read_locs or locs)[:12]:
                    print("   ", l)
            except Exception as e:
                print(f"\n{url}\n  ERROR: {e}")

        browser.close()
    print("\n--- READING ---")
    print("Any candidate with /read/ urls > 20 is our new Tempo source.")
    print("If only sub-sitemap .xml links appear, tell Claude which ones (esp. 'news').")
    print("If robots.txt lists a Sitemap: line, tell Claude that URL.")


if __name__ == "__main__":
    main()
