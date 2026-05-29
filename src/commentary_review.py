"""
Commentary review module (Monday only) — v47.

Surveys expert commentary on Indonesia from five major outlets over the
past week, selects up to 5 most consequential pieces, and writes one
paragraph each for the Monday briefing.

Discovery uses three mechanisms because no single method reaches all
five outlets reliably:

  1. RSS via feedparser, for The Diplomat, Fulcrum, and Indonesia at
     Melbourne. These feeds parse cleanly with a browser User-Agent.
  2. HTML index scrape of csis.or.id/publications/commentaries/, for
     CSIS Indonesia. Their RSS endpoint 404s. The index has no dates,
     so dates are resolved per-piece via metadata fetch at the strict
     filter step. CSIS publishes in both English and Bahasa Indonesia,
     and a heuristic filter at discovery strips obvious Bahasa titles.
  3. Claude's server-side web_search, for East Asia Forum. EAF blocks
     direct fetching and RSS access at the request level. Going
     through a search index bypasses this. URL date pattern
     (/YYYY/MM/DD/) gives us dates without needing to fetch the page.

Each discovery method returns candidates in the same shape:
{title, url, outlet, author, date, teaser}. Date may be None where the
discovery source doesn't expose one (CSIS index, EAF before URL parse).

The combined pool flows through:
  a. Soft date pre-filter: drop candidates whose date is known and
     >14 days old. Candidates with no known date pass through.
  b. Shortlist call: single Claude call applies the English-only and
     substance filters and picks up to 8.
  c. Article body fetch: Python pulls full text for the shortlist.
     Bot-blocked fetches (EAF) fall back to the discovery teaser.
  d. Paragraph call: second Claude call writes 3-4 sentence summaries
     with anchor phrases for hyperlinking.
  e. Strict 7-day filter using _resolve_date.
  f. HTML build.

Returns HTML <p> paragraphs on success, or an empty string on failure
or if no qualifying pieces remain. The emailer wraps the result in the
section <h2>; this module returns paragraphs only, to avoid the
duplicate-heading bug that surfaced in earlier prototypes.
"""

import json
import re
import logging
from datetime import datetime, timedelta

import feedparser
import requests
from bs4 import BeautifulSoup
import anthropic

logger = logging.getLogger(__name__)

# Browser User-Agent. feedparser's default UA is rejected by some
# outlets; this UA is also used for all direct HTTP fetches (CSIS index,
# article bodies, metadata pulls).
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)
HEADERS = {"User-Agent": USER_AGENT}
TIMEOUT = 20

# Outlets reachable via RSS. EAF was tried here in earlier versions but
# the feed is bot-blocked. CSIS Indonesia has no working RSS endpoint.
RSS_FEEDS = [
    ("The Diplomat", "https://thediplomat.com/countries/indonesia/feed/"),
    ("Fulcrum", "https://fulcrum.sg/tag/indonesia/feed/"),
    ("Indonesia at Melbourne", "https://indonesiaatmelbourne.unimelb.edu.au/feed/"),
]

CSIS_INDEX_URL = "https://www.csis.or.id/publications/commentaries/"

# Heuristic Bahasa filter for the CSIS scrape. The index mixes English
# and Bahasa Indonesia commentaries; we want English only. Match on
# common function/content words highly diagnostic of Bahasa. Permissive
# by design: false negatives (Bahasa slipping through) are caught
# downstream by Claude's English-only instruction.
BAHASA_MARKERS = {
    "dan", "yang", "untuk", "dengan", "dari", "ke", "di",
    "atau", "pada", "ini", "itu", "tidak", "akan",
    "kebijakan", "tekanan", "skenario", "lanskap", "dampak",
    "kompleksitas", "ketahanan", "ketidakpastian", "membangun",
    "putusan", "perubahan", "pemilu", "perdagangan", "investasi",
    "berkelanjutan", "kekuatan", "hukum", "tentang", "arah",
}


def _is_likely_bahasa(title: str) -> bool:
    """Return True if the title contains diagnostic Bahasa markers."""
    if not title:
        return False
    tokens = re.findall(r"\b\w+\b", title.lower())
    return any(t in BAHASA_MARKERS for t in tokens)


# ─── Date resolution (carried over from v46, unchanged) ──────────────

