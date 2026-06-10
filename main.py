"""
Indonesia Daily News Briefing - Main Pipeline

Orchestrates: scrape → summarize → email
Run manually:  python main.py
Automated via: GitHub Actions cron (see .github/workflows/daily_news.yml)
"""

import os
import sys
import logging
from datetime import datetime, timezone, timedelta

from src.scraper import fetch_all_headlines, headlines_to_text
from src.summarizer import summarize_headlines
from src.emailer import build_email_html, send_email
from src.archive import archive_headlines, categorize_all
from src.briefing_archive import archive_briefing
from src.subscribers import build_recipient_list
from src.weekly_review import generate_weekly_review
from src.commentary_review import generate_commentary_review

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
# Briefing archive: set BRIEFING_ARCHIVE_ENABLED=false to skip saving the
# generated briefing to the briefings/ folder (used by the test workflow).
BRIEFING_ARCHIVE_ENABLED = os.environ.get("BRIEFING_ARCHIVE_ENABLED", "true").lower() == "true"

# Weekly review: set FORCE_WEEKLY=true to generate the Monday-format
# sections (weekly review + commentary review) on a non-Monday. Used by
# the test workflow so the Monday format can be exercised any day.
FORCE_WEEKLY = os.environ.get("FORCE_WEEKLY", "false").lower() == "true"

# Independent gates for the two Monday-format sections. Each is checked
# in addition to monday_format (which already requires Monday or
# FORCE_WEEKLY=true). The two flags are independent, so any of the four
# combinations works:
#   SKIP_WEEKLY=false SKIP_COMMENTARY=false  → both sections run
#   SKIP_WEEKLY=true  SKIP_COMMENTARY=false  → only commentary runs
#   SKIP_WEEKLY=false SKIP_COMMENTARY=true   → only weekly runs
#   SKIP_WEEKLY=true  SKIP_COMMENTARY=true   → neither runs (the old
#                                              cheap test mode)
SKIP_WEEKLY = os.environ.get("SKIP_WEEKLY", "false").lower() == "true"
SKIP_COMMENTARY = os.environ.get("SKIP_COMMENTARY", "false").lower() == "true"


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
    if monday_format and SKIP_WEEKLY:
        logger.info("SKIP_WEEKLY set — skipping the weekly review (cheap test run)")
    elif monday_format:
        if FORCE_WEEKLY and not is_monday:
            logger.info("FORCE_WEEKLY set — generating weekly review on a non-Monday (test mode)")
        else:
            logger.info("Today is Monday — generating 'What Happened Last Week' review...")
        try:
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

    # ── Step 2c: Commentary review (Mondays, or when FORCE_WEEKLY set) ────
    # Surveys expert commentary on Indonesia from five outlets over the
    # past week. Gated independently of the weekly review by
    # SKIP_COMMENTARY so each section can be tested in isolation.
    commentary_review = ""
    if monday_format and SKIP_COMMENTARY:
        logger.info("SKIP_COMMENTARY set — skipping the commentary review")
    elif monday_format:
        logger.info("Generating 'Expert Commentary This Week' review...")
        try:
            commentary_review = generate_commentary_review(
                ANTHROPIC_API_KEY, end_date=now_canberra, model=CLAUDE_MODEL
            )
            if commentary_review:
                logger.info("Commentary review generated and will be added to the briefing")
            else:
                logger.warning("Commentary review empty — 'Expert Commentary' section will be omitted")
        except Exception as e:
            logger.error(f"Commentary review failed (non-fatal): {e}")
            commentary_review = ""

    # ── Step 3: Categorise + Archive ─────────────────────────────
    # Categorise headlines once. The result is used both for the Excel
    # archive and to group the email appendix by topic.
    logger.info("Step 3/4: Categorising headlines...")
    try:
        categories = categorize_all(all_headlines, ANTHROPIC_API_KEY, model=CLAUDE_MODEL)
    except Exception as e:
        logger.error(f"Categorisation failed (non-fatal): {e}")
        categories = []

    if ARCHIVE_ENABLED:
        logger.info("Archiving headlines to Excel...")
        try:
            archive_headlines(all_headlines, ANTHROPIC_API_KEY, today_date=today,
                              model=CLAUDE_MODEL, categories=categories or None)
        except Exception as e:
            logger.error(f"Archiving failed (non-fatal): {e}")
    else:
        logger.info("Archiving SKIPPED (ARCHIVE_ENABLED=false, test mode)")

    # ── Step 4: Email ────────────────────────────────────────────
    logger.info("Step 4/4: Sending email...")
    if monday_format:
        subject = f"Monday Briefing — {today}"
    else:
        subject = f"Indonesia News Briefing — {today}"

    html_body = build_email_html(summary, all_headlines, today,
                                 weekly_review=weekly_review, is_monday=monday_format,
                                 categories=categories, commentary_review=commentary_review)

    # Archive the generated briefing before sending, so it is captured even
    # if the SMTP send fails. Best-effort: never blocks the email.
    if BRIEFING_ARCHIVE_ENABLED:
        archive_briefing(
            date_iso=now_canberra.strftime("%Y-%m-%d"),
            date_display=today,
            summary_html=summary,
            weekly_html=weekly_review,
            commentary_html=commentary_review,
            email_html=html_body,
            model=CLAUDE_MODEL,
            is_monday=monday_format,
        )
    else:
        logger.info("Briefing archive SKIPPED (BRIEFING_ARCHIVE_ENABLED=false, test mode)")

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
