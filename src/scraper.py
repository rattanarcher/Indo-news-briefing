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


def fetch_rss(feed_url: str, source_name: str, max_items: int = 20, filter_date: bool = True) -> list[Headline]:
    """Fetch headlines from an RSS feed, optionally filtering by recency."""
    headlines = []
    try:
        feed = feedparser.parse(feed_url)
        if feed.bozo and not feed.entries:
            logger.warning(f"RSS parse error for {source_name}: {feed.bozo_exception}")
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
        fetchers.extend([
            ("Kompas.com", fetch_kompas_browser),
        ])

    all_headlines = {}

    for source_name, fetcher in fetchers:
        headlines = fetcher()

        # If RSS returned nothing, try HTML fallback
        if not headlines and source_name in FALLBACK_SELECTORS:
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
