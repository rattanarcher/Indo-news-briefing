"""
Weekly review module.

Generates the "What Happened Last Week" section that appears at the top of
the Monday briefing. Reads the headlines archive for the past 7 days, asks
Claude to identify the 3 main threads, fetch the archive articles, run web
searches for the latest developments, and write a two-paragraph retrospective.

This module is only invoked on Mondays. If anything fails, the caller should
skip the weekly section and continue with the normal daily briefing.
"""

import logging
from datetime import datetime, timezone, timedelta

import anthropic
import pandas as pd

logger = logging.getLogger(__name__)

ARCHIVE_FILE = "headlines_archive.xlsx"

# Topics surfaced first in the headline block (researcher's focus areas)
PRIORITY_TOPICS = ["Politics", "Foreign Affairs", "Defence/Security"]


WEEKLY_PROMPT = """You are an expert news analyst covering Indonesia. Below are the headlines collected over the past week ({date_range}), grouped by topic, each with publication day and URL.

Your task: write a TWO-paragraph retrospective titled "What Happened Last Week" for the top of a Monday briefing.

Step 1 - From the headlines, identify the 3 most important threads of the week. Prefer threads that recurred across multiple days. If you cannot find 3 multi-day threads, select the 3 most consequential individual stories instead.

Step 2 - For each thread, fetch and read the most relevant archive article URLs listed below to understand the specific context of what was reported.

Step 3 - Then run a general web search on each thread to check for the latest developments, including anything that happened after the archived articles.

Step 4 - Write two paragraphs (about 4-6 sentences each) covering the 3 threads. Be factual and descriptive; report what happened and how each thread developed over the week. Do not speculate on future implications.

CRITICAL OUTPUT RULE: Your final output must contain ONLY the two paragraphs, each wrapped in a <p> tag. Do NOT include any narration of your process, any preamble such as "I'll analyze..." or "Based on my research...", any step descriptions, or any heading. The very first characters of your final answer must be "<p>". Anything you need to say about your process belongs in tool calls, never in the final text.

HYPERLINK RULES:
- Embed hyperlinks using <a href="URL">anchor text</a>, with anchors of 3-7 words.
- Every distinct story or development mentioned must have a hyperlink.
- Every direct quote MUST be hyperlinked. If you quote a phrase such as "deep state" or "not to take too much initiative", the quoted phrase itself must be wrapped in an <a href> tag pointing to the article that reported it.
- Every specific, distinctive claim - a named figure, a statistic, a specific announcement - must be hyperlinked to its source.
- Prefer linking to the archive article URLs provided below; linking to other reputable URLs found via web search is also acceptable where it best supports the claim.

Headlines for the week:
{headlines}

Produce the final output now. Start immediately with "<p>" - no preamble, no narration."""


def _load_week(archive_path: str, end_date: datetime):
    """Load archive, return the 7-day slice ending on end_date and the start date."""
    df = pd.read_excel(archive_path)
    df["Date_parsed"] = pd.to_datetime(df["Date"], format="%A, %d %B %Y", errors="coerce")

    # The archive dates are timezone-naive. end_date arrives timezone-aware
    # (Canberra time). Strip the tzinfo so pandas can compare them - we only
    # care about the calendar date, not the time or zone.
    if end_date.tzinfo is not None:
        end_date = end_date.replace(tzinfo=None)
    # Normalise to midnight so the whole end day is included
    end_date = end_date.replace(hour=23, minute=59, second=59, microsecond=0)

    start = end_date - timedelta(days=6)
    start = start.replace(hour=0, minute=0, second=0, microsecond=0)
    mask = (df["Date_parsed"] >= start) & (df["Date_parsed"] <= end_date)
    return df[mask].copy(), start


def _build_headlines_block(week: pd.DataFrame) -> str:
    """Format the week's headlines grouped by topic, priority topics first."""
    other = [t for t in week["Topic"].unique() if t not in PRIORITY_TOPICS]
    lines = []
    for topic in PRIORITY_TOPICS + sorted(other):
        sub = week[week["Topic"] == topic].sort_values("Date_parsed")
        if len(sub) == 0:
            continue
        lines.append(f"\n=== {topic} ({len(sub)} headlines) ===")
        for _, r in sub.iterrows():
            day = r["Date_parsed"].strftime("%a %d")
            headline = str(r["Headline"]).replace("\n", " ").strip()
            lines.append(f"[{day}] {headline} | {r['Link']}")
    return "\n".join(lines)


def generate_weekly_review(api_key: str, end_date: datetime,
                           model: str = "claude-sonnet-4-5") -> str:
    """
    Generate the two-paragraph weekly review.

    Returns the HTML string (two <p> paragraphs) on success, or an empty
    string on any failure - the caller then skips the weekly section.
    """
    try:
        week, start = _load_week(ARCHIVE_FILE, end_date)
    except Exception as e:
        logger.error(f"Weekly review: could not load archive: {e}")
        return ""

    if len(week) == 0:
        logger.warning("Weekly review: no headlines for the past week, skipping")
        return ""

    date_range = f"{start.strftime('%d %b')} - {end_date.strftime('%d %b %Y')}"
    headlines_block = _build_headlines_block(week)
    logger.info(f"Weekly review: {date_range}, {len(week)} headlines")

    try:
        client = anthropic.Anthropic(api_key=api_key)

        # Enable both web search and web fetch tools.
        # These are SERVER-SIDE tools - Anthropic runs them on their own
        # infrastructure and returns the results directly. We do not execute
        # anything ourselves. When many tool calls are needed, the API may
        # return stop_reason="pause_turn", meaning "not finished, call again
        # with the conversation so far to continue".
        tools = [
            {"type": "web_search_20250305", "name": "web_search", "max_uses": 8},
            {"type": "web_fetch_20250910", "name": "web_fetch", "max_uses": 10},
        ]

        messages = [{
            "role": "user",
            "content": WEEKLY_PROMPT.format(headlines=headlines_block, date_range=date_range)
        }]

        # Loop while the API pauses for long-running server-side tools.
        # A generous cap prevents an infinite loop if something goes wrong.
        for _ in range(15):
            response = client.messages.create(
                model=model,
                max_tokens=6000,
                tools=tools,
                messages=messages,
                # web_fetch is a beta feature and needs this header.
                # web_search is generally available and needs nothing.
                extra_headers={"anthropic-beta": "web-fetch-2025-09-10"},
            )

            if response.stop_reason == "pause_turn":
                # Server is still working through tool calls. Append the
                # partial assistant turn and call again to continue.
                messages.append({"role": "assistant", "content": response.content})
                continue

            # Any other stop reason means generation finished.
            text = "".join(
                block.text for block in response.content
                if getattr(block, "type", None) == "text"
            )
            if not text.strip():
                logger.warning("Weekly review: empty text in final response, skipping")
                return ""

            # Safety net: strip any preamble/narration before the first
            # <p> tag. During tool use Claude sometimes emits process
            # narration as text; the real roundup starts at the first <p>.
            text = text.strip()
            p_start = text.find("<p>")
            if p_start > 0:
                logger.info(f"Weekly review: stripped {p_start} chars of preamble before <p>")
                text = text[p_start:]
            # Also trim anything after the last closing </p>
            p_end = text.rfind("</p>")
            if p_end != -1:
                text = text[:p_end + 4]

            logger.info(f"Weekly review generated ({len(text)} chars)")
            return text.strip()

        logger.warning("Weekly review: tool loop did not converge, skipping")
        return ""

    except Exception as e:
        logger.error(f"Weekly review generation failed: {e}")
        return ""
