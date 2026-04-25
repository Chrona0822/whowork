"""
Job search orchestrator.

Coordinates all sources (JobSpy, thehub.io, academic feeds) and
handles deduplication, relevance filtering, and sorting.

Sources live in backend/sources/:
  jobspy.py    — LinkedIn + Indeed
  thehub.py    — thehub.io (Nordic job board)
  academic.py  — KTH, SU Varbi, Uppsala, Lund, Linköping, Euraxess, jobs.ac.uk
"""

import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pandas as pd

from backend.config import ACADEMIC_QUERIES, HOURS_OLD
from backend.sources.academic import (
    search_euraxess,
    search_jobsac,
    search_kth,
    search_uni_feeds,
)
from backend.sources.jobspy import search_all_jobspy
from backend.sources.thehub import search_thehub
from backend.sources.utils import (
    assign_city_priority,
    is_relevant,
    make_job_hash,
)

SEEN_JOBS_FILE = str(Path(__file__).parent / "data" / "seen_jobs.txt")

# Sources that pre-filter by relevance — skip the English keyword check
_PRE_FILTERED = {"kth", "varbi", "euraxess", "jobs.ac.uk", "thehub.io"}


# ── Seen-jobs deduplication ───────────────────────────────────────────────────

def load_seen_jobs() -> set:
    if not os.path.exists(SEEN_JOBS_FILE):
        return set()
    with open(SEEN_JOBS_FILE) as f:
        return {line.strip() for line in f if line.strip()}


def save_seen_jobs(seen: set) -> None:
    with open(SEEN_JOBS_FILE, "w") as f:
        f.write("\n".join(seen))


# ── Main entry point ──────────────────────────────────────────────────────────

def run_search(
    status_callback=None,
    region: str = "all",
    hours_old: int = HOURS_OLD,
) -> tuple[pd.DataFrame, int]:
    """
    Run all relevant sources for the given region and return (output_df, new_count).

    region="sweden"   → JobSpy (Sweden) + thehub.io (Sweden)
    region="eu"       → JobSpy (EU) + thehub.io (EU)
    region="academic" → KTH + Varbi + Euraxess + jobs.ac.uk
    region="all"      → everything
    """
    seen   = load_seen_jobs()
    frames = []

    # ── Commercial boards (JobSpy + thehub.io) ────────────────────────────────
    if region != "academic":
        if status_callback:
            status_callback("Searching LinkedIn & Indeed...")
        df_jobspy = search_all_jobspy(status_callback, region=region, hours_old=hours_old)
        if not df_jobspy.empty:
            frames.append(df_jobspy)

        if status_callback:
            status_callback("Searching thehub.io...")
        df_thehub = search_thehub(region=region, hours_old=hours_old)
        if not df_thehub.empty:
            frames.append(df_thehub)

    # ── Academic boards ───────────────────────────────────────────────────────
    if region in ("academic", "all"):
        if status_callback:
            status_callback("Searching KTH, universities & academic feeds...")

        _EURAXESS_ENABLED = False
        _JOBSAC_ENABLED   = False

        def _euraxess_sweden(q):
            return search_euraxess(q, country_id=770)

        active_rss = []
        if _EURAXESS_ENABLED:
            active_rss += [(q, "euraxess")    for q in ACADEMIC_QUERIES]
            active_rss += [(q, "euraxess_se") for q in ACADEMIC_QUERIES]
        if _JOBSAC_ENABLED:
            active_rss += [(q, "jobsac") for q in ACADEMIC_QUERIES]

        with ThreadPoolExecutor(max_workers=12) as pool:
            futures: dict = {
                pool.submit(
                    search_euraxess if src == "euraxess" else
                    _euraxess_sweden if src == "euraxess_se" else
                    search_jobsac,
                    q,
                ): (q, src)
                for q, src in active_rss
            }
            futures[pool.submit(search_kth)]        = ("kth",)
            futures[pool.submit(search_uni_feeds)]  = ("uni_feeds",)

            for future in as_completed(futures):
                df = future.result()
                if not df.empty:
                    frames.append(df)

    if not frames:
        return pd.DataFrame(), 0

    # ── Merge & clean ─────────────────────────────────────────────────────────
    combined = pd.concat(frames, ignore_index=True)

    for col in ["title", "company", "location", "job_url", "date_posted", "site", "location_priority"]:
        if col not in combined.columns:
            combined[col] = ""

    # Relevance filter — skip for pre-filtered sources
    combined = combined[combined.apply(
        lambda r: True if r.get("site") in _PRE_FILTERED else is_relevant(str(r["title"])),
        axis=1,
    )]
    combined = combined.drop_duplicates(subset=["job_url"], keep="first")
    combined["job_hash"] = combined.apply(make_job_hash, axis=1)

    new_jobs = combined[~combined["job_hash"].isin(seen)].copy()

    # Sort: location priority → city sub-priority → newest first
    new_jobs["city_priority"] = new_jobs["location"].fillna("").apply(assign_city_priority)
    new_jobs = new_jobs.sort_values(
        ["location_priority", "city_priority", "date_posted"],
        ascending=[True, True, False],
        na_position="last",
    )

    seen.update(new_jobs["job_hash"].tolist())
    save_seen_jobs(seen)

    output_cols = ["title", "company", "location", "date_posted", "deadline", "site", "job_url", "description"]
    output = new_jobs[[c for c in output_cols if c in new_jobs.columns]].reset_index(drop=True)

    # Truncate descriptions — Ollama only reads ~1500 chars, no need to store more
    if "description" in output.columns:
        output["description"] = output["description"].fillna("").str[:2000]

    return output, len(output)
