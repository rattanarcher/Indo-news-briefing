"""
Briefing archive module.

Persists each day's generated briefing alongside the headlines archive, so
the curated "what mattered" layer is preserved for later analysis and
retrieval instead of being emailed and lost.

Layout (committed under briefings/):
  briefings/YYYY-MM-DD.json   structured record: dates, flags, model, each
                              section as HTML + stripped text, the weekly
                              takeaways pulled out, and the cited source URLs.
  briefings/YYYY-MM-DD.html   the rendered email, for eyeballing in a browser.
  briefings/index.csv         one row per day for quick scanning / pandas load.

Designed to run once per day from main.py, after the email HTML is built and
before sending, so the briefing is captured even if the SMTP send fails.
"""

import csv
import json
import logging
import os
import re
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

ARCHIVE_DIR = "briefings"
INDEX_FILE = "index.csv"

_ENTITIES = [
    ("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"),
    ("&quot;", '"'), ("&#39;", "'"), ("&nbsp;", " "),
]


def _strip_html(html: str) -> str:
    """Return readable plain text from an HTML fragment."""
    if not html:
        return ""
    text = re.sub(r"<[^>]+>", " ", html)
    for a, b in _ENTITIES:
        text = text.replace(a, b)
    return re.sub(r"\s+", " ", text).strip()


def _extract_links(html: str) -> list:
    """Return the http(s) URLs referenced by href attributes."""
    if not html:
        return []
    return re.findall(r'href=["\'](https?://[^"\']+)["\']', html)


def _extract_takeaways(weekly_html: str) -> list:
    """Pull the bold one-sentence takeaways out of the weekly review."""
    if not weekly_html:
        return []
    blocks = re.findall(
        r'class="thread-takeaway"[^>]*>(.*?)</p>', weekly_html, flags=re.DOTALL
    )
    return [t for t in (_strip_html(b) for b in blocks) if t]


def archive_briefing(date_iso: str, date_display: str, summary_html: str,
                     weekly_html: str, commentary_html: str, email_html: str,
                     model: str, is_monday: bool, out_dir: str = ARCHIVE_DIR) -> str:
    """
    Write the structured JSON record, the rendered HTML, and the index row
    for one day's briefing. Returns the JSON path, or "" on failure (the
    caller should treat archiving as best-effort and never block sending).
    """
    try:
        os.makedirs(out_dir, exist_ok=True)

        cited = sorted(set(
            _extract_links(summary_html)
            + _extract_links(weekly_html)
            + _extract_links(commentary_html)
        ))
        takeaways = _extract_takeaways(weekly_html)

        record = {
            "date": date_iso,
            "date_display": date_display,
            "is_monday": bool(is_monday),
            "model": model,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "sections": {
                "daily_summary": {
                    "html": summary_html or "",
                    "text": _strip_html(summary_html),
                },
                "weekly_review": {
                    "html": weekly_html or "",
                    "text": _strip_html(weekly_html),
                    "takeaways": takeaways,
                },
                "commentary_review": {
                    "html": commentary_html or "",
                    "text": _strip_html(commentary_html),
                },
            },
            "cited_urls": cited,
            "n_cited_urls": len(cited),
        }

        json_path = os.path.join(out_dir, f"{date_iso}.json")
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(record, f, ensure_ascii=False, indent=2)

        # The faithful render, for browsing past briefings.
        html_path = os.path.join(out_dir, f"{date_iso}.html")
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(email_html or "")

        _update_index(out_dir, record)

        logger.info(
            f"Briefing archived: {json_path} "
            f"({record['n_cited_urls']} cited URLs, {len(takeaways)} takeaways)"
        )
        return json_path

    except Exception as e:
        logger.error(f"Briefing archive failed (non-fatal): {e}")
        return ""


_INDEX_FIELDS = [
    "date", "is_monday", "model",
    "summary_chars", "weekly_chars", "commentary_chars",
    "n_cited_urls", "weekly_takeaways",
]


def _update_index(out_dir: str, record: dict):
    """Append/refresh this day's row in index.csv, deduped by date and sorted."""
    index_path = os.path.join(out_dir, INDEX_FILE)
    sec = record["sections"]
    row = {
        "date": record["date"],
        "is_monday": record["is_monday"],
        "model": record["model"],
        "summary_chars": len(sec["daily_summary"]["html"]),
        "weekly_chars": len(sec["weekly_review"]["html"]),
        "commentary_chars": len(sec["commentary_review"]["html"]),
        "n_cited_urls": record["n_cited_urls"],
        "weekly_takeaways": " || ".join(sec["weekly_review"]["takeaways"]),
    }

    rows = {}
    if os.path.exists(index_path):
        with open(index_path, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                rows[r["date"]] = r
    rows[row["date"]] = row

    with open(index_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=_INDEX_FIELDS)
        writer.writeheader()
        for d in sorted(rows):
            writer.writerow(rows[d])
