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


# ─── Tempo (nasional + dunia) ────────────────────────────────────────

def fetch_tempo_browser() -> list[Headline]:
    """
    Tempo.co - browser-based scrape of nasional.tempo.co and dunia.tempo.co.
    URLs follow pattern: nasional.tempo.co/read/NNNNNNN/slug (no date in URL).

    Tempo added bot protection (403 on direct requests, malformed RSS XML).
    This scraper waits for JS, scrolls to trigger lazy loading, and detects
    WAF/challenge interstitials so failures are diagnosable rather than silent.
    """
    headlines = []
    p = browser = context = None

    pages_to_scrape = [
        ("https://nasional.tempo.co/", "nasional"),
        ("https://dunia.tempo.co/", "dunia"),
    ]

    # Phrases that indicate a bot-wall / challenge page rather than real content
    CHALLENGE_MARKERS = [
        "checking your browser", "cloudflare", "captcha", "akamai",
        "access denied", "attention required", "just a moment",
        "verify you are human", "enable javascript",
    ]

    try:
        p, browser, context = _launch_browser()

        for page_url, section in pages_to_scrape:
            try:
                page = context.new_page()
                # wait_until="networkidle" gives JS-driven link lists time to populate
                page.goto(page_url, timeout=45000, wait_until="networkidle")
                page.wait_for_timeout(3000)

                # Scroll to trigger lazy-loaded article cards
                try:
                    for frac in (0.3, 0.6, 1.0):
                        page.evaluate(f"window.scrollTo(0, document.body.scrollHeight * {frac})")
                        page.wait_for_timeout(1200)
                except Exception:
                    pass

                # Diagnose a challenge/block page instead of failing silently
                try:
                    body_text = (page.inner_text("body") or "").lower()[:2000]
                    title_text = (page.title() or "").lower()
                    if any(m in body_text or m in title_text for m in CHALLENGE_MARKERS):
                        logger.error(
                            f"Tempo {section}: looks like a bot-challenge/WAF page "
                            f"(title={title_text!r}). Headless browser is being blocked."
                        )
                except Exception:
                    pass

                anchors = page.query_selector_all("a[href]")
                logger.info(f"Tempo {section}: {len(anchors)} anchors found on page")

                seen_urls = set(h.url for h in headlines)  # dedup across sections
                section_count = 0
                for a in anchors:
                    try:
                        href = a.get_attribute("href") or ""
                        title = (a.inner_text() or "").strip()

                        # Tempo article URLs contain /read/ followed by a numeric ID
                        if not re.search(r'tempo\.co/read/\d+/', href):
                            continue
                        if not title or len(title) < 15:
                            continue
                        if href in seen_urls:
                            continue
                        seen_urls.add(href)

                        if not href.startswith("http"):
                            href = f"https://{section}.tempo.co{href}"

                        headlines.append(Headline(
                            title=title,
                            url=href,
                            source="Tempo.co",
                        ))
                        section_count += 1
                    except Exception:
                        continue

                logger.info(f"Fetched {section_count} headlines from Tempo.co/{section} (browser)")
                page.close()

            except Exception as e:
                logger.error(f"Browser scrape failed for Tempo {section}: {e}")

    except Exception as e:
        logger.error(f"Browser scrape failed for Tempo: {e}")
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

    logger.info(f"Fetched {len(headlines)} total headlines from Tempo.co (browser)")
    return headlines


# ─── CNN Indonesia (national only) ───────────────────────────────────

def fetch_cnn_indonesia_browser() -> list[Headline]:
    """
    CNN Indonesia - browser-based scrape of cnnindonesia.com/nasional.
    URLs typically follow pattern: cnnindonesia.com/nasional/YYYYMMDDHHMMSS-XX-XXXXXXX/slug
    """
    headlines = []
    p = browser = context = None

    try:
        p, browser, context = _launch_browser()
        page = context.new_page()

        page.goto("https://www.cnnindonesia.com/nasional", timeout=45000, wait_until="domcontentloaded")
        # Wait longer for lazy-loaded content (CNN Indonesia loads articles via JS)
        page.wait_for_timeout(8000)

        # Scroll down to trigger any lazy-loading
        try:
            page.evaluate("window.scrollTo(0, document.body.scrollHeight / 2)")
            page.wait_for_timeout(2000)
        except Exception:
            pass

        anchors = page.query_selector_all("a[href]")

        # Date for filtering
        today = datetime.now(timezone(timedelta(hours=7)))
        today_compact = today.strftime("%Y%m%d")
        yesterday_compact = (today - timedelta(days=1)).strftime("%Y%m%d")

        seen_urls = set()
        for a in anchors:
            try:
                href = a.get_attribute("href") or ""
                title = (a.inner_text() or "").strip()

                # More permissive URL matching - catch /nasional/ articles with any digit pattern
                # Original strict pattern: /nasional/YYYYMMDDHHMMSS-XX-XXXXXXX/
                # Permissive pattern: any /nasional/ URL with at least 8 consecutive digits
                if "cnnindonesia.com/nasional/" not in href:
                    continue

                # Try strict pattern first to extract date
                ts_match = re.search(r'/nasional/(\d{8,14})', href)
                if not ts_match:
                    continue

                # Filter by date (first 8 digits should be YYYYMMDD)
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


# ─── Detik (national news) ───────────────────────────────────────────

def fetch_detik_browser() -> list[Headline]:
    """
    Detik.com - browser-based scrape of news.detik.com.
    URLs follow pattern: news.detik.com/berita/d-XXXXXXX/headline-slug
    or detik.com/news/berita/d-XXXXXXX/...
    """
    headlines = []
    p = browser = context = None

    try:
        p, browser, context = _launch_browser()
        page = context.new_page()

        page.goto("https://news.detik.com/", timeout=30000, wait_until="domcontentloaded")
        # Allow JS to populate article links
        page.wait_for_timeout(3000)

        anchors = page.query_selector_all("a[href]")

        seen_urls = set()
        for a in anchors:
            try:
                href = a.get_attribute("href") or ""
                title = (a.inner_text() or "").strip()

                # Detik article URLs contain /d-XXXXXXX/ where X is a numeric article ID
                if not re.search(r'detik\.com/[^/]+/[^/]*/?d-\d+/', href):
                    continue

                # Skip non-article patterns (videos, photos, etc.)
                if any(skip in href for skip in ["/video/", "/foto-", "/dvideo/"]):
                    continue

                # Skip empty titles, very short text, or duplicates
                if not title or len(title) < 15:
                    continue
                if href in seen_urls:
                    continue
                seen_urls.add(href)

                # Ensure absolute URL
                if not href.startswith("http"):
                    href = "https://news.detik.com" + href

                headlines.append(Headline(
                    title=title,
                    url=href,
                    source="Detik.com",
                ))
            except Exception:
                continue

        logger.info(f"Fetched {len(headlines)} headlines from Detik.com (browser)")

    except Exception as e:
        logger.error(f"Browser scrape failed for Detik: {e}")
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
    print("\n--- Detik ---")
    for h in fetch_detik_browser():
        print(f"  {h.title}\n    {h.url}")
    print("\n--- Kompas ---")
    for h in fetch_kompas_browser():
        print(f"  {h.title}\n    {h.url}")
    print("\n--- CNN Indonesia ---")
    for h in fetch_cnn_indonesia_browser():
        print(f"  {h.title}\n    {h.url}")
