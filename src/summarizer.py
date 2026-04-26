"""
Summarizer module.
Sends collected headlines to Claude API and returns an English summary.
"""

import anthropic
import logging

logger = logging.getLogger(__name__)

SUMMARY_PROMPT = """You are an expert news analyst covering Indonesia. You have been given today's headlines from major Indonesian news outlets, each with its URL.

Today's date is {today_date}.

Write an English-language executive summary structured into three sections, each with a section header in HTML <h3> tags. The structure for each section is: a short opening paragraph followed by supporting bullets.

SECTION 1: <h3>Domestic Politics</h3>
Scope: actions by the president, cabinet, DPR/MPR, party leadership, coalition dynamics, governance, electoral matters, political controversies. Do NOT include economic, financial, or business stories here even if they involve a politician — those belong in "Other". For example, a DPR member commenting on the rupiah exchange rate or GDP growth is an "Other" (economy) story, not a "Domestic Politics" story.
- Lead paragraph (3-4 sentences) on the day's single most significant political development.
- Up to 7 supporting bullets covering OTHER significant political stories. Each bullet must be a DIFFERENT story from the one covered in the lead paragraph — do not restate or rephrase what the lead paragraph already said. Each bullet is one sentence (which may run long if needed for context).

SECTION 2: <h3>Foreign Policy &amp; Defence</h3>
Scope: Indonesia's foreign relations, diplomacy, ASEAN, bilateral meetings, military operations, defence procurement, TNI leadership, security matters, and Indonesian responses to international events.
- Lead paragraph (3-4 sentences) on the day's single most significant foreign policy or defence story.
- Up to 5 supporting bullets covering OTHER significant foreign policy or defence stories. Each bullet must be a DIFFERENT story from the lead paragraph.
- If there are no foreign policy or defence stories, omit this section entirely.

SECTION 3: <h3>Other</h3>
Scope: economy, business, energy, legal/judicial, social affairs, environment, health, education, religion, technology, infrastructure. Includes economic comments by politicians.
- Lead paragraph (3-4 sentences) on the day's single most significant non-political story.
- Up to 5 supporting bullets covering OTHER significant stories from these areas. Each bullet must be a DIFFERENT story from the lead paragraph.

CRITICAL: The lead paragraph and bullets within each section must cover DIFFERENT stories. The lead paragraph covers the most important development. The bullets cover other significant stories not mentioned in the lead. Never repeat or rephrase a story across the lead paragraph and the bullets.

Significance filter: Only include stories that represent major developments. A story qualifies if it (a) involves named senior officials or major institutions, (b) appears across multiple outlets, or (c) represents a substantive policy or political development. Filter out routine ministerial visits, ceremonial activities, individual crime cases, and minor regional incidents unless they have national significance.

Rules:
- Write in English. If headlines are in Bahasa Indonesia, translate the key points.
- Be factual and descriptive. Report what happened, not what it means. Do not analyse implications.
- Embed HTML hyperlinks using <a href="URL">descriptive text</a> format.
- Lead paragraph hyperlink rule: each distinct factual claim in the lead paragraph that comes from a specific article MUST be hyperlinked to that article. If a lead paragraph mentions three separate stories (e.g. "X did A, while Y said B, and Z proposed C"), all three must be hyperlinked. Do not leave any sourced claim without a link in the lead paragraph.
- Bullet hyperlink rule: each bullet contains exactly one hyperlink anchored to its most important phrase.
- Hyperlinks should anchor to substantive phrases (e.g. "raised interest rates by 25 basis points") not vague ones (e.g. "thanking them").
- Use HTML <h3> tags for the three section headers exactly as written above.
- Use HTML <ul><li>...</li></ul> tags for the bullet lists.
- Lead paragraphs go in HTML <p> tags.
- Do not include any title, date, or preamble before the first <h3>.

Headlines:
{headlines}

Write the summary now in HTML-ready format."""


def summarize_headlines(headlines_text: str, api_key: str, today_date: str = "", model: str = "claude-sonnet-4-20250514") -> str:
    """
    Send headlines to Claude API and return an English summary.

    Args:
        headlines_text: Formatted string of all headlines
        api_key: Anthropic API key
        model: Claude model to use

    Returns:
        Summary text string
    """
    if not headlines_text.strip():
        return "No headlines were collected today. Sources may be temporarily unavailable."

    try:
        client = anthropic.Anthropic(api_key=api_key)

        message = client.messages.create(
            model=model,
            max_tokens=2500,
            messages=[
                {
                    "role": "user",
                    "content": SUMMARY_PROMPT.format(headlines=headlines_text, today_date=today_date)
                }
            ]
        )

        summary = message.content[0].text
        logger.info(f"Summary generated ({len(summary)} chars, model={model})")
        return summary

    except anthropic.AuthenticationError:
        logger.error("Invalid API key. Check your ANTHROPIC_API_KEY.")
        return "ERROR: Authentication failed. Please check your API key."

    except anthropic.RateLimitError:
        logger.error("Rate limit exceeded.")
        return "ERROR: API rate limit exceeded. Summary unavailable."

    except Exception as e:
        logger.error(f"Summarization failed: {e}")
        return f"ERROR: Could not generate summary. Reason: {e}"


if __name__ == "__main__":
    # Quick test
    import os
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    test_headlines = """
    === Detik.com ===
    1. Presiden Prabowo Bertemu Pemimpin ASEAN di KTT Jakarta
    2. Harga BBM Naik Mulai Besok

    === Antara News ===
    1. Indonesia GDP growth beats expectations at 5.2%
    2. Mount Merapi eruption alert raised to level 3
    """
    if key:
        print(summarize_headlines(test_headlines, key))
    else:
        print("Set ANTHROPIC_API_KEY to test.")