def _date_from_url(url: str):
    """
    Extract a full publication date from /YYYY/MM/DD/ in a URL path.
    No network. Used for EAF (where direct fetching is blocked) and as
    a fast first-pass for any outlet with date-stamped URLs.
    """
    if not url:
        return None
    m = re.search(r"/(\d{4})/(\d{2})/(\d{2})/", url)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            pass
    return None


def _parse_iso_date(s: str):
    """Pull the first YYYY-MM-DD out of an ISO-ish string."""
    if not s:
        return None
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", s)
    if not m:
        return None
    try:
        return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def _fetch_published_date(url: str):
    """
    Fetch a page and read its publish date from machine-readable
    metadata. Tries article:published_time, JSON-LD datePublished, and
    itemprop=datePublished in that order. Returns datetime or None.
    """
    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
    except Exception as e:
        logger.info(f"date-extract: fetch failed for {url}: {e}")
        return None

    try:
        soup = BeautifulSoup(r.text, "html.parser")

        m = soup.find("meta", attrs={"property": "article:published_time"})
        if m and m.get("content"):
            d = _parse_iso_date(m["content"])
            if d:
                return d

        for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
            txt = script.string or script.get_text() or ""
            mm = re.search(r'"datePublished"\s*:\s*"([^"]+)"', txt)
            if mm:
                d = _parse_iso_date(mm.group(1))
                if d:
                    return d

        m2 = soup.find(attrs={"itemprop": "datePublished"})
        if m2:
            val = m2.get("content") or m2.get("datetime") or m2.get_text()
            d = _parse_iso_date(val)
            if d:
                return d
    except Exception as e:
        logger.info(f"date-extract: parse failed for {url}: {e}")

    return None


def _resolve_date(entry: dict):
    """
    Authoritative publication date for an entry, in order:
      1. URL-path date (no network, immune to bot-blocking; covers EAF)
      2. Page metadata via fetch (covers slug-URL sites that allow it)
      3. Model-reported date as last resort
    Returns datetime or None (None = cannot confirm, piece is dropped).
    """
    url = str(entry.get("url", "")).strip()
    d = _date_from_url(url)
    if d:
        return d
    if url:
        meta_date = _fetch_published_date(url)
        if meta_date:
            return meta_date
    return _parse_iso_date(str(entry.get("date", "")))


# ─── Article body fetch (for paragraph writing) ───────────────────────

def _fetch_article_text(url: str, max_chars: int = 3500) -> str:
    """
    Pull clean readable text from the article page. Used so Claude
    writes paragraphs from the actual argument rather than the RSS
    teaser. Returns plain text capped at max_chars, or empty string on
    failure. EAF is bot-blocked and will return ""; callers should fall
    back to the discovery snippet in that case.
    """
    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
    except Exception as e:
        logger.info(f"article fetch failed for {url}: {e}")
        return ""
    try:
        soup = BeautifulSoup(r.text, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "aside", "form", "iframe"]):
            tag.decompose()
        container = soup.find("article") or soup.find("main") or soup.body
        if not container:
            return ""
        text = container.get_text(separator=" ", strip=True)
        text = re.sub(r"\s+", " ", text)
        return text[:max_chars]
    except Exception as e:
        logger.info(f"article parse failed for {url}: {e}")
        return ""


# ─── Discovery: RSS (Diplomat, Fulcrum, Indonesia at Melbourne) ──────

def _discover_rss() -> list[dict]:
    """Parse the three working RSS feeds with a browser UA."""
    out = []
    for outlet, url in RSS_FEEDS:
        try:
            feed = feedparser.parse(url, agent=USER_AGENT)
            count = 0
            for entry in feed.entries[:12]:
                author = (getattr(entry, "author", None)
                          or getattr(entry, "dc_creator", None)
                          or None)
                pub_date = None
                if getattr(entry, "published_parsed", None):
                    pub_date = datetime(*entry.published_parsed[:6])
                elif getattr(entry, "updated_parsed", None):
                    pub_date = datetime(*entry.updated_parsed[:6])
                teaser_raw = entry.get("summary", entry.get("description", "")) or ""
                teaser = BeautifulSoup(teaser_raw, "html.parser").get_text(" ", strip=True)
                out.append({
                    "title": (entry.get("title") or "").strip(),
                    "url": (entry.get("link") or "").strip(),
                    "outlet": outlet,
                    "author": author.strip() if isinstance(author, str) else None,
                    "date": pub_date.strftime("%Y-%m-%d") if pub_date else None,
                    "teaser": teaser[:600],
                })
                count += 1
            logger.info(f"RSS: fetched {count} from {outlet}")
        except Exception as e:
            logger.warning(f"RSS failed for {outlet}: {e}")
    return out


