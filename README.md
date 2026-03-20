# 🇮🇩 Indonesia Daily News Briefing

Automated daily scraper that collects headlines from major Indonesian news outlets, generates an AI-powered English summary, and emails you a formatted briefing every morning.

## Sources

| Outlet | Method | Language |
|--------|--------|----------|
| Detik.com | RSS | Bahasa Indonesia |
| Kompas.com | RSS | Bahasa Indonesia |
| Tempo.co | RSS | Bahasa Indonesia |
| Antara News | HTML scrape | English |

## What You Get

A daily email with:
- **Executive summary** — Top themes and stories in English (AI-generated via Claude)
- **Appendix** — Every headline with a clickable link, grouped by source

## Quick Start

### 1. Clone and install

```bash
git clone https://github.com/YOUR_USERNAME/indo-news-briefing.git
cd indo-news-briefing
pip install -r requirements.txt
```

### 2. Configure environment

```bash
cp .env.example .env
# Edit .env with your actual values
```

You'll need:
- **Anthropic API key** — Get one at [console.anthropic.com](https://console.anthropic.com/)
- **SMTP credentials** — For Gmail, generate an [App Password](https://support.google.com/accounts/answer/185833)

### 3. Test locally

```bash
# Load env vars and run
export $(cat .env | xargs) && python main.py
```

### 4. Deploy to GitHub Actions (automated daily runs)

1. Push this repo to GitHub
2. Go to **Settings → Secrets and variables → Actions**
3. Add these **Repository secrets**:

   | Secret | Value |
   |--------|-------|
   | `ANTHROPIC_API_KEY` | Your Claude API key |
   | `SMTP_HOST` | `smtp.gmail.com` (or your provider) |
   | `SMTP_PORT` | `587` |
   | `SMTP_USER` | Your email address |
   | `SMTP_PASSWORD` | Your email app password |
   | `EMAIL_FROM` | Sender address |
   | `EMAIL_TO` | Recipient address |

4. The workflow runs automatically at **7:00 AM AEDT** every day
5. You can also trigger it manually from the **Actions** tab → **Run workflow**

## Project Structure

```
indo-news-briefing/
├── main.py                          # Pipeline orchestrator
├── src/
│   ├── scraper.py                   # Headline fetcher (RSS + HTML)
│   ├── summarizer.py                # Claude API summarization
│   └── emailer.py                   # HTML email builder + SMTP sender
├── .github/workflows/
│   └── daily_news.yml               # GitHub Actions cron schedule
├── .env.example                     # Environment variable template
├── requirements.txt                 # Python dependencies
└── README.md
```

## Customization

- **Add/remove sources** — Edit the fetcher functions and `fetchers` list in `src/scraper.py`
- **Change summary language** — Edit the prompt in `src/summarizer.py`
- **Change schedule** — Edit the cron expression in `.github/workflows/daily_news.yml`
- **Change Claude model** — Set `CLAUDE_MODEL` env var (default: `claude-sonnet-4-20250514`)

## Troubleshooting

- **No headlines from a source?** — Site may have changed its HTML structure. Check the CSS selectors in `scraper.py` or switch to a different RSS feed URL.
- **Email not arriving?** — Check spam folder. For Gmail, ensure you're using an App Password and have "Less secure apps" considerations handled.
- **API errors?** — Verify your key at [console.anthropic.com](https://console.anthropic.com/). The daily cost is typically under $0.01.

## Cost

~$0.005/day with Claude Sonnet (a few dozen headlines summarized). That's roughly **$1.50/year**.
