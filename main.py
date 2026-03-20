"""
Indonesia Daily News Briefing - Main Pipeline

Orchestrates: scrape → summarize → email
Run manually:  python main.py
Automated via: GitHub Actions cron (see .github/workflows/daily_news.yml)
"""

import os
import sys
import logging
from datetime import datetime

from src.scraper import fetch_all_headlines, headlines_to_text
from src.summarizer import summarize_headlines
from src.emailer import build_email_html, send_email

# ─── Configuration (all from environment variables) ─────────────────

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
EMAIL_FROM = os.environ.get("EMAIL_FROM", SMTP_USER)
EMAIL_TO = os.environ.get("EMAIL_TO", SMTP_USER)
USE_TLS = os.environ.get("SMTP_USE_TLS", "true").lower() == "true"

# Optional: override Claude model
CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-20250514")


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    logger = logging.getLogger("main")

    # ── Validate config ──────────────────────────────────────────
    missing = []
    if not ANTHROPIC_API_KEY:
        missing.append("ANTHROPIC_API_KEY")
    if not SMTP_USER:
        missing.append("SMTP_USER")
    if not SMTP_PASSWORD:
        missing.append("SMTP_PASSWORD")

    if missing:
        logger.error(f"Missing required environment variables: {', '.join(missing)}")
        logger.error("See .env.example for the full list.")
        sys.exit(1)

    # ── Step 1: Scrape ───────────────────────────────────────────
    logger.info("Step 1/3: Scraping headlines...")
    all_headlines = fetch_all_headlines()

    total = sum(len(v) for v in all_headlines.values())
    if total == 0:
        logger.warning("No headlines fetched from any source. Sending error notice.")

    headlines_text = headlines_to_text(all_headlines)

    # ── Step 2: Summarize ────────────────────────────────────────
    logger.info("Step 2/3: Generating summary via Claude API...")
    summary = summarize_headlines(headlines_text, ANTHROPIC_API_KEY, model=CLAUDE_MODEL)

    # ── Step 3: Email ────────────────────────────────────────────
    logger.info("Step 3/3: Sending email...")
    today = datetime.now().strftime("%A, %d %B %Y")
    subject = f"Indonesia News Briefing — {today}"

    html_body = build_email_html(summary, all_headlines, today)

    success = send_email(
        smtp_host=SMTP_HOST,
        smtp_port=SMTP_PORT,
        smtp_user=SMTP_USER,
        smtp_password=SMTP_PASSWORD,
        from_email=EMAIL_FROM,
        to_email=EMAIL_TO,
        subject=subject,
        html_body=html_body,
        use_tls=USE_TLS,
    )

    if success:
        logger.info("Daily briefing sent successfully!")
    else:
        logger.error("Failed to send daily briefing.")
        sys.exit(1)


if __name__ == "__main__":
    main()
