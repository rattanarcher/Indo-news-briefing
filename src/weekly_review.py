"""
Weekly review module.

Generates the "What Happened Last Week" section that appears at the top of
the Monday briefing. Reads the headlines archive for the past 7 days, asks
Claude to identify the 3 main threads, fetch the archive articles, run web
searches for the latest developments, and write a three-paragraph retrospective.

This module is only invoked on Mondays. If anything fails, the caller should
skip the weekly section and continue with the normal daily briefing.
"""

import logging
import os
from datetime import datetime, timezone, timedelta

import anthropic
import pandas as pd

logger = logging.getLogger(__name__)


def _view_with_rolling_cache(messages):
    """
    Return a shallow copy of `messages` where the LAST message's content
    blocks are converted to plain dicts and a cache_control breakpoint is
    placed on the final block. This caches the accumulated conversation
    (tools, prompt, and all fetched tool results up to this point) so the
    next pause_turn iteration reads it back at ~10% of the input price
    instead of reprocessing every fetched article at full price.

    Only the last message is dict-converted, so the known-good block
    objects in `messages` itself stay untouched and remain available as a
    fallback if a converted payload is ever rejected. messages[0] keeps
    its own static-prefix breakpoint, so the view carries two breakpoints
    (static prefix + rolling), well within the 4-breakpoint limit.
    """
    view = list(messages)
    last = view[-1]
    content = last.get("content")
    if isinstance(content, list) and content:
        new_content = [
            b if isinstance(b, dict) else b.model_dump(mode="json")
            for b in content
        ]
        new_content[-1] = {**new_content[-1], "cache_control": {"type": "ephemeral"}}
        view[-1] = {"role": last["role"], "content": new_content}
    return view

ARCHIVE_FILE = "headlines_archive.xlsx"

# Topics included in the weekly review (researcher's focus areas).
# Only these three are scanned - other topics are excluded to keep the
# prompt small and within the API rate limit.
PRIORITY_TOPICS = ["Politics", "Foreign Affairs", "Defence/Security"]

# Maximum headlines kept per topic (most recent first). Capping the
# prompt size keeps the weekly request well under the API rate limit.
MAX_PER_TOPIC = 60


WEEKLY_PROMPT = """You are an expert news analyst covering Indonesia. Below are the headlines collected over the past week ({date_range}), grouped by topic, each with publication day and URL.

Your task: write a THREE-paragraph retrospective titled "What Happened Last Week" for the top of a Monday briefing.

Step 1 - From the headlines, identify the 3 most important threads of the week. Prefer threads that recurred across multiple days. If you cannot find 3 multi-day threads, select the 3 most consequential individual stories instead.

Step 2 - For each thread, fetch and read the most relevant archive article URLs listed below to understand the specific context of what was reported.

Step 3 - Then run a general web search on each thread to check for the latest developments, including anything that happened after the archived articles.

Step 4 - Write three paragraphs (about 4-6 sentences each), ONE paragraph per thread, in order of importance. Be factual and descriptive; report what happened and how each thread developed over the week. Do not speculate on future implications. If you could only identify two substantive threads, write two paragraphs rather than padding with a weak third.

CRITICAL OUTPUT RULE: Your final output must contain ONLY the paragraphs, each wrapped in a <p> tag (normally three). Do NOT include any narration of your process, any preamble such as "I'll analyze..." or "Based on my research...", any step descriptions, or any heading. The very first characters of your final answer must be "<p>". Anything you need to say about your process belongs in tool calls, never in the final text.

HYPERLINK RULES (these matter as much as the prose itself):
- Source your claims with inline hyperlinks: <a href="URL">anchor text</a>, anchors 3-7 words. These links are the core value of this section. A paragraph with no hyperlink is a failure, and most paragraphs should carry several.
- Hyperlink the key claim, name, statistic, or development to the article that reported it. This applies whether you quote OR paraphrase. You will mostly be paraphrasing (see LANGUAGE below), and paraphrasing must NEVER strip the link: wrap the paraphrased claim or its key noun phrase in the <a href> tag pointing to the source.
- Concretely, write: Golkar's <a href="URL">Sarmuji defended the appointment</a> as an effort to break patronage networks. NOT: Golkar's Sarmuji defended the appointment as an effort to break patronage networks (with no link).
- Every distinct story, named figure, statistic, and specific announcement must be linked to its source.
- Prefer the archive article URLs provided below; reputable URLs found via web search are also fine where the archive lacks a good source. If a thread came mainly from web search, link the most authoritative source you found.

LANGUAGE: The entire output must be in English. If a source spoke in Bahasa Indonesia, paraphrase the substance in English or translate the relevant phrase, and still hyperlink that paraphrased claim to its source per the rules above. Do NOT include verbatim Bahasa Indonesia sentences or quoted phrases in the paragraphs. Indonesian proper nouns and established terms of art (e.g. bebas aktif, Kartu Prakerja, hilirisasi, Pancasila) may remain in Indonesian where appropriate; the rule targets quoted speech and quoted document text, not these established terms.

Headlines for the week:
{headlines}

Produce the final output now. Start immediately with "<p>" - no preamble, no narration. Remember: every paragraph needs several inline source hyperlinks, and paraphrasing in English must not drop them."""


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
    """
    Format the week's headlines for the prompt.

    Only the three priority topics are included. Each topic is capped at
    MAX_PER_TOPIC most-recent headlines to keep the prompt within the API
    rate limit. Topics with fewer headlines simply include all of them.
    """
    lines = []
    total_kept = 0
    for topic in PRIORITY_TOPICS:
        sub = week[week["Topic"] == topic].sort_values("Date_parsed")
        if len(sub) == 0:
            continue

        # Keep the most recent MAX_PER_TOPIC headlines for this topic
        full_count = len(sub)
        if full_count > MAX_PER_TOPIC:
            sub = sub.tail(MAX_PER_TOPIC)

        kept = len(sub)
        total_kept += kept
        note = f" (showing {kept} most recent of {full_count})" if full_count > kept else f" ({kept} headlines)"
        lines.append(f"\n=== {topic}{note} ===")

        for _, r in sub.iterrows():
            day = r["Date_parsed"].strftime("%a %d")
            headline = str(r["Headline"]).replace("\n", " ").strip()
            lines.append(f"[{day}] {headline} | {r['Link']}")

    logger.info(f"Weekly review: {total_kept} headlines included after per-topic cap")
    return "\n".join(lines)


