# 🇮🇩 Indonesia Daily News Briefing

Automated daily scraper that collects headlines from major Indonesian news outlets, generates an AI-powered English summary with embedded hyperlinks, archives all headlines to a searchable Excel database with AI-assigned topic categories, and emails you a formatted briefing every morning.

## Sources

| Outlet | Method | Language |
|--------|--------|----------|
| Detik.com | RSS (HTML fallback) | Bahasa Indonesia |
| Tempo.co | RSS | Bahasa Indonesia |
| Liputan6.com | RSS | Bahasa Indonesia |
| CNN Indonesia | RSS | Bahasa Indonesia |
| Antara News | RSS | English |

## What You Get

**Daily email briefing** with:
- **Executive summary** — Top themes and stories in English, with clickable hyperlinks to source articles (AI-generated via Claude)
- **Appendix** — Every headline with a clickable link, grouped by source

**Growing Excel archive** (`headlines_archive.xlsx`) with:
- Every headline collected, categorised by AI into topics (e.g., Politics, Economy, Defence/Security, Foreign Affairs, Energy, Legal/Judiciary, etc.)
- Filterable columns: Date, Source, Headline, Topic
- Updated automatically after each daily run and committed back to the repo

## Quick Start

### 1. Clone and install

```bash
git clone https://github.com/rattanarcher/Indo-news-briefing.git
cd Indo-news-briefing
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
# Load env vars and run (Linux/Mac)
export $(cat .env | xargs) && python main.py

# Windows Command Prompt
for /f "delims=" %a in (.env) do @set "%a"
python main.py
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
   | `EMAIL_TO` | Recipient address(es) — comma-separated for multiple recipients |

4. The workflow runs automatically at **7:00 AM AEDT** every day
5. You can also trigger it manually from the **Actions** tab → **Run workflow**
6. The `headlines_archive.xlsx` file is automatically committed back to the repo after each run

## Project Structure

```
indo-news-briefing/
├── main.py                          # Pipeline orchestrator (4 steps)
├── src/
│   ├── scraper.py                   # Headline fetcher (RSS + HTML fallback)
│   ├── summarizer.py                # Claude API summarization with hyperlinks
│   ├── emailer.py                   # HTML email builder + SMTP sender
│   └── archive.py                   # AI topic categorisation + Excel archive
├── headlines_archive.xlsx           # Growing headline database (auto-updated)
├── .github/workflows/
│   └── daily_news.yml               # GitHub Actions cron + auto-commit
├── .env.example                     # Environment variable template
├── requirements.txt                 # Python dependencies
└── README.md
```

## Pipeline Steps

1. **Scrape** — Fetch headlines from 4 Indonesian news sources via RSS (with HTML fallback)
2. **Summarise** — Send headlines to Claude API for an English executive summary with embedded hyperlinks
3. **Archive** — Send headlines to Claude API for topic categorisation, then append to Excel database
4. **Email** — Format and send the briefing to all recipients via SMTP

## Customisation

- **Add/remove sources** — Edit the fetcher functions and `fetchers` list in `src/scraper.py`
- **Change summary language** — Edit the prompt in `src/summarizer.py`
- **Change topic categories** — Edit the categorisation prompt in `src/archive.py`
- **Change schedule** — Edit the cron expression in `.github/workflows/daily_news.yml`
- **Change Claude model** — Set `CLAUDE_MODEL` env var (default: `claude-sonnet-4-20250514`)
- **Add email recipients** — Update `EMAIL_TO` secret with comma-separated addresses

## Troubleshooting

- **No headlines from a source?** — RSS feed URL may have changed. Check `scraper.py` and update the feed URLs, or add new HTML fallback selectors.
- **Email not arriving?** — Check spam folder. For Gmail, ensure you're using an App Password with 2-Step Verification enabled.
- **API errors?** — Verify your key at [console.anthropic.com](https://console.anthropic.com/).
- **Excel file not updating?** — Check the Actions tab on GitHub for errors in the commit step.

## Cost

~$0.01/day with Claude Sonnet (summary + categorisation of ~60 headlines). That's roughly **$3–4/year**.
