"""
Commentary review module (Monday only).

Surveys expert commentary on Indonesia from five major outlets over the
past week, selects up to 5 most consequential pieces, and writes one
paragraph each for the Monday briefing.

Discovery and reading are done through Claude's server-side web_search and
web_fetch tools. Claude returns structured JSON (one entry per candidate
piece, including the publication date it read off each page). The DATE
FILTERING is then done deterministically in Python, not left to Claude's
judgement — prompt-based date enforcement proved unreliable (Claude would
include stale pieces to fill the section even when told not to). Claude is
good at reading a date off a page; Python is reliable at comparing it to a
cutoff. So we split the work accordingly.

Sources:
    - East Asia Forum (eastasiaforum.org)
    - The Diplomat (thediplomat.com)
    - Fulcrum (fulcrum.sg)
    - CSIS Indonesia (csis.or.id)
    - Indonesia at Melbourne (indonesiaatmelbourne.unimelb.edu.au)

If anything fails, returns an empty string and the caller silently skips
the section. Same graceful failure pattern as weekly_review.
"""

import json
import logging
from datetime import datetime, timedelta

import anthropic

logger = logging.getLogger(__name__)

COMMENTARY_PROMPT = """You are an expert news analyst covering Indonesia. Your task is to survey recent expert commentary on Indonesia and return a structured list of candidate pieces for a Monday briefing's "Expert Commentary This Week" section.

You have web_search and web_fetch tools. Use BOTH discovery methods below to build the widest possible candidate pool. Fetching an index page can miss recent articles that have scrolled down the page, so always run the searches as well.

Index pages to fetch directly (use web_fetch on each):
- Fulcrum: https://fulcrum.sg/tag/indonesia/
- CSIS Indonesia: https://www.csis.or.id/publications/commentaries/
- Indonesia at Melbourne: https://indonesiaatmelbourne.unimelb.edu.au/

Searches to run (use web_search for each):
- "site:eastasiaforum.org Indonesia"
- "site:thediplomat.com Indonesia"
- "site:fulcrum.sg Indonesia"
- "site:csis.or.id Indonesia commentary"
- "site:indonesiaatmelbourne.unimelb.edu.au Indonesia"

Combine everything found into one candidate pool. Deduplicate by URL.

Requirements for a candidate piece:
- Indonesia-link: a direct, substantive Indonesia focus. A piece merely mentioning Indonesia while focused on ASEAN, China, or US policy does NOT qualify.
- English language only. CSIS Indonesia publishes in both English and Bahasa Indonesia — exclude the Bahasa pieces.
- Not a podcast episode note (especially Indonesia at Melbourne's "Talking Indonesia" series), not a paywalled excerpt, not republished older content.
- Substantive policy or political significance: engages seriously with major developments in domestic politics, government policy, defence policy, foreign policy, institutional changes, or economic decisions. Light reviews, commemorative essays, and general overviews do not qualify.

Process:
1. Run the fetches and searches to build the candidate pool.
2. Shortlist 7-8 promising pieces from titles/outlets/authors.
3. web_fetch the full text of the shortlisted pieces.
4. For EACH shortlisted piece, find its publication date on the page (look for "Published", a dateline, or page metadata). You MUST record this date.
5. Keep the pieces that meet all the requirements above. Do not apply any date cutoff yourself — include every qualifying piece you find regardless of age, and report its true date. Date filtering happens downstream.
6. Order the kept pieces by consequence, most significant first.

Output format — CRITICAL:
Return ONLY a JSON array, nothing else. No preamble, no markdown fences, no narration. Each element is an object with exactly these fields:
- "date": the publication date in strict ISO format "YYYY-MM-DD". If you genuinely cannot determine the date, use "unknown".
- "url": the canonical article URL.
- "outlet": the outlet name (e.g. "East Asia Forum", "Fulcrum", "CSIS Indonesia", "Indonesia at Melbourne", "The Diplomat").
- "anchor": a 3-7 word phrase, drawn from what the piece argues, to be used as the hyperlink text.
- "paragraph": a 3-4 sentence prose summary of the piece. Weave the attribution in naturally (e.g. "Writing in East Asia Forum, Edward Aspinall argues..."). Include the exact "anchor" phrase somewhere in this paragraph so it can be hyperlinked. Do NOT put any HTML in this field — plain prose only. Report what the piece argues and why it matters; do not insert your own view. Do not quote more than 10 words verbatim.

Example of a single element:
{{"date": "2026-05-22", "url": "https://eastasiaforum.org/...", "outlet": "East Asia Forum", "anchor": "Indonesia's defense partnership with the US", "paragraph": "Writing in East Asia Forum, ... argues that Indonesia's defense partnership with the US has triggered public anxiety ..."}}

If you find no qualifying pieces at all, return an empty JSON array: []

Return the JSON array now."""