def generate_weekly_review(api_key: str, end_date: datetime,
                           model: str = "claude-sonnet-4-5") -> str:
    """
    Generate the three-paragraph weekly review.

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
            # max_content_tokens caps how much of each fetched page enters
            # the context. Server-side web_fetch otherwise injects the full
            # page (article + nav + footer + related links), which then gets
            # re-read on every pause_turn iteration. 2000 tokens comfortably
            # holds a full 600-1000 word article, including the less
            # token-efficient Bahasa Indonesia sources and the page padding
            # that survives extraction, while trimming long features. Raise
            # to 2500 for zero clipping risk on the longest pieces.
            {"type": "web_fetch_20250910", "name": "web_fetch", "max_uses": 10,
             "max_content_tokens": 2000},
        ]

        # The initial prompt (instructions + a week of headlines) plus the
        # tool definitions are re-sent on every pause_turn iteration of the
        # loop below. Marking a cache_control breakpoint here caches that
        # static prefix (tools + this message) so each subsequent iteration
        # reads it back at ~10% of the input price instead of full price.
        # The 5-minute cache TTL easily covers a single run's iterations.
        messages = [{
            "role": "user",
            "content": [{
                "type": "text",
                "text": WEEKLY_PROMPT.format(headlines=headlines_block, date_range=date_range),
                "cache_control": {"type": "ephemeral"},
            }],
        }]

        # Whether to cache the accumulated fetched content across loop
        # iterations (Tier 2). Off by default: it was ruled out as the
        # cause of a hyperlink regression and its saving is marginal, so
        # it is not worth the risk on production runs. Set
        # CACHE_FETCH_RESULTS=true to re-enable for experiments. The
        # static prefix cache and the fetch truncation are independent of
        # this flag and always apply.
        cache_fetch = os.environ.get("CACHE_FETCH_RESULTS", "false").lower() == "true"

        def _call(msgs):
            return client.messages.create(
                model=model,
                max_tokens=6000,
                tools=tools,
                messages=msgs,
                # web_fetch is a beta feature and needs this header.
                # web_search is generally available and needs nothing.
                extra_headers={"anthropic-beta": "web-fetch-2025-09-10"},
            )

        # Loop while the API pauses for long-running server-side tools.
        # A generous cap prevents an infinite loop if something goes wrong.
        for _ in range(15):
            # Build the request. When fetch-caching is on and we already
            # have appended turns, send a cache-annotated view; otherwise
            # send the canonical messages (which still carry the static
            # prefix cache).
            use_cache_view = cache_fetch and len(messages) > 1
            request_messages = _view_with_rolling_cache(messages) if use_cache_view else messages

            try:
                response = _call(request_messages)
            except Exception as e:
                # If the cache-annotated payload was rejected, fall back to
                # the known-good canonical messages and stop trying to cache
                # fetched content for the rest of this run. The run still
                # completes, just without the Tier 2 saving.
                if use_cache_view:
                    logger.warning(
                        f"Weekly review: call failed with fetch-caching active "
                        f"({e}); disabling fetch-caching and retrying"
                    )
                    cache_fetch = False
                    response = _call(messages)
                else:
                    raise

            # Log cache effectiveness so test runs show whether it engaged.
            usage = getattr(response, "usage", None)
            if usage is not None:
                created = getattr(usage, "cache_creation_input_tokens", 0) or 0
                read = getattr(usage, "cache_read_input_tokens", 0) or 0
                if created or read:
                    logger.info(f"Weekly review cache: {read} read, {created} written (input tokens)")

            if response.stop_reason == "pause_turn":
                # Append the partial assistant turn as block objects (the
                # canonical, known-good form). The cache breakpoint is added
                # only in the per-call view, never in messages itself.
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
