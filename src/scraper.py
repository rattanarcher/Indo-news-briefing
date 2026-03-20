"""
Scraper module for Indonesian news headlines.
Fetches from: Kompas, Tempo, Detik, Antara News
Uses RSS feeds where available, falls back to HTML scraping.
"""

import feedparser
import requests
from bs4 import BeautifulSoup
from dataclasses import dataclass, asdict
from datetime import datetime
import logging
import json

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


def fetch_rss(feed_url: str, source_name: str, max_items: int = 15) -> list[Headline]:
    """Fetch headlines from an RSS feed."""
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

            if title and link:
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


# ─── Source definitions ─────────────────────────────────────────────

def fetch_detik() -> list[Headline]:
    """Detik.com - RSS feed available."""
    return fetch_rss(
        feed_url="https://rss.detik.com/index.php/detikcom",
        source_name="Detik.com"
    )


def fetch_kompas() -> list[Headline]:
    """Kompas.com - RSS feed available."""
    # Try the national news feed first, fall back to alternative URLs
    headlines = fetch_rss(
        feed_url="https://www.kompas.com/getrss/nasional",
        source_name="Kompas.com"
    )
    if not headlines:
        headlines = fetch_rss(
            feed_url="https://rss.kompas.com/kompas-cek-fakta",
            source_name="Kompas.com"
        )
    return headlines


def fetch_tempo() -> list[Headline]:
    """Tempo.co - RSS feed available."""
    return fetch_rss(
        feed_url="https://rss.tempo.co/nasional",
        source_name="Tempo.co"
    )


def fetch_antara() -> list[Headline]:
    """Antara News - English section, RSS feed."""
    # Try the main English news feed first, fall back to latest-news feed
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
    "Kompas.com": {
        "url": "https://www.kompas.com/",
        "selector": {
            "container": ".article__title a, .most__title a",
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
        ("Kompas.com", fetch_kompas),
        ("Tempo.co", fetch_tempo),
        ("Antara News", fetch_antara),
    ]

    all_headlines = {}

    for source_name, fetcher in fetchers:
        headlines = fetcher()

        # If RSS returned nothing, try HTML fallback
        if not headlines and source_name in FALLBACK_SELECTORS:
            fb = FALLBACK_SELECTORS[source_name]
            logger.info(f"RSS empty for {source_name}, trying HTML fallback...")
            headlines = fetch_html(fb["url"], source_name, fb["selector"])

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