# ─── Discovery: CSIS Indonesia HTML scrape ───────────────────────────

# CSIS article pages list the 5 most recent commentaries in a "Recent"
# sidebar, each rendered as an anchor followed by a date string like
# "18 may 2026". This is the only place on CSIS where dates appear
# machine-readably for commentaries, since the article pages themselves
# have no article:published_time, JSON-LD datePublished, or itemprop
# date metadata. We fetch one article page per Monday run and use its
# sidebar as a URL->date oracle to plug the metadata gap.

_CSIS_MONTH = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}
_CSIS_DATE_RE = re.compile(
    r"\b(\d{1,2})\s+(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\s+(\d{4})\b"
)


def _normalise_csis_url(url: str) -> str:
    """Canonical form for CSIS publication URLs (for date-map lookups)."""
    if not url:
        return ""
    if url.startswith("/"):
        url = "https://www.csis.or.id" + url
    return url.rstrip("/").lower()


def _parse_csis_date_string(s: str):
    """Parse 'DD mon YYYY' (lowercase month abbr) into 'YYYY-MM-DD'. None on failure."""
    if not s:
        return None
    m = _CSIS_DATE_RE.search(s.lower())
    if not m:
        return None
    try:
        return datetime(int(m.group(3)), _CSIS_MONTH[m.group(2)], int(m.group(1))).strftime("%Y-%m-%d")
    except (ValueError, KeyError):
        return None


