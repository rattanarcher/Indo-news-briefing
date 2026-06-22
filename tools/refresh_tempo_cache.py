"""
Refresh the Tempo cache from your own (residential) machine.

Tempo's Cloudflare blocks the GitHub Actions datacentre IP, so the hosted
pipeline cannot fetch Tempo. This script fetches Tempo from YOUR connection
(which Tempo accepts), writes the result to tempo_cache.json, and commits and
pushes it. The pipeline then reads that cache at 7am instead of fetching Tempo.

Run it whenever your machine is on. The companion Windows Task Scheduler job
runs it twice a day (2pm and 10pm) so a single failed run does not starve the
next morning's briefing. The pipeline accepts a cache up to
TEMPO_CACHE_MAX_AGE_HOURS old (default 24h): the 10pm refresh leaves the 7am
read about 9 hours old, and if 10pm fails the 2pm cache is still ~17h old at
7am, inside the window. Missing both for a day means Tempo simply sits out until
the next successful run. Nothing breaks.

Usage:
    python tools/refresh_tempo_cache.py            # fetch, write, commit, push
    python tools/refresh_tempo_cache.py --no-git   # fetch and write only

Optionally automate it with Windows Task Scheduler to run, say, daily at noon
while you are likely online. See the setup notes Claude provided.
"""

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

# Run from the repo root so imports and the output path resolve correctly
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from src.scraper import fetch_tempo_raw  # noqa: E402

CACHE_PATH = REPO_ROOT / "tempo_cache.json"


def main():
    do_git = "--no-git" not in sys.argv

    print("Fetching Tempo from this machine's IP ...")
    headlines = fetch_tempo_raw()

    if not headlines:
        print("No Tempo headlines fetched. Cache left unchanged so a transient "
              "failure does not wipe a good cache. Try again shortly.")
        sys.exit(1)

    payload = {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "count": len(headlines),
        "headlines": [h.to_dict() for h in headlines],
    }
    CACHE_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                          encoding="utf-8")
    print(f"Wrote {len(headlines)} Tempo headlines to {CACHE_PATH.name}")

    if not do_git:
        print("--no-git set; skipping commit/push.")
        return

    try:
        subprocess.run(["git", "add", str(CACHE_PATH)], cwd=REPO_ROOT, check=True)
        # Only commit if the cache actually changed
        diff = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=REPO_ROOT)
        if diff.returncode == 0:
            print("Cache unchanged since last commit; nothing to push.")
            return
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        subprocess.run(["git", "commit", "-m", f"chore: refresh Tempo cache ({stamp})"],
                       cwd=REPO_ROOT, check=True)
        subprocess.run(["git", "pull", "--no-rebase"], cwd=REPO_ROOT, check=True)
        subprocess.run(["git", "push"], cwd=REPO_ROOT, check=True)
        print("Committed and pushed Tempo cache.")
    except subprocess.CalledProcessError as e:
        print(f"Git step failed: {e}. The cache file is written; you can commit "
              f"it manually.")
        sys.exit(1)


if __name__ == "__main__":
    main()
