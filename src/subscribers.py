"""
Subscribers module.
Fetches the subscriber email list from a published Google Sheet CSV.
Combines with the core EMAIL_TO list and deduplicates.
"""

import csv
import io
import logging
import re
import requests

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT = 15

# Basic email validation pattern
EMAIL_PATTERN = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')


def fetch_subscriber_emails(csv_url: str) -> list[str]:
    """
    Fetch subscriber emails from a published Google Sheet CSV.

    The sheet is expected to have a column containing email addresses
    (typically named 'Email address' from a Google Form).

    Args:
        csv_url: The published CSV URL of the Google Sheet

    Returns:
        List of valid, unique email addresses. Empty list on failure.
    """
    if not csv_url:
        logger.info("No subscriber CSV URL configured, skipping subscriber list")
        return []

    try:
        resp = requests.get(csv_url, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()

        # Parse CSV content
        content = resp.content.decode("utf-8", errors="replace")
        reader = csv.reader(io.StringIO(content))
        rows = list(reader)

        if not rows:
            logger.warning("Subscriber CSV is empty")
            return []

        # Find the email column by header name
        header = [h.strip().lower() for h in rows[0]]
        email_col_idx = None
        for i, col_name in enumerate(header):
            if "email" in col_name:
                email_col_idx = i
                break

        # If no header match, scan all columns for the first that looks like emails
        emails = []
        if email_col_idx is not None:
            for row in rows[1:]:
                if len(row) > email_col_idx:
                    candidate = row[email_col_idx].strip()
                    if EMAIL_PATTERN.match(candidate):
                        emails.append(candidate.lower())
        else:
            logger.warning("No email column found in subscriber CSV, scanning all cells")
            for row in rows[1:]:
                for cell in row:
                    candidate = cell.strip()
                    if EMAIL_PATTERN.match(candidate):
                        emails.append(candidate.lower())
                        break

        # Deduplicate while preserving order
        seen = set()
        unique_emails = []
        for e in emails:
            if e not in seen:
                seen.add(e)
                unique_emails.append(e)

        logger.info(f"Fetched {len(unique_emails)} subscriber emails from Google Sheet")
        return unique_emails

    except Exception as e:
        logger.error(f"Failed to fetch subscriber list: {e}")
        return []


def build_recipient_list(core_emails: str, subscriber_csv_url: str = "") -> str:
    """
    Combine the core EMAIL_TO list with Google Sheet subscribers.

    Args:
        core_emails: Comma-separated core recipient string (from EMAIL_TO)
        subscriber_csv_url: Published CSV URL of the subscriber Google Sheet

    Returns:
        Comma-separated string of all unique recipients.
    """
    # Parse core list
    core = [e.strip().lower() for e in core_emails.split(",") if e.strip()]

    # Fetch subscribers
    subscribers = fetch_subscriber_emails(subscriber_csv_url)

    # Merge and deduplicate (core list first, so it always takes priority)
    seen = set()
    combined = []
    for e in core + subscribers:
        if e and e not in seen:
            seen.add(e)
            combined.append(e)

    logger.info(
        f"Recipient list: {len(core)} core + "
        f"{len(subscribers)} subscribers = {len(combined)} total unique"
    )
    return ",".join(combined)


if __name__ == "__main__":
    import os
    logging.basicConfig(level=logging.INFO)
    url = os.environ.get("SUBSCRIBER_CSV_URL", "")
    core = os.environ.get("EMAIL_TO", "test@example.com")
    result = build_recipient_list(core, url)
    print(f"Final recipient list:\n{result}")