def _fetch_csis_date_map(article_url: str) -> dict:
    """
    Fetch a CSIS article page and parse the 'Recent commentaries' sidebar
    into a URL -> ISO date map. Returns {} on any failure. Only the 5
    most recent commentaries appear in the sidebar; older CSIS candidates
    will not get a date from this map and will be dropped at the strict
    filter (which is the right outcome, since they are stale).
    """
    try:
        r = requests.get(article_url, headers=HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
    except Exception as e:
        logger.info(f"CSIS date map: fetch failed for {article_url}: {e}")
        return {}

    out = {}
    try:
        soup = BeautifulSoup(r.text, "html.parser")
        for a in soup.find_all("a", href=re.compile(r"/publication/[^/]")):
            url = _normalise_csis_url((a.get("href") or "").strip())
            if not url or url in out:
                continue
            # Look for a 'DD mon YYYY' in the parent's text AFTER this
            # anchor's own text. This is robust to whether the date sits
            # as a sibling text node, in a span, or inline.
            parent = a.parent
            if parent is None:
                continue
            parent_text = parent.get_text(" ", strip=True)
            anchor_text = a.get_text(" ", strip=True)
            idx = parent_text.find(anchor_text) if anchor_text else -1
            after = parent_text[idx + len(anchor_text):] if idx >= 0 else parent_text
            iso = _parse_csis_date_string(after)
            if iso:
                out[url] = iso
    except Exception as e:
        logger.info(f"CSIS date map: parse failed: {e}")
        return {}

    logger.info(f"CSIS date map: extracted {len(out)} URL->date mappings")
    return out


def _discover_csis() -> list[dict]:
    """
    Scrape the CSIS commentaries index. The index has no dates, so
    candidates are returned date=None and dates resolve via metadata
    fetch later. Bahasa-marked titles are dropped at this step.
    """
    try:
        r = requests.get(CSIS_INDEX_URL, headers=HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
    except Exception as e:
        logger.warning(f"CSIS scrape: fetch failed: {e}")
        return []

    out = []
    try:
        soup = BeautifulSoup(r.text, "html.parser")
        seen_urls = set()
        # Find headings (h2/h3) containing a /publication/<slug> link.
        # Sidebar/nav links don't sit inside heading tags, so this
        # filter rejects them. Adjust if CSIS redesigns the index.
        for heading in soup.find_all(["h2", "h3"]):
            a = heading.find("a", href=re.compile(r"/publication/[^/]"))
            if not a:
                continue
            url = (a.get("href") or "").strip()
            if url.startswith("/"):
                url = "https://www.csis.or.id" + url
            if not url or url in seen_urls:
                continue
            title = a.get_text(" ", strip=True)
            if not title:
                continue
            if _is_likely_bahasa(title):
                logger.info(f"CSIS scrape: skipping Bahasa-marked title: {title!r}")
                continue

            # Author is usually a short text node in the next sibling.
            # Walk forward a few siblings to find it.
            author = None
            sib = heading.find_next_sibling()
            for _ in range(4):
                if sib is None:
                    break
                txt = sib.get_text(" ", strip=True) if hasattr(sib, "get_text") else str(sib).strip()
                if (txt and 0 < len(txt) < 120
                        and not txt.lower().startswith(("commentaries", "publication"))
                        and "http" not in txt):
                    author = txt
                    break
                sib = sib.find_next_sibling()

            seen_urls.add(url)
            out.append({
                "title": title,
                "url": url,
                "outlet": "CSIS Indonesia",
                "author": author,
                "date": None,
                "teaser": "",
            })
            if len(out) >= 12:
                break
    except Exception as e:
        logger.warning(f"CSIS scrape: parse failed: {e}")
        return []

    logger.info(f"CSIS scrape: {len(out)} candidates after Bahasa filter")

    # Enrich with dates from the "Recent" sidebar of one article page,
    # since CSIS exposes no machine-readable dates on the article pages
    # themselves. We fetch one (the first candidate, by index order) and
    # use its sidebar as a URL->date oracle. Candidates not found in the
    # map (i.e. not in CSIS's 5 most recent) remain dateless and will be
    # dropped at the strict filter, which is correct since they are stale.
    if out:
        date_map = _fetch_csis_date_map(out[0]["url"])
        if date_map:
            enriched = 0
            for c in out:
                key = _normalise_csis_url(c["url"])
                if key in date_map:
                    c["date"] = date_map[key]
                    enriched += 1
            logger.info(f"CSIS scrape: enriched {enriched} of {len(out)} candidate dates from sidebar")

    return out


# ─── Discovery: East Asia Forum via Claude web_search ────────────────

EAF_DISCOVERY_PROMPT = """Use the web_search tool to find recent East Asia Forum articles about Indonesia.

Run searches for: site:eastasiaforum.org Indonesia {year}

From the results, return up to 10 articles whose URL contains "/{year}/" (i.e. published this year). For each, output a JSON object with exactly these fields:
- "url": the full article URL (must contain /YYYY/MM/DD/ in the path)
- "title": the article title
- "snippet": the search result snippet, verbatim, no more than 250 characters

Return ONLY a JSON array, no preamble, no markdown fences. If you find nothing, return [].

Return the JSON array now."""


def _discover_eaf(client, model: str, year: int) -> list[dict]:
    """
    Use Claude's server-side web_search to find recent EAF Indonesia
    pieces. EAF blocks direct fetching and RSS, so search bypasses by
    hitting Google's index. URL date pattern handles recency downstream.
    """
    try:
        tools = [{"type": "web_search_20250305", "name": "web_search", "max_uses": 3}]
        prompt = EAF_DISCOVERY_PROMPT.format(year=year)
        messages = [{"role": "user", "content": prompt}]

        for _ in range(8):
            resp = client.messages.create(
                model=model,
                max_tokens=2500,
                tools=tools,
                messages=messages,
            )
            if resp.stop_reason == "pause_turn":
                messages.append({"role": "assistant", "content": resp.content})
                continue

            raw = "".join(
                b.text for b in resp.content
                if getattr(b, "type", None) == "text"
            ).strip()
            entries = _parse_json_array(raw)
            out = []
            for e in entries:
                url = (e.get("url") or "").strip()
                if not url or "eastasiaforum.org" not in url:
                    continue
                out.append({
                    "title": (e.get("title") or "").strip(),
                    "url": url,
                    "outlet": "East Asia Forum",
                    "author": None,
                    "date": None,
                    "teaser": (e.get("snippet") or "").strip()[:600],
                })
            logger.info(f"EAF web_search: {len(out)} candidates")
            return out

        logger.warning("EAF web_search: tool loop did not converge")
        return []
    except Exception as e:
        logger.warning(f"EAF web_search failed: {e}")
        return []


# ─── JSON helper ──────────────────────────────────────────────────────

def _parse_json_array(raw: str) -> list:
    """Pull a JSON array out of model output. Returns [] on any failure."""
    if not raw:
        return []
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end == -1 or end < start:
        return []
    try:
        data = json.loads(text[start:end + 1])
    except Exception as e:
        logger.warning(f"JSON parse failed: {e}")
        return []
    return data if isinstance(data, list) else []


# ─── Shortlist + paragraph writing ────────────────────────────────────

SHORTLIST_PROMPT = """You are an expert news analyst covering Indonesia. Below is a candidate pool of recent expert commentary on Indonesia, drawn from East Asia Forum, The Diplomat, Fulcrum, CSIS Indonesia, and Indonesia at Melbourne.

Your task: select up to 8 of the most substantive, Indonesia-focused pieces for further review. Apply these filters strictly:

- ENGLISH ONLY. CSIS Indonesia publishes in both English and Bahasa Indonesia; exclude Bahasa pieces. If the title looks Bahasa or you cannot tell, prefer to exclude.
- DIRECT INDONESIA FOCUS. A piece merely mentioning Indonesia while focused on ASEAN, China, or US policy does NOT qualify.
- SUBSTANTIVE POLICY OR POLITICAL ANALYSIS. Engages seriously with domestic politics, government policy, defence, foreign policy, institutional change, or major economic decisions. Exclude podcast episode notes (especially "Talking Indonesia"), commemorative essays, and light overviews.

Return ONLY a JSON array of objects with these exact fields:
- "url": the article URL
- "outlet": the outlet name
- "author": the author name as given in the candidate (or null if not provided)

Return at most 8 objects, ordered by significance most-first. If fewer than 2 qualify, return [].

Candidate pool:
{candidates}

Return the JSON array now."""


PARAGRAPH_PROMPT = """You are an expert news analyst writing the "Expert Commentary This Week" section of a Monday Indonesia briefing. Below are shortlisted articles with their full text (or, where the full text could not be fetched, with the available teaser).

For each article, write one 3-4 sentence paragraph summarising the piece's argument. Requirements:

- Start each paragraph with the attribution woven in naturally, e.g. "Writing in East Asia Forum, Edward Aspinall argues that..." Use the author's name where provided; if no author is available, use "analysts at OUTLET" or "OUTLET's commentary" naturally.
- Report what the piece argues and why it matters. Do not insert your own view.
- Identify a 3-7 word anchor phrase that captures the central claim. The anchor phrase MUST appear verbatim in the paragraph. It will be hyperlinked downstream.
- Do not quote more than 10 words verbatim from the article.
- Do not include any HTML in the paragraph field. Plain prose only.

Return ONLY a JSON array of objects with these exact fields:
- "url": the article URL (copy from input)
- "outlet": the outlet name (copy from input)
- "author": the author's name (or "Staff" if unknown)
- "anchor": the 3-7 word anchor phrase that appears verbatim in the paragraph
- "paragraph": the 3-4 sentence summary

Return at most 8 objects, ordered by significance most-first.

Shortlisted articles:
{articles}

Return the JSON array now."""


def _format_candidates_for_shortlist(candidates: list[dict]) -> str:
    lines = []
    for i, c in enumerate(candidates, 1):
        author = c.get("author") or "unknown"
        date = c.get("date") or "date unknown"
        teaser = (c.get("teaser") or "").strip()
        if len(teaser) > 350:
            teaser = teaser[:350] + "..."
        lines.append(
            f"{i}. [{c.get('outlet', '?')}] ({date}) by {author}\n"
            f"   Title: {c.get('title', '')}\n"
            f"   URL: {c.get('url', '')}\n"
            f"   Teaser: {teaser}\n"
        )
    return "\n".join(lines)


def _format_shortlist_for_paragraphs(items: list[dict]) -> str:
    blocks = []
    for i, it in enumerate(items, 1):
        author = it.get("author") or "unknown"
        body = (it.get("text") or "").strip()
        if not body:
            body = (it.get("teaser") or "(no body text available, use title only)").strip()
        blocks.append(
            f"--- Article {i} ---\n"
            f"Outlet: {it.get('outlet', '?')}\n"
            f"Author: {author}\n"
            f"URL: {it.get('url', '')}\n"
            f"Title: {it.get('title', '')}\n"
            f"Body:\n{body}\n"
        )
    return "\n".join(blocks)


def _shortlist(client, candidates: list[dict], model: str) -> list[dict]:
    """Single Claude call. Returns shortlist entries merged with original candidate data."""
    if not candidates:
        return []
    candidates_text = _format_candidates_for_shortlist(candidates)
    prompt = SHORTLIST_PROMPT.format(candidates=candidates_text)
    try:
        resp = client.messages.create(
            model=model,
            max_tokens=2000,
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception as e:
        logger.error(f"Shortlist call failed: {e}")
        return []
    raw = "".join(
        b.text for b in resp.content
        if getattr(b, "type", None) == "text"
    ).strip()
    picked = _parse_json_array(raw)

    # Merge model picks back onto the full candidate dicts so we keep
    # title/teaser/date for downstream steps.
    by_url = {c.get("url", ""): c for c in candidates}
    out = []
    for p in picked:
        if not isinstance(p, dict):
            continue
        url = (p.get("url") or "").strip()
        c = by_url.get(url)
        if not c:
            continue
        merged = dict(c)
        if not merged.get("author") and p.get("author"):
            merged["author"] = p["author"]
        out.append(merged)
    logger.info(f"Shortlist: Claude picked {len(out)} of {len(candidates)} candidates")
    return out


def _write_paragraphs(client, shortlist_with_text: list[dict], model: str) -> list[dict]:
    """
    Second Claude call. Returns final entries with anchor + paragraph
    fields, merged back onto the input items by URL so candidate fields
    not echoed by the model (most importantly "date" for CSIS pieces
    enriched from the sidebar map) survive into the strict date filter.
    """
    if not shortlist_with_text:
        return []
    articles_text = _format_shortlist_for_paragraphs(shortlist_with_text)
    prompt = PARAGRAPH_PROMPT.format(articles=articles_text)
    try:
        resp = client.messages.create(
            model=model,
            max_tokens=5000,
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception as e:
        logger.error(f"Paragraph call failed: {e}")
        return []
    raw = "".join(
        b.text for b in resp.content
        if getattr(b, "type", None) == "text"
    ).strip()
    written = _parse_json_array(raw)

    # Merge model output back onto the input items by URL. Without this,
    # fields not in the model's JSON schema (such as the candidate's
    # original "date") get dropped before the strict filter, which would
    # cause CSIS pieces with sidebar-resolved dates to be wrongly flagged
    # as "no confirmable date".
    by_url = {it.get("url", ""): it for it in shortlist_with_text}
    out = []
    for entry in written:
        if not isinstance(entry, dict):
            continue
        url = (entry.get("url") or "").strip()
        base = by_url.get(url, {})
        merged = dict(base)
        merged.update({k: v for k, v in entry.items() if v is not None})
        out.append(merged)
    return out


# ─── Final filtering + HTML build ─────────────────────────────────────

def _filter_by_date(entries: list[dict], cutoff: datetime) -> list[dict]:
    """Strict 7-day filter using _resolve_date. Caps at 5."""
    kept = []
    for e in entries:
        if not isinstance(e, dict):
            continue
        pub = _resolve_date(e)
        url = e.get("url", "?")
        if pub is None:
            logger.info(f"Commentary review: dropped piece with no confirmable date: {url}")
            continue
        if pub < cutoff:
            logger.info(f"Commentary review: dropped stale piece dated {pub.strftime('%Y-%m-%d')}: {url}")
            continue
        logger.info(f"Commentary review: kept piece dated {pub.strftime('%Y-%m-%d')}: {url}")
        kept.append(e)
        if len(kept) >= 5:
            break
    return kept


def _build_html(entries: list[dict]) -> str:
    """Turn entries into <p> paragraphs with the anchor phrase hyperlinked."""
    paragraphs = []
    for e in entries:
        prose = (e.get("paragraph") or "").strip()
        anchor = (e.get("anchor") or "").strip()
        url = (e.get("url") or "").strip()
        if not prose or not url:
            continue
        if anchor and anchor in prose:
            prose = prose.replace(anchor, f'<a href="{url}">{anchor}</a>', 1)
        else:
            prose = f'{prose} <a href="{url}">[source]</a>'
        paragraphs.append(f"<p>{prose}</p>")
    return "".join(paragraphs)


# ─── Entry point ──────────────────────────────────────────────────────

def generate_commentary_review(api_key: str, end_date,
                               model: str = "claude-sonnet-4-5") -> str:
    """
    Generate the "Expert Commentary This Week" section.

    end_date: a datetime (today, Canberra time). Only commentary
    published within the 7 days ending on end_date survives the
    Python date filter.

    Returns HTML <p> paragraphs on success, or "" on failure or no
    qualifying pieces. The emailer wraps the result with the <h2>
    section heading; this module returns paragraphs only.
    """
    today = end_date.replace(tzinfo=None) if getattr(end_date, "tzinfo", None) else end_date
    cutoff = (today - timedelta(days=7)).replace(hour=0, minute=0, second=0, microsecond=0)
    soft_cutoff = (today - timedelta(days=14)).replace(hour=0, minute=0, second=0, microsecond=0)
    logger.info(
        f"Commentary review: strict cutoff {cutoff.strftime('%Y-%m-%d')}, "
        f"soft cutoff {soft_cutoff.strftime('%Y-%m-%d')}"
    )

    try:
        client = anthropic.Anthropic(api_key=api_key)

        # ── Discovery ────────────────────────────────────────────────
        rss_candidates = _discover_rss()
        csis_candidates = _discover_csis()
        eaf_candidates = _discover_eaf(client, model, today.year)
        all_candidates = rss_candidates + csis_candidates + eaf_candidates
        logger.info(
            f"Commentary review: {len(all_candidates)} total candidates "
            f"(RSS {len(rss_candidates)}, CSIS {len(csis_candidates)}, "
            f"EAF {len(eaf_candidates)})"
        )
        if not all_candidates:
            logger.info("Commentary review: no candidates, omitting section")
            return ""

        # ── Soft pre-filter ──────────────────────────────────────────
        # Drop candidates whose date is known and >14 days old. Keep
        # candidates with no known date (CSIS index entries) for the
        # shortlist step; their dates resolve via metadata fetch later.
        pre = []
        for c in all_candidates:
            d = _parse_iso_date(c.get("date") or "")
            if d is None:
                d = _date_from_url(c.get("url") or "")
            if d is not None and d < soft_cutoff:
                continue
            pre.append(c)
        logger.info(f"Commentary review: {len(pre)} after soft pre-filter")
        if not pre:
            return ""

        # ── Shortlist ────────────────────────────────────────────────
        shortlisted = _shortlist(client, pre, model)
        if not shortlisted:
            logger.info("Commentary review: empty shortlist, omitting")
            return ""

        # ── Fetch article bodies for shortlist ───────────────────────
        for item in shortlisted:
            item["text"] = _fetch_article_text(item.get("url", ""))
            if not item["text"]:
                logger.info(
                    f"Commentary review: body fetch failed/blocked, "
                    f"falling back to teaser for {item.get('url', '?')}"
                )

        # ── Paragraph writing ────────────────────────────────────────
        entries = _write_paragraphs(client, shortlisted, model)
        if not entries:
            logger.info("Commentary review: paragraph writing returned nothing")
            return ""
        logger.info(f"Commentary review: {len(entries)} paragraphs written, verifying dates...")

        # ── Strict date filter ───────────────────────────────────────
        kept = _filter_by_date(entries, cutoff)
        if not kept:
            logger.info("Commentary review: no recent qualifying pieces after strict date check")
            return ""

        # ── HTML build ───────────────────────────────────────────────
        html = _build_html(kept)
        if not html:
            logger.warning("Commentary review: entries produced no usable HTML")
            return ""
        logger.info(
            f"Commentary review: {len(kept)} pieces in final output ({len(html)} chars)"
        )
        return html

    except Exception as e:
        logger.error(f"Commentary review generation failed (non-fatal): {e}")
        return ""
