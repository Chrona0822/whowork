"""
Standalone job search script — no Discord bot process required.
Saves results to output/jobs_all_YYYYMMDD_HHMM.xlsx and posts to Discord via Webhook.

Run manually:   python run.py
Scheduled:      see setup_cron.sh
"""

import json
import os
from datetime import datetime

import requests
from dotenv import load_dotenv

load_dotenv()

WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")


def post_to_discord(df, count: int, xlsx_path) -> None:
    if not WEBHOOK_URL:
        print("  DISCORD_WEBHOOK_URL not set in .env — skipping Discord post.")
        return

    from whowork.config import MAX_SUMMARY_JOBS
    mime = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

    lines = [f"**{count} new job(s)** — {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"]
    for _, row in df.head(MAX_SUMMARY_JOBS).iterrows():
        title   = str(row.get("title",    ""))[:60]
        company = str(row.get("company",  ""))[:40]
        loc     = str(row.get("location", ""))[:30]
        url     = str(row.get("job_url",  ""))
        site    = str(row.get("site",     ""))
        line = f"• **{title}** @ {company} — {loc} `[{site}]`"
        if url:
            line += f"\n  <{url}>"
        lines.append(line)

    if count > MAX_SUMMARY_JOBS:
        lines.append(f"\n_…and {count - MAX_SUMMARY_JOBS} more in the attachment._")

    resp = requests.post(
        WEBHOOK_URL,
        data={"payload_json": json.dumps({"content": "\n".join(lines)})},
        files={"file": (xlsx_path.name, xlsx_path.read_bytes(), mime)},
        timeout=30,
    )
    resp.raise_for_status()


def main():
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Starting job search...")

    from whowork.health import run_checks, print_banner
    results = run_checks()
    print_banner(results)

    from whowork.search import run_search
    from whowork.export import save_xlsx

    df, count = run_search(status_callback=lambda m: print(f"  {m}"))

    if count == 0:
        print("Done — no new jobs found.")
        if WEBHOOK_URL:
            requests.post(
                WEBHOOK_URL,
                json={"content": f"Job search {datetime.now().strftime('%Y-%m-%d %H:%M')} — no new jobs found."},
                timeout=10,
            )
        return

    from whowork.db import save_run
    save_run(df, region="all")

    xlsx_path = save_xlsx(df, region="all")
    print(f"Done — {count} new job(s) saved to {xlsx_path} and jobs.db")

    try:
        post_to_discord(df, count, xlsx_path)
        print("Posted to Discord.")
    except Exception as e:
        print(f"Discord post failed: {e}")


if __name__ == "__main__":
    main()
