"""
Email module.
Formats the daily briefing as HTML and sends it via SMTP.
"""

import smtplib
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime

logger = logging.getLogger(__name__)


def build_email_html(summary: str, all_headlines: dict, date_str: str,
                     weekly_review: str = "", is_monday: bool = False,
                     categories: list = None) -> str:
    """
    Build a nicely formatted HTML email with summary + appendix.

    On Mondays, weekly_review (HTML paragraphs) is prepended as a
    "What Happened Last Week" section and the header changes to
    "Monday Briefing".
    """
    # Summary now arrives with its own HTML tags (<h3>, <p>, <ul><li>)
    # Pass through directly. If it doesn't look like HTML, wrap in <p>.
    summary_html = summary.strip()
    if not summary_html.startswith("<"):
        # Fallback for plain text - wrap paragraphs
        summary_html = "".join(
            f"<p>{p.strip()}</p>" for p in summary_html.split("\n\n") if p.strip()
        )

    # Build appendix - grouped by the three topic buckets.
    # categories is a flat list aligned with the headlines flattened
    # source-by-source (same order archive.categorize_all produces).
    from src.archive import bucket_for_category, APPENDIX_BUCKETS

    # Flatten headlines in the same order the categories were computed
    flat = []
    for source, headlines in all_headlines.items():
        for h in headlines:
            flat.append(h)

    # Group headlines into the three buckets
    buckets = {b: [] for b in APPENDIX_BUCKETS}
    for i, h in enumerate(flat):
        if categories and i < len(categories):
            bucket = bucket_for_category(categories[i])
        else:
            # No category available (categorisation failed) - default bucket
            bucket = "Other"
        buckets[bucket].append(h)

    appendix_sections = []
    for bucket in APPENDIX_BUCKETS:
        items_list = buckets[bucket]
        if not items_list:
            continue
        items = "".join(
            f'<li><a href="{h.url}" style="color:#1a73e8; text-decoration:none;">{h.title}</a></li>'
            for h in items_list
        )
        appendix_sections.append(f"""
        <h3 style="color:#333; border-bottom:1px solid #ddd; padding-bottom:4px; margin-top:20px;">
            {bucket}
        </h3>
        <ul style="line-height:1.8;">{items}</ul>
        """)

    appendix_html = "".join(appendix_sections) if appendix_sections else "<p>No headlines available.</p>"

    # Monday-specific header and weekly review section
    header_title = "Monday Briefing" if is_monday else "Indonesia Daily News Briefing"

    weekly_review_block = ""
    if is_monday and weekly_review.strip():
        review_html = weekly_review.strip()
        if not review_html.startswith("<"):
            review_html = "".join(
                f"<p>{p.strip()}</p>" for p in review_html.split("\n\n") if p.strip()
            )
        weekly_review_block = f"""
        <div class="summary-section" style="background:#f4f0e8; border-left:4px solid #8a6d3b; padding:16px 20px; margin-bottom:28px;">
            <h2 style="margin:0 0 12px; font-size:18px; color:#333;">What Happened Last Week</h2>
            {review_html}
        </div>
        """

    html = f"""
    <!DOCTYPE html>
    <html>
    <head><meta charset="utf-8">
    <style>
        .summary-section a {{ color: #1a73e8; text-decoration: underline; }}
        .summary-section a:hover {{ color: #c0392b; }}
        .summary-section h3 {{
            font-size: 16px;
            color: #c0392b;
            margin: 18px 0 8px;
            padding-bottom: 4px;
            border-bottom: 1px solid #ddd;
            font-family: Georgia, serif;
        }}
        .summary-section h3:first-child {{ margin-top: 0; }}
        .summary-section p {{ margin: 0 0 10px; line-height: 1.55; }}
        .summary-section ul {{ margin: 0 0 14px; padding-left: 22px; }}
        .summary-section li {{ margin-bottom: 6px; line-height: 1.5; }}
    </style>
    </head>
    <body style="font-family: Georgia, 'Times New Roman', serif; max-width:680px; margin:0 auto; padding:20px; color:#222;">

        <div style="border-bottom:3px solid #c0392b; padding-bottom:12px; margin-bottom:24px;">
            <h1 style="margin:0; font-size:24px; color:#c0392b;">
                {header_title}
            </h1>
            <p style="margin:4px 0 0; color:#888; font-size:14px;">
                {date_str}
            </p>
        </div>

        {weekly_review_block}

        <div class="summary-section" style="background:#fafafa; border-left:4px solid #c0392b; padding:16px 20px; margin-bottom:28px;">
            <h2 style="margin:0 0 12px; font-size:18px; color:#333;">Key Stories Today</h2>
            {summary_html}
        </div>

        <div>
            <h2 style="font-size:18px; color:#333; border-bottom:2px solid #eee; padding-bottom:6px;">
                Appendix: All Headlines &amp; Links
            </h2>
            {appendix_html}
        </div>

        <div style="margin-top:32px; padding-top:12px; border-top:1px solid #ddd; color:#aaa; font-size:12px;">
            Generated automatically by Indo News Briefing &middot;
            Powered by Claude API
        </div>

    </body>
    </html>
    """
    return html


def send_email(
    smtp_host: str,
    smtp_port: int,
    smtp_user: str,
    smtp_password: str,
    from_email: str,
    to_email: str,
    subject: str,
    html_body: str,
    use_tls: bool = True,
) -> bool:
    """
    Send an HTML email via SMTP.

    Returns True on success, False on failure.
    """
    try:
        # Support multiple recipients (comma-separated)
        recipients = [e.strip() for e in to_email.split(",") if e.strip()]

        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = from_email
        # Address the visible "To" to the sender itself; put all real
        # recipients in BCC so subscribers cannot see each other's emails.
        msg["To"] = from_email

        # Plain text fallback
        plain_text = "Your email client does not support HTML. Please view this email in a modern client."
        msg.attach(MIMEText(plain_text, "plain"))
        msg.attach(MIMEText(html_body, "html"))

        if use_tls:
            server = smtplib.SMTP(smtp_host, smtp_port)
            server.starttls()
        else:
            server = smtplib.SMTP_SSL(smtp_host, smtp_port)

        server.login(smtp_user, smtp_password)
        # sendmail's recipient list is the actual delivery (BCC behaviour) -
        # these addresses do not appear in the message headers.
        server.sendmail(from_email, recipients, msg.as_string())
        server.quit()

        logger.info(f"Email sent to {len(recipients)} recipient(s) via BCC")
        return True

    except Exception as e:
        logger.error(f"Failed to send email: {e}")
        return False
