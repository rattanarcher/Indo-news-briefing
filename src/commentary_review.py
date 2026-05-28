"""
Commentary review module (Monday only).

Surveys expert commentary on Indonesia from five major outlets over the
past week, asks Claude to select up to 5 most consequential pieces and
write one paragraph each for the Monday briefing.

Discovery is done entirely through Claude's server-side web_search and
web_fetch tools rather than raw HTTP scraping. This is deliberate: the
commentary sites use bot protection that blocks plain requests calls but
that Anthropic's fetch infrastructure can reach, and it avoids fragile
site-specific HTML parsing that breaks on redesigns.

Sources:
    - East Asia Forum (eastasiaforum.org)      [via web_search]
    - The Diplomat (thediplomat.com)           [via web_search]
    - Fulcrum (fulcrum.sg)                      [via web_fetch of tag page]
    - CSIS Indonesia (csis.or.id)               [via web_fetch of commentaries]
    - Indonesia at Melbourne (unimelb.edu.au)   [via web_fetch of homepage]

If anything fails, returns an empty string and the caller silently skips
the section. Same graceful failure pattern as weekly_review.
"""

import logging

import anthropic

logger = logging.getLogger(__name__)

COMMENTARY_PROMPT = """You are an expert news analyst covering Indonesia. Your task is to survey expert commentary on Indonesia published in the last 7 days and write a short "Expert Commentary This Week" section for a Monday briefing.

You have web_search and web_fetch tools. Use them to discover and read candidate pieces from these five outlets:

Index pages to fetch directly (use web_fetch on each to see recent articles):
- Fulcrum: https://fulcrum.sg/tag/indonesia/
- CSIS Indonesia: https://www.csis.or.id/publications/commentaries/
- Indonesia at Melbourne: https://indonesiaatmelbourne.unimelb.edu.au/

Outlets to discover via web_search (these block direct fetching of index pages):
- East Asia Forum: search "site:eastasiaforum.org Indonesia"
- The Diplomat: search "site:thediplomat.com Indonesia"

Your goal: identify up to 5 of the most consequential pieces, read each one, then write a one-paragraph summary of each.

If fewer than 5 qualifying pieces exist this week, write fewer paragraphs. Do not reach for marginal pieces to fill the section.

Indonesia-link requirement: only consider pieces with a direct, substantive Indonesia focus. A piece that mentions Indonesia briefly while focused on ASEAN, China, or US policy does not qualify.

Language requirement: only consider pieces written in English. CSIS Indonesia publishes in both English and Bahasa Indonesia - exclude the Bahasa pieces.

Selection criteria, in this order of priority:
1. Substantive policy or political significance. Pieces that engage seriously with major developments in one or more of these areas: domestic politics, government policy, defence policy, foreign policy, institutional changes, economic decisions. Light reviews, commemorative essays, podcast episodes, and general overviews do not qualify even when well written.
2. Analytical depth over recap. Pieces that offer original argument, framing, or evaluation are preferred over those that mainly describe events the reader will already have seen in the daily news.
3. Authoritative authorship. Pieces by established Indonesianists, named scholars, or senior policy figures are preferred where the substance is otherwise comparable.
4. Outlet diversity. Where two candidate pieces are otherwise comparable, prefer the one from an outlet not already represented in your selection. Do not sacrifice substantive significance to diversify.

Process:
1. Fetch the three index pages and run the two searches to build a pool of candidate pieces published in the last 7 days.
2. From the pool, shortlist 7-8 that look most promising based on titles, outlets, authors, and dates. Do not read all of them in full.
3. Use web_fetch to read the full text of only the shortlisted pieces.
4. Discard any that turn out to be republished older content (more than 7 days old), paywalled excerpts, podcast episode notes (especially Indonesia at Melbourne's "Talking Indonesia" series), Bahasa-language pieces, or fail the Indonesia-link requirement on closer inspection.
5. From the remaining set, select up to 5 most consequential.
6. Write one paragraph per selected piece.

Format for each paragraph:
- Write 3-4 sentences in flowing prose. Weave the attribution naturally into the writing (for example: "Writing in East Asia Forum, [author] argues that...").
- Hyperlink a short anchor (3-7 words) within the prose to the piece's URL. Anchor the link to a phrase that conveys what the piece is about, not to the author or outlet name.
- Report what the piece argues and why it matters. Do not insert your own view.
- Do not quote more than 10 words verbatim from any piece. Paraphrase.

Output: Up to 5 <p> paragraphs, in order of significance (most consequential first). No heading, no preamble, no narration of your process. Begin your output with the first <p> tag.

If after searching you find no qualifying pieces at all, output exactly the text NONE and nothing else.

Begin."""


def generate_commentary_review(api_key: str,
                               model: str = "claude-sonnet-4-5") -> str:
    """
    Generate the "Expert Commentary This Week" section.

    Returns the HTML string (up to five <p> paragraphs) on success, or an
    empty string on any failure or if no qualifying pieces were found
    (caller silently omits the section).
    """
    try:
        client = anthropic.Anthropic(api_key=api_key)

        tools = [
            {"type": "web_search_20250305", "name": "web_search", "max_uses": 6},
            {"type": "web_fetch_20250910", "name": "web_fetch", "max_uses": 12},
        ]

        messages = [{"role": "user", "content": COMMENTARY_PROMPT}]

        # Loop while the API pauses for long-running server-side tools.
        # 20-iteration cap as agreed (discovery + shortlist fetches).
        for _ in range(20):
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

            text = "".join(
                block.text for block in response.content
                if getattr(block, "type", None) == "text"
            )
            text = text.strip()

            if not text:
                logger.warning("Commentary review: empty final text, omitting section")
                return ""

            # Explicit no-content signal from the model
            if text.upper().startswith("NONE"):
                logger.info("Commentary review: no qualifying pieces this week, omitting section")
                return ""

            # Strip any preamble before the first <p>
            p_start = text.find("<p>")
            if p_start == -1:
                logger.warning("Commentary review: no <p> tags in output, omitting section")
                return ""
            if p_start > 0:
                logger.info(f"Commentary review: stripped {p_start} chars of preamble")
                text = text[p_start:]
            p_end = text.rfind("</p>")
            if p_end != -1:
                text = text[:p_end + 4]

            logger.info(f"Commentary review generated ({len(text)} chars)")
            return text.strip()

        logger.warning("Commentary review: tool loop did not converge in 20 iterations")
        return ""

    except Exception as e:
        logger.error(f"Commentary review generation failed (non-fatal): {e}")
        return ""
