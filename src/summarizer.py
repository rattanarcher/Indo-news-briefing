"""
Summarizer module.
Sends collected headlines to Claude API and returns an English summary.
"""

import anthropic
import logging

logger = logging.getLogger(__name__)

SUMMARY_PROMPT = """You are an expert news analyst covering Indonesia. You have been given today's headlines from major Indonesian news outlets, each with its URL.

Your task:
1. Write a concise executive summary (3-5 paragraphs) of the most important news from Indonesia today, in English.
2. Group related stories and identify the top themes.
3. Note any stories that appear across multiple outlets (indicating high significance).
4. Keep it factual and neutral. If headlines are in Bahasa Indonesia, translate the key points to English.
5. IMPORTANT: When you mention a story, embed an HTML hyperlink to the relevant article using <a href="URL">descriptive text</a> format. For example: <a href="https://example.com/article">Indonesia's GDP grew by 5.2%</a>. Every key claim should link to its source article.

Headlines:
{headlines}

Write the summary now in HTML-ready format with embedded <a> hyperlinks. Do not include any preamble like "Here is the summary" — just start with the content."""


def summarize_headlines(headlines_text: str, api_key: str, model: str = "claude-sonnet-4-20250514") -> str:
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
            max_tokens=1500,
            messages=[
                {
                    "role": "user",
                    "content": SUMMARY_PROMPT.format(headlines=headlines_text)
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
