"""
Scraper module for Indonesian news headlines.
Fetches from: Detik, Tempo, Antara News, Jakarta Post, Liputan6
Uses RSS feeds where available, falls back to HTML scraping.
Filters out articles older than 24 hours.
"""

import feedparser
import requests
from bs4 import BeautifulSoup
from dataclasses import dataclass, asdict
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
import logging
import json
import re

logger = logging.getLogger(__name__)

# Request headers to avoid being blocked
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
}

REQUEST_TIMEOUT = 15  # seconds


@dataclass
class Headline:
    title: str
    url: str
    source: str
    published: str = ""

    def to_dict(self):
        return asdict(self)


def parse_published_date(date_str: str) -> datetime | None:
    """
    Try to parse a publication date string into a timezone-aware datetime.
    Handles RFC 2822 (common in RSS) and ISO 8601 formats.
    Returns None if parsing fails.
    """
    if not date_str:
        return None

    # Try RFC 2822 format (e.g., "Mon, 24 Mar 2026 07:00:00 +0700")
    try:
        return parsedate_to_datetime(date_str)
    except Exception:
        pass

    # Try ISO 8601 format (e.g., "2026-03-24T07:00:00+07:00")
    try:
        return datetime.fromisoformat(date_str)
    except Exception:
        pass

    # Try common formats
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
    """
    Check if a published date is within the last max_age_hours.
    Returns True if date can't be parsed (benefit of the doubt).
    Uses 36 hours instead of 24 to account for timezone differences.
    """
    parsed = parse_published_date(date_str)
    if parsed is None:
        return True  # Can't parse date, include it anyway

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

            # Filter out old articles
            if filter_date and not is_recent(published):
                continue

            headlines.append(Headline(
                title=title,
                url=link,
                source=source_name,
                published=published
            ))
        logger.info(f"Fetched {len(headlines)} headlines from {source_name} (RSS)")
    except Exception as e:
        logger.error(f"Error fetching RSS for {source_name}: {e}")

    return headlines


def fetch_html(url: str, source_name: str, selector: dict, max_items: int = 15) -> list[Headline]:
    """
    Fetch headlines by scraping HTML.
    selector dict should have:
        - 'container': CSS selector for headline containers
        - 'title': CSS selector for title within container (or None to use container text)
        - 'link': CSS selector for link within container (or None if container is <a>)
    """
    headlines = []
    try:
        resp = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        items = soup.select(selector["container"])[:max_items]
        for item in items:
            # Get title
            if selector.get("title"):
                title_el = item.select_one(selector["title"])
                title = title_el.get_text(strip=True) if title_el else ""
            else:
                title = item.get_text(strip=True)

            # Get link
            if selector.get("link"):
                link_el = item.select_one(selector["link"])
                link = link_el.get("href", "") if link_el else ""
            elif item.name == "a":
                link = item.get("href", "")
            else:
                a_tag = item.find("a")
                link = a_tag.get("href", "") if a_tag else ""

            # Ensure absolute URL
            if link and not link.startswith("http"):
                link = url.rstrip("/") + "/" + link.lstrip("/")

            if title and link:
                headlines.append(Headline(
                    title=title,
                    url=link,
                    source=source_name
                ))
        logger.info(f"Fetched {len(headlines)} headlines from {source_name} (HTML)")
    except Exception as e:
        logger.error(f"Error scraping HTML for {source_name}: {e}")

    return headlines


def filter_html_headlines_by_url_date(headlines: list[Headline]) -> list[Headline]:
    """
    For HTML-scraped headlines (no published date), try to extract date from URL.
    Many Indonesian news sites embed the date in the URL, e.g.:
    https://www.detik.com/news/berita/d-1234567/2026/03/24/headline
    https://nasional.tempo.co/read/1234567/2026/03/24/headline
    """
    today = datetime.now(timezone(timedelta(hours=7)))  # WIB (Jakarta time)
    today_str = today.strftime("%Y/%m/%d")
    yesterday_str = (today - timedelta(days=1)).strftime("%Y/%m/%d")

    # Also check date formats like /2026/03/24/ or /20260324/
    today_compact = today.strftime("%Y%m%d")
    yesterday_compact = (today - timedelta(days=1)).strftime("%Y%m%d")

    filtered = []
    for h in headlines:
        # If any date-like pattern is in the URL, check it
        date_match = re.search(r'/(\d{4})/(\d{2})/(\d{2})/', h.url)
        if date_match:
            url_date = f"{date_match.group(1)}/{date_match.group(2)}/{date_match.group(3)}"
            if url_date == today_str or url_date == yesterday_str:
                filtered.append(h)
        else:
            # No date in URL, include it (benefit of the doubt)
            filtered.append(h)

    return filtered


# ─── Source definitions ─────────────────────────────────────────────

def fetch_detik() -> list[Headline]:
    """Detik.com - RSS feed available."""
    return fetch_rss(
        feed_url="https://rss.detik.com/index.php/detikcom",
        source_name="Detik.com"
    )


def fetch_tempo() -> list[Headline]:
    """Tempo.co - RSS feed available."""
    return fetch_rss(
        feed_url="https://rss.tempo.co/nasional",
        source_name="Tempo.co"
    )


def fetch_antara() -> list[Headline]:
    """Antara News - English section, RSS feed."""
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


def fetch_cnn_indonesia() -> list[Headline]:
    """CNN Indonesia - RSS feed available."""
    return fetch_rss(
        feed_url="https://www.cnnindonesia.com/nasional/rss",
        source_name="CNN Indonesia"
    )


def fetch_liputan6() -> list[Headline]:
    """Liputan6.com - RSS feed available."""
    return fetch_rss(
        feed_url="https://feed.liputan6.com/rss/news",
        source_name="Liputan6.com"
    )


# Fallback HTML selectors in case RSS feeds break
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
    """
    Fetch headlines from all sources.
    Returns a dict keyed by source name.
    """
    fetchers = [
        ("Detik.com", fetch_detik),
        ("Tempo.co", fetch_tempo),
        ("Antara News", fetch_antara),
        ("CNN Indonesia", fetch_cnn_indonesia),
        ("Liputan6.com", fetch_liputan6),
    ]

    all_headlines = {}

    for source_name, fetcher in fetchers:
        headlines = fetcher()

        # If RSS returned nothing, try HTML fallback
        if not headlines and source_name in FALLBACK_SELECTORS:
            fb = FALLBACK_SELECTORS[source_name]
            logger.info(f"RSS empty for {source_name}, trying HTML fallback...")
            headlines = fetch_html(fb["url"], source_name, fb["selector"])

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