def _build_html(entries: list[dict]) -> str:
    """Turn filtered entries into HTML <p> paragraphs with hyperlinks."""
    paragraphs = []
    for e in entries:
        prose = e.get("paragraph", "").strip()
        anchor = e.get("anchor", "").strip()
        url = e.get("url", "").strip()
        if not prose or not url:
            continue
        # Hyperlink the anchor phrase within the prose, if present
        if anchor and anchor in prose:
            linked = f'<a href="{url}">{anchor}</a>'
            prose = prose.replace(anchor, linked, 1)
        else:
            # Anchor phrase not found verbatim — append a small source link
            prose = f'{prose} <a href="{url}">[source]</a>'
        paragraphs.append(f"<p>{prose}</p>")
    return "".join(paragraphs)


def _parse_and_filter(raw_text: str, cutoff: datetime) -> list[dict]:
    """
    Parse Claude's JSON output and keep only entries dated on/after cutoff.
    Entries with an unparseable or "unknown" date are excluded (we never
    surface a piece we cannot confirm is recent). Caps at 5.
    """
    text = raw_text.strip()
    # Strip markdown fences if present
    if text.startswith("```"):
        text = text.split("```", 2)[1] if "```" in text[3:] else text
        text = text.replace("json", "", 1).strip("`").strip()
    # Extract the JSON array
    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end == -1 or end < start:
        logger.warning("Commentary review: no JSON array found in output")
        return []
    try:
        entries = json.loads(text[start:end + 1])
    except Exception as e:
        logger.warning(f"Commentary review: JSON parse failed: {e}")
        return []
    if not isinstance(entries, list):
        return []

    kept = []
    for e in entries:
        if not isinstance(e, dict):
            continue
        date_str = str(e.get("date", "")).strip()
        if not date_str or date_str.lower() == "unknown":
            logger.info(f"Commentary review: dropped piece with no confirmed date: {e.get('url','?')}")
            continue
        try:
            pub = datetime.strptime(date_str, "%Y-%m-%d")
        except ValueError:
            logger.info(f"Commentary review: dropped piece with unparseable date '{date_str}': {e.get('url','?')}")
            continue
        if pub < cutoff:
            logger.info(f"Commentary review: dropped stale piece dated {date_str}: {e.get('url','?')}")
            continue
        kept.append(e)

    return kept[:5]


def generate_commentary_review(api_key: str, end_date,
                               model: str = "claude-sonnet-4-5") -> str:
    """
    Generate the "Expert Commentary This Week" section.

    end_date: a datetime (today, Canberra time). Only commentary published
    within the 7 days ending on end_date survives the Python date filter.

    Returns HTML (<p> paragraphs) on success, or an empty string on any
    failure or if no qualifying pieces remain after filtering.
    """
    today = end_date.replace(tzinfo=None) if getattr(end_date, "tzinfo", None) else end_date
    cutoff = (today - timedelta(days=7)).replace(hour=0, minute=0, second=0, microsecond=0)
    logger.info(f"Commentary review: cutoff is {cutoff.strftime('%Y-%m-%d')} (pieces older than this are dropped)")

    try:
        client = anthropic.Anthropic(api_key=api_key)

        tools = [
            {"type": "web_search_20250305", "name": "web_search", "max_uses": 8},
            {"type": "web_fetch_20250910", "name": "web_fetch", "max_uses": 14},
        ]

        messages = [{"role": "user", "content": COMMENTARY_PROMPT}]

        # Loop while the API pauses for long-running server-side tools.
        for _ in range(24):
            response = client.messages.create(
                model=model,
                max_tokens=6000,
                tools=tools,
                messages=messages,
                extra_headers={"anthropic-beta": "web-fetch-2025-09-10"},
            )

            if response.stop_reason == "pause_turn":
                messages.append({"role": "assistant", "content": response.content})
                continue

            raw = "".join(
                block.text for block in response.content
                if getattr(block, "type", None) == "text"
            ).strip()

            if not raw:
                logger.warning("Commentary review: empty final text, omitting section")
                return ""

            entries = _parse_and_filter(raw, cutoff)
            if not entries:
                logger.info("Commentary review: no recent qualifying pieces, omitting section")
                return ""

            html = _build_html(entries)
            if not html:
                logger.warning("Commentary review: entries produced no usable HTML, omitting")
                return ""
            logger.info(f"Commentary review generated ({len(entries)} pieces, {len(html)} chars)")
            return html

        logger.warning("Commentary review: tool loop did not converge in 24 iterations")
        return ""

    except Exception as e:
        logger.error(f"Commentary review generation failed (non-fatal): {e}")
        return ""
