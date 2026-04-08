"""
Discord bot for job alerts.

Commands:
  !jobsv    — Sweden only (Stockholm → Gothenburg → Malmö → other SE)
  !jobeu    — Europe excl. Sweden (Denmark, Germany, ...) — 24h window
  !jobeu7d  — Europe excl. Sweden — 7-day window
  !status   — how many jobs have been seen so far
  !reset    — clear seen-jobs history
"""

import asyncio
import os
import re
import sys
from datetime import datetime

import discord
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()

TOKEN             = os.getenv("DISCORD_TOKEN")
RESTART_FLAG_FILE = ".restart_channel"   # stores channel ID across restarts

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)


@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")
    # If we just restarted via !restart, notify the original channel
    if os.path.exists(RESTART_FLAG_FILE):
        try:
            channel_id = int(open(RESTART_FLAG_FILE, encoding="utf-8").read().strip())
            channel    = bot.get_channel(channel_id)
            if channel:
                await channel.send("Back online ✓")
        except Exception:
            pass
        finally:
            os.remove(RESTART_FLAG_FILE)


# ── Shared search helper ───────────────────────────────────────────────────────

async def _run_and_respond(ctx, region: str, label: str, hours_old: int = None):
    from whowork.health import run_checks
    from whowork.config import HOURS_OLD as DEFAULT_HOURS
    if hours_old is None:
        hours_old = DEFAULT_HOURS

    results = await asyncio.get_event_loop().run_in_executor(None, run_checks, True)
    failures = [name for name, (ok, msg) in results.items() if not ok]
    if failures:
        warn = "⚠ " + ", ".join(
            f"**{name}** — {msg}" for name, (ok, msg) in results.items() if not ok
        )
        await ctx.send(warn)

    window = f"{hours_old // 24}d" if hours_old >= 24 else f"{hours_old}h"
    status_msg = await ctx.send(f"Searching **{label}** jobs... ⏳")

    messages = []

    def _search():
        from whowork.search import run_search
        return run_search(
            status_callback=lambda m: messages.append(m),
            region=region,
            hours_old=hours_old,
        )

    df, count = await asyncio.get_event_loop().run_in_executor(None, _search)

    if count == 0:
        await status_msg.edit(content=f"No new **{label}** jobs found in the past {window}.")
        return

    # Persist to DB
    from whowork.db import save_run
    save_run(df, region=region)

    # Build inline summary (top 10)
    from whowork.config import MAX_SUMMARY_JOBS
    lines = [f"**{count} new {label} job(s)** — {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"]
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
    from whowork.config import WEB_URL
    lines.append(f"\n_Open {WEB_URL} to track applications._")

    await status_msg.delete()
    await ctx.send("\n".join(lines))


# ── !jobsv — Sweden only (fixed 24h) ──────────────────────────────────────────

@bot.command(name="jobsv")
async def jobsv_command(ctx):
    await _run_and_respond(ctx, region="sweden", label="Sweden")


# ── !jobsv<N> — Sweden, dynamic hour window ───────────────────────────────────

@bot.event
async def on_message(message):
    if message.author.bot:
        return
    m = re.fullmatch(r"!jobsv(\d+)", message.content.strip())
    if m:
        hours = int(m.group(1))
        ctx = await bot.get_context(message)
        await _run_and_respond(ctx, region="sweden", label=f"Sweden ({hours}h)", hours_old=hours)
        return
    await bot.process_commands(message)


# ── !jobeu — Europe excl. Sweden ──────────────────────────────────────────────

@bot.command(name="jobeu")
async def jobeu_command(ctx):
    await _run_and_respond(ctx, region="eu", label="Europe (non-SE)")


# ── !jobeu7d — Europe excl. Sweden, 7-day window ──────────────────────────────

@bot.command(name="jobeu7d")
async def jobeu7d_command(ctx):
    await _run_and_respond(ctx, region="eu", label="Europe (non-SE, 7d)", hours_old=168)


# ── !jobac — Academic / research roles ────────────────────────────────────────

@bot.command(name="jobac")
async def jobac_command(ctx):
    await _run_and_respond(ctx, region="academic", label="Academic & Research")


# ── !status ────────────────────────────────────────────────────────────────────

@bot.command(name="status")
async def status_command(ctx):
    from whowork.search import load_seen_jobs, SEEN_JOBS_FILE
    seen = load_seen_jobs()
    await ctx.send(f"Seen-jobs history: **{len(seen)}** unique jobs in `{SEEN_JOBS_FILE}`.")


# ── !restart ──────────────────────────────────────────────────────────────────

@bot.command(name="restart")
async def restart_command(ctx):
    import subprocess
    # Restart web UI via launchd
    web_plist = os.path.expanduser("~/Library/LaunchAgents/com.whowork.webui.plist")
    if os.path.exists(web_plist):
        subprocess.run(["launchctl", "unload", web_plist], capture_output=True)
        subprocess.run(["launchctl", "load",   web_plist], capture_output=True)
    # Save channel ID so on_ready can confirm when bot is back
    with open(RESTART_FLAG_FILE, "w") as f:
        f.write(str(ctx.channel.id))
    await ctx.send("Restarting everything... I'll confirm when I'm back.")
    await bot.close()
    sys.exit(0)   # launchd KeepAlive=true restarts the bot automatically


# ── !help ─────────────────────────────────────────────────────────────────────

@bot.command(name="help")
async def help_command(ctx):
    await ctx.send(
        "**Job Alert Bot — Commands**\n\n"
        "`!jobsv`      — Search Sweden (Stockholm → Gothenburg → Malmö) — 24h\n"
        "`!jobsv<N>`   — Same but N hours back (e.g. !jobsv48 = last 48h)\n"
        "`!jobeu`   — Search Europe excl. Sweden (Denmark, Germany, ...) — 24h\n"
        "`!jobeu7d` — Same as !jobeu but with a 7-day window\n"
        "`!jobac`  — Search academic & research roles (Euraxess + jobs.ac.uk + KTH + SU Varbi, 7-day window)\n"
        "`!status` — Show how many jobs have been seen so far\n"
        "`!reset`  — Clear seen-jobs history (next search resurfaces all jobs)\n"
        "`!restart`— Restart the bot\n"
        "`!health` — Check Ollama, Web UI, and Database status\n"
        "`!help`   — Show this message\n\n"
        f"_Results are saved to {WEB_URL} — mark jobs as applied there._"
    )


# ── !health ───────────────────────────────────────────────────────────────────

@bot.command(name="health")
async def health_command(ctx):
    from whowork.health import run_checks
    results = await asyncio.get_event_loop().run_in_executor(None, run_checks, True)
    lines = ["**Service Health**"]
    for name, (ok, msg) in results.items():
        icon = "✅" if ok else "❌"
        lines.append(f"{icon} **{name}**: {msg}")
    await ctx.send("\n".join(lines))


# ── !reset ─────────────────────────────────────────────────────────────────────

@bot.command(name="reset")
async def reset_command(ctx):
    from whowork.search import SEEN_JOBS_FILE
    if os.path.exists(SEEN_JOBS_FILE):
        os.remove(SEEN_JOBS_FILE)
        await ctx.send("History cleared. Next search will surface all matching jobs again.")
    else:
        await ctx.send("No history file found — nothing to clear.")


# ── Run ────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if not TOKEN:
        raise ValueError("DISCORD_TOKEN not set in .env")
    bot.run(TOKEN)
