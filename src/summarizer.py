"""
Summarizer module.
Sends collected headlines to Claude API and returns an English summary.
"""

import anthropic
import logging

logger = logging.getLogger(__name__)

SUMMARY_PROMPT = """You are an expert news analyst covering Indonesia. You have been given today's headlines from major Indonesian news outlets, each with its URL.

Today's date is {today_date}.

Write a 5-paragraph English-language executive summary structured as follows:

Paragraphs 1-2: Political news. Cover the most important domestic political stories — anything involving the president, cabinet, DPR/MPR, political parties, coalitions, elections, governance, state institutions, and political controversies. Describe what happened factually. Do not analyse or speculate on implications.

Paragraph 3: Foreign policy and defence news. Cover stories involving Indonesia's foreign relations, diplomacy, ASEAN, bilateral meetings, military operations, defence procurement, TNI leadership, and security matters. Describe what happened factually.

Paragraphs 4-5: Everything else. Cover the most important remaining stories across economy, energy, legal, social affairs, environment, health, and other topics.

Rules:
- Write in English. If headlines are in Bahasa Indonesia, translate the key points.
- Be factual and descriptive. Report what happened, not what it means.
- When you mention a story, embed an HTML hyperlink to the relevant article using <a href="URL">descriptive text</a> format. Every key claim should link to its source article.
- Do NOT include any title, heading, section header, or date. Do NOT label paragraphs. Just write five flowing paragraphs.
- If there are no foreign policy or defence stories on a given day, fold that paragraph into the political section and write four paragraphs total.
- Start directly with the first paragraph.

Headlines:
{headlines}

Write the summary now in HTML-ready format with embedded <a> hyperlinks."""


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
