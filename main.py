"""
Indonesia Daily News Briefing - Main Pipeline

Orchestrates: scrape → summarize → email
Run manually:  python main.py
Automated via: GitHub Actions cron (see .github/workflows/daily_news.yml)
"""

import os
import sys
import time
import logging
from datetime import datetime, timezone, timedelta

from src.scraper import fetch_all_headlines, headlines_to_text
from src.summarizer import summarize_headlines
from src.emailer import build_email_html, send_email
from src.archive import archive_headlines
from src.subscribers import build_recipient_list
from src.weekly_review import generate_weekly_review

# ─── Configuration (all from environment variables) ─────────────────

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
EMAIL_FROM = os.environ.get("EMAIL_FROM", SMTP_USER)
EMAIL_TO = os.environ.get("EMAIL_TO", SMTP_USER)
SUBSCRIBER_CSV_URL = os.environ.get("SUBSCRIBER_CSV_URL", "")
USE_TLS = os.environ.get("SMTP_USE_TLS", "true").lower() == "true"

# Optional: override Claude model
CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-5")

# Archiving: set ARCHIVE_ENABLED=false to skip the Excel archive step
# (used by the test workflow so test runs don't touch the archive)
ARCHIVE_ENABLED = os.environ.get("ARCHIVE_ENABLED", "true").lower() == "true"

# Weekly review: set FORCE_WEEKLY=true to generate the "What Happened
# Last Week" section even when today is not Monday (used by the test
# workflow so the Monday format can be tested any day)
FORCE_WEEKLY = os.environ.get("FORCE_WEEKLY", "false").lower() == "true"


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
    logger.info("Step 2/4: Generating summary via Claude API...")
    now_canberra = datetime.now(timezone(timedelta(hours=10)))
    today = now_canberra.strftime("%A, %d %B %Y")
    is_monday = now_canberra.weekday() == 0  # Monday == 0
    # The Monday format (header + weekly box) shows on real Mondays, or
    # any day when FORCE_WEEKLY is set (test workflow).
    monday_format = is_monday or FORCE_WEEKLY
    summary = summarize_headlines(headlines_text, ANTHROPIC_API_KEY, today_date=today, model=CLAUDE_MODEL)

    # ── Step 2b: Weekly review (Mondays, or when FORCE_WEEKLY set) ────
    weekly_review = ""
    if monday_format:
        if FORCE_WEEKLY and not is_monday:
            logger.info("FORCE_WEEKLY set — generating weekly review on a non-Monday (test mode)")
        else:
            logger.info("Today is Monday — generating 'What Happened Last Week' review...")
        try:
            # Pause before the weekly review so the API's per-minute token
            # window resets after the daily summary call. The weekly review
            # is a large request; running it immediately can trip the rate limit.
            logger.info("Pausing 60s before weekly review (rate limit headroom)...")
            time.sleep(60)

            # The review covers the 7 days ending yesterday
            week_end = now_canberra - timedelta(days=1)
            weekly_review = generate_weekly_review(
                ANTHROPIC_API_KEY, end_date=week_end, model=CLAUDE_MODEL
            )
            if weekly_review:
                logger.info("Weekly review generated and will be added to the briefing")
            else:
                logger.warning("Weekly review empty — 'What Happened Last Week' box will be omitted")
        except Exception as e:
            logger.error(f"Weekly review failed (non-fatal): {e}")
            weekly_review = ""

    # ── Step 3: Archive to Excel ─────────────────────────────────
    if ARCHIVE_ENABLED:
        logger.info("Step 3/4: Categorising and archiving headlines...")
        try:
            archive_headlines(all_headlines, ANTHROPIC_API_KEY, today_date=today, model=CLAUDE_MODEL)
        except Exception as e:
            logger.error(f"Archiving failed (non-fatal): {e}")
    else:
        logger.info("Step 3/4: Archiving SKIPPED (ARCHIVE_ENABLED=false, test mode)")

    # ── Step 4: Email ────────────────────────────────────────────
    logger.info("Step 4/4: Sending email...")
    if monday_format:
        subject = f"Monday Briefing — {today}"
    else:
        subject = f"Indonesia News Briefing — {today}"

    html_body = build_email_html(summary, all_headlines, today,
                                 weekly_review=weekly_review, is_monday=monday_format)

    # Combine core recipients with Google Sheet subscribers
    recipients = build_recipient_list(EMAIL_TO, SUBSCRIBER_CSV_URL)

    success = send_email(
        smtp_host=SMTP_HOST,
        smtp_port=SMTP_PORT,
        smtp_user=SMTP_USER,
        smtp_password=SMTP_PASSWORD,
        from_email=EMAIL_FROM,
        to_email=recipients,
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
