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


def build_email_html(summary: str, all_headlines: dict, date_str: str) -> str:
    """
    Build a nicely formatted HTML email with summary + appendix.
    """
    # Convert summary paragraphs to HTML
    summary_html = "".join(f"<p>{p.strip()}</p>" for p in summary.split("\n\n") if p.strip())
    if not summary_html:
        summary_html = f"<p>{summary}</p>"

    # Build appendix
    appendix_sections = []
    for source, headlines in all_headlines.items():
        if not headlines:
            continue
        items = "".join(
            f'<li><a href="{h.url}" style="color:#1a73e8; text-decoration:none;">{h.title}</a></li>'
            for h in headlines
        )
        appendix_sections.append(f"""
        <h3 style="color:#333; border-bottom:1px solid #ddd; padding-bottom:4px; margin-top:20px;">
            {source}
        </h3>
        <ul style="line-height:1.8;">{items}</ul>
        """)

    appendix_html = "".join(appendix_sections) if appendix_sections else "<p>No headlines available.</p>"

    total_count = sum(len(v) for v in all_headlines.values())
    source_count = sum(1 for v in all_headlines.values() if v)

    html = f"""
    <!DOCTYPE html>
    <html>
    <head><meta charset="utf-8">
    <style>
        .summary-section a {{ color: #1a73e8; text-decoration: underline; }}
        .summary-section a:hover {{ color: #c0392b; }}
    </style>
    </head>
    <body style="font-family: Georgia, 'Times New Roman', serif; max-width:680px; margin:0 auto; padding:20px; color:#222;">

        <div style="border-bottom:3px solid #c0392b; padding-bottom:12px; margin-bottom:24px;">
            <h1 style="margin:0; font-size:24px; color:#c0392b;">
                Indonesia Daily News Briefing
            </h1>
            <p style="margin:4px 0 0; color:#888; font-size:14px;">
                {date_str} &middot; {total_count} headlines from {source_count} sources
            </p>
        </div>

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
        msg["To"] = ", ".join(recipients)

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
        server.sendmail(from_email, recipients, msg.as_string())
        server.quit()

        logger.info(f"Email sent to {', '.join(recipients)}")
        return True

    except Exception as e:
        logger.error(f"Failed to send email: {e}")
        return False
