"""
Scraper module for Indonesian news headlines.
Fetches from: Detik, Tempo, Antara News, CNN Indonesia
Uses RSS feeds where available, falls back to HTML scraping.
Filters out articles older than 36 hours.
"""

import feedparser
import requests
from bs4 import BeautifulSoup
from dataclasses import dataclass, asdict
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
import logging
import re
import time

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
}

REQUEST_TIMEOUT = 15


@dataclass
class Headline:
    title: str
    url: str
    source: str
    published: str = ""

    def to_dict(self):
        return asdict(self)


def parse_published_date(date_str: str) -> datetime | None:
    """Parse a publication date string into a timezone-aware datetime."""
    if not date_str:
        return None
    try:
        return parsedate_to_datetime(date_str)
    except Exception:
        pass
    try:
        return datetime.fromisoformat(date_str)
    except Exception:
        pass
    for fmt in [
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%d %H:%M:%S",
        "%a, %d %b %Y %H:%M:%S %z",
        "%a, %d %b %Y %H:%M:%S",
    ]:
        try:
            dt = datetime.strptime(date_str, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            continue
    return None


def is_recent(date_str: str, max_age_hours: int = 36) -> bool:
    """Check if a published date is within the last max_age_hours."""
    parsed = parse_published_date(date_str)
    if parsed is None:
        return True
    now = datetime.now(timezone.utc)
    age = now - parsed
    return age < timedelta(hours=max_age_hours)


def _sanitise_xml(raw: str) -> str:
    """
    Repair the most common reasons feedparser reports 'undefined entity' on
    otherwise-valid feeds: bare ampersands and HTML named entities that are not
    predefined in XML. XML only predefines amp, lt, gt, quot, apos.
    """
    if not raw:
        return raw
    # Map common HTML entities to their XML-safe numeric equivalents
    named = {
        "&nbsp;": "&#160;", "&mdash;": "&#8212;", "&ndash;": "&#8211;",
        "&rsquo;": "&#8217;", "&lsquo;": "&#8216;", "&rdquo;": "&#8221;",
        "&ldquo;": "&#8220;", "&hellip;": "&#8230;", "&eacute;": "&#233;",
        "&agrave;": "&#224;", "&uuml;": "&#252;", "&copy;": "&#169;",
        "&reg;": "&#174;", "&trade;": "&#8482;", "&deg;": "&#176;",
        "&times;": "&#215;", "&middot;": "&#183;", "&bull;": "&#8226;",
    }
    for k, v in named.items():
        raw = raw.replace(k, v)
    # Escape any remaining bare ampersand that is not already part of a valid
    # entity (numeric &#123; / &#x1F; or one of the five XML-predefined names).
    raw = re.sub(r'&(?!#\d+;|#x[0-9A-Fa-f]+;|amp;|lt;|gt;|quot;|apos;)', '&amp;', raw)
    return raw


def fetch_rss(feed_url: str, source_name: str, max_items: int = 20, filter_date: bool = True) -> list[Headline]:
    """Fetch headlines from an RSS feed, optionally filtering by recency."""
    headlines = []
    try:
        # Fetch each feed on its OWN fresh connection, and retry transient
        # network errors. Some sources (Antara, Detik) intermittently reset the
        # connection mid-run (RemoteDisconnected / WinError 10054). A reset can
        # otherwise poison the pool and make the NEXT source fail too, which is
        # what made Tempo look broken when the real fault was the source before
        # it. Connection: close + a fresh Session per attempt isolates each fetch.
        raw = None
        last_err = None
        for attempt in range(3):
            try:
                with requests.Session() as sess:
                    sess.headers.update({"Connection": "close"})
                    resp = sess.get(feed_url, timeout=REQUEST_TIMEOUT)
                    resp.raise_for_status()
                    raw = resp.text
                break
            except requests.exceptions.HTTPError as e:
                # A real HTTP status (403/404 etc.) will not change on retry
                last_err = e
                break
            except (requests.exceptions.ConnectionError,
                    requests.exceptions.ChunkedEncodingError,
                    requests.exceptions.Timeout) as e:
                # Transient: reset / aborted connection. Wait and retry fresh.
                last_err = e
                time.sleep(1.0 * (attempt + 1))
                continue
            except Exception as e:
                last_err = e
                break

        if raw is None:
            logger.error(f"Error fetching RSS for {source_name}: {last_err}")
            return headlines

        feed = feedparser.parse(raw)

        # If malformed (e.g. Tempo's 'undefined entity'), sanitise the bytes we
        # already have and re-parse. No extra network request.
        if feed.bozo and not feed.entries:
            logger.warning(f"RSS parse error for {source_name}: {feed.bozo_exception}; sanitising")
            feed = feedparser.parse(_sanitise_xml(raw))
            if feed.entries:
                logger.info(f"Sanitiser recovered {len(feed.entries)} entries for {source_name}")

        if feed.bozo and not feed.entries:
            return headlines

        for entry in feed.entries[:max_items]:
            title = entry.get("title", "").strip()
            link = entry.get("link", "").strip()
            published = entry.get("published", "")

            if not title or not link:
                continue
            if filter_date and not is_recent(published):
                continue

            headlines.append(Headline(
                title=title, url=link, source=source_name, published=published
            ))
        logger.info(f"Fetched {len(headlines)} headlines from {source_name} (RSS)")
    except Exception as e:
        logger.error(f"Error fetching RSS for {source_name}: {e}")
    return headlines


def fetch_html(url: str, source_name: str, selector: dict, max_items: int = 15) -> list[Headline]:
    """Fetch headlines by scraping HTML."""
    headlines = []
    try:
        resp = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        items = soup.select(selector["container"])[:max_items]
        for item in items:
            if selector.get("title"):
                title_el = item.select_one(selector["title"])
                title = title_el.get_text(strip=True) if title_el else ""
            else:
                title = item.get_text(strip=True)

            if selector.get("link"):
                link_el = item.select_one(selector["link"])
                link = link_el.get("href", "") if link_el else ""
            elif item.name == "a":
                link = item.get("href", "")
            else:
                a_tag = item.find("a")
                link = a_tag.get("href", "") if a_tag else ""

            if link and not link.startswith("http"):
                link = url.rstrip("/") + "/" + link.lstrip("/")

            if title and link:
                headlines.append(Headline(title=title, url=link, source=source_name))
        logger.info(f"Fetched {len(headlines)} headlines from {source_name} (HTML)")
    except Exception as e:
        logger.error(f"Error scraping HTML for {source_name}: {e}")
    return headlines


# ─── Source definitions ─────────────────────────────────────────────

def fetch_detik() -> list[Headline]:
    """Detik.com - RSS feed with HTML fallback."""
    return fetch_rss(
        feed_url="https://rss.detik.com/index.php/detikcom",
        source_name="Detik.com"
    )


def fetch_tempo() -> list[Headline]:
    """Tempo.co - Nasional + Dunia (international) RSS feeds."""
    national = fetch_rss(
        feed_url="https://rss.tempo.co/nasional",
        source_name="Tempo.co"
    )
    # Brief pause: Tempo's Cloudflare rate-limits rapid back-to-back requests,
    # so space the two feed fetches out rather than firing them instantly.
    time.sleep(1.5)
    # Tempo's international feed is called "dunia", not "internasional"
    dunia = fetch_rss(
        feed_url="https://rss.tempo.co/dunia",
        source_name="Tempo.co (Dunia)"
    )
    return national + dunia


def fetch_antara() -> list[Headline]:
    """Antara News - English general news feed."""
    headlines = fetch_rss(
        feed_url="https://en.antaranews.com/rss/news.xml",
        source_name="Antara News"
    )
    if not headlines:
        headlines = fetch_rss(
            feed_url="https://en.antaranews.com/rss/latest-news.xml",
            source_name="Antara News"
        )
    return headlines


def fetch_antara_international() -> list[Headline]:
    """Antara News - Bahasa international feed (covers foreign affairs, diplomacy, defence)."""
    return fetch_rss(
        feed_url="https://www.antaranews.com/rss/dunia-internasional.xml",
        source_name="Antara News International"
    )


def fetch_republika() -> list[Headline]:
    """Republika Online - general RSS feed covering nasional and internasional."""
    return fetch_rss(
        feed_url="https://www.republika.co.id/rss/",
        source_name="Republika"
    )


# Fallback HTML selectors
FALLBACK_SELECTORS = {
    "Detik.com": {
        "url": "https://www.detik.com/",
        "selector": {
            "container": "article h3 a, .media__title a",
            "title": None,
            "link": None,
        }
    },
    "Tempo.co": {
        "url": "https://www.tempo.co/",
        "selector": {
            "container": "article h2 a, .title a",
            "title": None,
            "link": None,
        }
    },
}


def fetch_all_headlines() -> dict[str, list[Headline]]:
    """Fetch headlines from all sources."""
    # Lazy import browser scrapers so the module still loads if Playwright is missing
    try:
        from src.scraper_browser import (
            fetch_kompas_browser,
            fetch_detik_browser,
        )
        browser_available = True
    except ImportError as e:
        logger.warning(f"Playwright not available, skipping browser-based scrapers: {e}")
        browser_available = False

    fetchers = [
        ("Detik.com", fetch_detik),
        ("Tempo.co", fetch_tempo),
        ("Antara News", fetch_antara),
        ("Antara News International", fetch_antara_international),
        ("Republika", fetch_republika),
    ]

    if browser_available:
        # Kompas is browser-first (it blocks direct requests but not the
        # browser). Tempo is RSS-only: Cloudflare blocks its homepage, section
        # pages and sitemap to automated browsers, but rss.tempo.co stays open,
        # so we read the feed (sanitised for malformed XML) and do not attempt
        # the browser/HTML paths that Cloudflare denies.
        fetchers.append(("Kompas.com", fetch_kompas_browser))

    all_headlines = {}

    for source_name, fetcher in fetchers:
        headlines = fetcher()

        # If RSS returned nothing, try HTML fallback. Tempo is excluded: its
        # HTML is behind Cloudflare (403), so only the RSS sanitiser can help.
        if not headlines and source_name in FALLBACK_SELECTORS and source_name != "Tempo.co":
            fb = FALLBACK_SELECTORS[source_name]
            logger.info(f"RSS empty for {source_name}, trying HTML fallback...")
            headlines = fetch_html(fb["url"], source_name, fb["selector"])

        # Detik special case: if both RSS and HTML failed, try browser scraper
        if not headlines and source_name == "Detik.com" and browser_available:
            logger.info(f"RSS and HTML both failed for Detik, trying browser scraper...")
            headlines = fetch_detik_browser()

        # Deduplicate by URL
        seen_urls = set()
        unique_headlines = []
        for h in headlines:
            if h.url not in seen_urls:
                seen_urls.add(h.url)
                unique_headlines.append(h)
        headlines = unique_headlines

        all_headlines[source_name] = headlines

        if not headlines:
            logger.warning(f"No headlines fetched from {source_name}")

    total = sum(len(v) for v in all_headlines.values())
    logger.info(f"Total headlines fetched: {total}")
    return all_headlines


def headlines_to_text(all_headlines: dict[str, list[Headline]]) -> str:
    """Format headlines as plain text for summarization, including URLs."""
    lines = []
    for source, headlines in all_headlines.items():
        lines.append(f"\n=== {source} ===")
        for i, h in enumerate(headlines, 1):
            lines.append(f"{i}. {h.title}")
            lines.append(f"   URL: {h.url}")
            if h.published:
                lines.append(f"   Published: {h.published}")
    return "\n".join(lines)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    results = fetch_all_headlines()
    print(headlines_to_text(results))
