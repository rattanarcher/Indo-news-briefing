"""
Browser-based scraper module using Playwright.
Used for sites that block requests from server IPs (Kompas, CNN Indonesia).
Launches a real headless Chrome browser to evade basic bot detection.
"""

import logging
import re
from datetime import datetime, timezone, timedelta

from src.scraper import Headline

logger = logging.getLogger(__name__)


# Use realistic browser settings to maximise success rate
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def _launch_browser():
    """Launch a headless Chromium instance with stealth settings."""
    # Lazy import so the rest of the project still works if Playwright isn't installed
    from playwright.sync_api import sync_playwright

    p = sync_playwright().start()
    browser = p.chromium.launch(
        headless=True,
        args=[
            "--disable-blink-features=AutomationControlled",  # Hide automation flag
            "--no-sandbox",
            "--disable-dev-shm-usage",
        ],
    )
    context = browser.new_context(
        user_agent=BROWSER_USER_AGENT,
        viewport={"width": 1920, "height": 1080},
        locale="id-ID",
    )
    return p, browser, context


def _is_url_today_or_yesterday(url: str) -> bool:
    """Check if URL contains today's or yesterday's date in YYYY/MM/DD format."""
    today = datetime.now(timezone(timedelta(hours=7)))  # WIB
    today_str = today.strftime("%Y/%m/%d")
    yesterday_str = (today - timedelta(days=1)).strftime("%Y/%m/%d")

    match = re.search(r'/(\d{4})/(\d{2})/(\d{2})/', url)
    if not match:
        return False
    url_date = f"{match.group(1)}/{match.group(2)}/{match.group(3)}"
    return url_date in (today_str, yesterday_str)


# ─── Kompas (national only) ──────────────────────────────────────────

def fetch_kompas_browser() -> list[Headline]:
    """
    Kompas.com - browser-based scrape of nasional.kompas.com.
    URLs follow pattern: nasional.kompas.com/read/YYYY/MM/DD/HHMMSSXXX/headline-slug
    """
    headlines = []
    p = browser = context = None

    try:
        p, browser, context = _launch_browser()
        page = context.new_page()

        page.goto("https://nasional.kompas.com/", timeout=30000, wait_until="domcontentloaded")
        # Allow JS to populate article links
        page.wait_for_timeout(3000)

        # Get all anchor tags
        anchors = page.query_selector_all("a[href]")

        seen_urls = set()
        for a in anchors:
            try:
                href = a.get_attribute("href") or ""
                title = (a.inner_text() or "").strip()

                # Kompas article URLs include /read/YYYY/MM/DD/
                if not re.search(r'kompas\.com/read/\d{4}/\d{2}/\d{2}/', href):
                    continue

                # Filter to today or yesterday only
                if not _is_url_today_or_yesterday(href):
                    continue

                # Skip empty titles, very short text, or duplicates
                if not title or len(title) < 15:
                    continue
                if href in seen_urls:
                    continue
                seen_urls.add(href)

                # Ensure absolute URL
                if not href.startswith("http"):
                    href = "https://nasional.kompas.com" + href

                headlines.append(Headline(
                    title=title,
                    url=href,
                    source="Kompas.com",
                ))
            except Exception:
                continue  # Skip individual link errors

        logger.info(f"Fetched {len(headlines)} headlines from Kompas.com (browser)")

    except Exception as e:
        logger.error(f"Browser scrape failed for Kompas: {e}")
    finally:
        try:
            if context:
                context.close()
            if browser:
                browser.close()
            if p:
                p.stop()
        except Exception:
            pass

    return headlines


# ─── CNN Indonesia (national only) ───────────────────────────────────

def fetch_cnn_indonesia_browser() -> list[Headline]:
    """
    CNN Indonesia - browser-based scrape of cnnindonesia.com/nasional.
    URLs follow pattern: cnnindonesia.com/nasional/YYYYMMDDHHMMSS-XX-XXXXXXX/slug
    """
    headlines = []
    p = browser = context = None

    try:
        p, browser, context = _launch_browser()
        page = context.new_page()

        page.goto("https://www.cnnindonesia.com/nasional", timeout=30000, wait_until="domcontentloaded")
        page.wait_for_timeout(3000)

        anchors = page.query_selector_all("a[href]")

        # CNN Indonesia URLs embed timestamp: /nasional/20260425143000-12-1234567/slug
        # The first 8 chars of timestamp are YYYYMMDD
        today = datetime.now(timezone(timedelta(hours=7)))
        today_compact = today.strftime("%Y%m%d")
        yesterday_compact = (today - timedelta(days=1)).strftime("%Y%m%d")

        seen_urls = set()
        for a in anchors:
            try:
                href = a.get_attribute("href") or ""
                title = (a.inner_text() or "").strip()

                # Match CNN Indonesia article URL pattern
                ts_match = re.search(r'/nasional/(\d{14})-\d+-\d+/', href)
                if not ts_match:
                    continue

                # Filter by date (first 8 digits of timestamp)
                article_date = ts_match.group(1)[:8]
                if article_date not in (today_compact, yesterday_compact):
                    continue

                if not title or len(title) < 15:
                    continue
                if href in seen_urls:
                    continue
                seen_urls.add(href)

                if not href.startswith("http"):
                    href = "https://www.cnnindonesia.com" + href

                headlines.append(Headline(
                    title=title,
                    url=href,
                    source="CNN Indonesia",
                ))
            except Exception:
                continue

        logger.info(f"Fetched {len(headlines)} headlines from CNN Indonesia (browser)")

    except Exception as e:
        logger.error(f"Browser scrape failed for CNN Indonesia: {e}")
    finally:
        try:
            if context:
                context.close()
            if browser:
                browser.close()
            if p:
                p.stop()
        except Exception:
            pass

    return headlines


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print("\n--- Kompas ---")
    for h in fetch_kompas_browser():
        print(f"  {h.title}\n    {h.url}")
    print("\n--- CNN Indonesia ---")
    for h in fetch_cnn_indonesia_browser():
        print(f"  {h.title}\n    {h.url}")
