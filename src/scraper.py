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


def fetch_tempo_raw() -> list[Headline]:
    """
    Fetch Tempo directly from its RSS feeds. Works from a RESIDENTIAL IP but
    NOT from the GitHub Actions datacentre IP (Cloudflare 403). The hosted
    pipeline never calls this; the local tools/refresh_tempo_cache.py script
    does, on your home connection, and commits the result. fetch_tempo() (the
    cache reader) is what the pipeline uses.
    """
    import time
    national = fetch_rss(
        feed_url="https://rss.tempo.co/nasional",
        source_name="Tempo.co"
    )
    time.sleep(1.5)  # space the two requests to avoid a rapid burst
    dunia = fetch_rss(
        feed_url="https://rss.tempo.co/dunia",
        source_name="Tempo.co (Dunia)"
    )
    return national + dunia


def fetch_tempo() -> list[Headline]:
    """
    Read Tempo headlines from the cache file your machine commits, rather than
    fetching them here. Tempo blocks the GitHub Actions datacentre IP, so the
    runner cannot fetch Tempo directly. Your local machine refreshes the cache
    on a residential IP (see tools/refresh_tempo_cache.py).

    If the cache is missing or older than TEMPO_CACHE_MAX_AGE_HOURS, Tempo is
    quietly skipped for that run and the other sources carry the briefing.
    """
    import os, json
    TEMPO_CACHE_PATH = os.environ.get("TEMPO_CACHE_PATH", "tempo_cache.json")
    TEMPO_CACHE_MAX_AGE_HOURS = int(os.environ.get("TEMPO_CACHE_MAX_AGE_HOURS", "24"))
    try:
        if not os.path.exists(TEMPO_CACHE_PATH):
            logger.info("Tempo cache not found; skipping Tempo this run "
                        "(run tools/refresh_tempo_cache.py on your machine to populate it)")
            return []

        with open(TEMPO_CACHE_PATH, "r", encoding="utf-8") as f:
            cache = json.load(f)

        fetched_at = cache.get("fetched_at")
        age_hours = None
        if fetched_at:
            try:
                ts = datetime.fromisoformat(fetched_at)
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                age_hours = (datetime.now(timezone.utc) - ts).total_seconds() / 3600
            except Exception:
                age_hours = None

        if age_hours is not None and age_hours > TEMPO_CACHE_MAX_AGE_HOURS:
            logger.warning(f"Tempo cache is stale ({age_hours:.0f}h old > "
                           f"{TEMPO_CACHE_MAX_AGE_HOURS}h); skipping Tempo this run")
            return []

        items = cache.get("headlines", [])
        headlines = [
            Headline(
                title=h.get("title", ""),
                url=h.get("url", ""),
                source=h.get("source", "Tempo.co"),
                published=h.get("published", ""),
            )
            for h in items if h.get("title") and h.get("url")
        ]
        age_str = f"{age_hours:.0f}h old" if age_hours is not None else "age unknown"
        logger.info(f"Loaded {len(headlines)} Tempo headlines from cache ({age_str})")
        return headlines

    except Exception as e:
        logger.error(f"Error reading Tempo cache (skipping Tempo): {e}")
        return []



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
        ("Antara News", fetch_antara),
        ("Antara News International", fetch_antara_international),
        ("Republika", fetch_republika),
    ]

# Tempo reads from the residential-IP cache (see fetch_tempo), so it is
    # always in the base list. Kompas stays browser-first.
    fetchers.append(("Tempo.co", fetch_tempo))
    if browser_available:
        fetchers.append(("Kompas.com", fetch_kompas_browser))

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
