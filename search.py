"""
Job search logic:
  - JobSpy  → LinkedIn + Indeed (commercial job boards)
  - Euraxess → EU academic/research positions (RSS)
  - jobs.ac.uk → UK/EU academic positions (RSS)
"""

import hashlib
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone

import feedparser
import pandas as pd

from config import (
    ACADEMIC_HOURS_OLD,
    ACADEMIC_QUERIES,
    HOURS_OLD,
    JOBSPY_QUERIES,
    LOCATIONS,
    MAX_SUMMARY_JOBS,
    RESULTS_PER_CALL,
    SWEDEN_CITY_PRIORITY,
    TITLE_EXCLUDE,
    TITLE_INCLUDE,
)

SEEN_JOBS_FILE = "seen_jobs.txt"


# ── Deduplication helpers ──────────────────────────────────────────────────────

def load_seen_jobs() -> set:
    if not os.path.exists(SEEN_JOBS_FILE):
        return set()
    with open(SEEN_JOBS_FILE) as f:
        return {line.strip() for line in f if line.strip()}


def save_seen_jobs(seen: set) -> None:
    with open(SEEN_JOBS_FILE, "w") as f:
        f.write("\n".join(seen))


def make_job_hash(row) -> str:
    key = f"{row.get('title', '')}|{row.get('company', '')}|{row.get('location', '')}"
    return hashlib.md5(key.encode()).hexdigest()


# ── Relevance filter ───────────────────────────────────────────────────────────

def is_relevant(title: str) -> bool:
    t = title.lower()
    for kw in TITLE_EXCLUDE:
        if kw in t:
            return False
    for kw in TITLE_INCLUDE:
        if kw in t:
            return True
    return False


# ── Location priority scoring ──────────────────────────────────────────────────

def _unique_countries() -> list[dict]:
    """
    Collapse LOCATIONS to one entry per country, keeping the highest priority
    (lowest number) for that country and its indeed_country value.
    Order: by priority ascending so Sweden comes first.
    """
    seen_countries: dict[str, dict] = {}
    for loc in LOCATIONS:
        c = loc["country"]
        if c not in seen_countries or loc["priority"] < seen_countries[c]["priority"]:
            seen_countries[c] = loc
    return sorted(seen_countries.values(), key=lambda x: x["priority"])


def assign_city_priority(location_str: str) -> int:
    """Sub-priority within Sweden: Stockholm first, then Gothenburg, Malmö, others."""
    loc = location_str.lower()
    for city, pri in SWEDEN_CITY_PRIORITY.items():
        if city in loc:
            return pri
    return 9


def assign_location_priority(location_str: str) -> int:
    """
    Score a result row's location string against LOCATIONS.
    City match beats country match; unknown locations get priority 99.
    """
    loc_lower = location_str.lower()
    best = 99
    for loc in LOCATIONS:
        if loc["city"].lower() in loc_lower:
            return loc["priority"]          # exact city match — no need to look further
        if loc["country"].lower() in loc_lower:
            best = min(best, loc["priority"])
    return best


# ── JobSpy (LinkedIn + Indeed) ─────────────────────────────────────────────────

def _search_jobspy_country(query: str, country: dict) -> pd.DataFrame:
    """Search one query across an entire country (not city-by-city)."""
    try:
        from jobspy import scrape_jobs
        df = scrape_jobs(
            site_name=["linkedin", "indeed"],
            search_term=query,
            location=country["country"],          # country-level → broader results
            results_wanted=RESULTS_PER_CALL,
            hours_old=HOURS_OLD,
            country_indeed=country["indeed_country"],
            linkedin_fetch_description=False,
        )
        if not df.empty:
            df["search_query"] = query
        return df
    except Exception as e:
        print(f"  [jobspy] {query} / {country['country']}: {e}")
        return pd.DataFrame()


def search_all_jobspy(status_callback=None, region: str = "all") -> pd.DataFrame:
    """
    For each position query, search all relevant countries in parallel.

    region="all"    → Sweden + Denmark + Germany
    region="sweden" → Sweden only
    region="eu"     → Denmark + Germany (non-Sweden priority ≤ 3)
    """
    all_countries = [c for c in _unique_countries() if c["priority"] <= 3]
    if region == "sweden":
        countries = [c for c in all_countries if c["country"] == "Sweden"]
    elif region == "eu":
        countries = [c for c in all_countries if c["country"] != "Sweden"]
    else:
        countries = all_countries
    frames    = []

    country_names = " | ".join(c["country"] for c in countries)
    for i, query in enumerate(JOBSPY_QUERIES, 1):
        if status_callback:
            status_callback(
                f"[{i}/{len(JOBSPY_QUERIES)}] \"{query}\"  →  {country_names}"
            )

        with ThreadPoolExecutor(max_workers=len(countries)) as pool:
            futures = {pool.submit(_search_jobspy_country, query, c): c for c in countries}
            for future in as_completed(futures):
                df = future.result()
                if not df.empty:
                    frames.append(df)

        time.sleep(2)  # pause between position queries

    if not frames:
        return pd.DataFrame()

    combined = pd.concat(frames, ignore_index=True)
    combined["location_priority"] = combined["location"].fillna("").apply(assign_location_priority)
    return combined


# ── Euraxess RSS ───────────────────────────────────────────────────────────────

def search_euraxess(query: str) -> pd.DataFrame:
    url = f"https://euraxess.ec.europa.eu/jobs/search/rss?query={query.replace(' ', '+')}"
    try:
        feed = feedparser.parse(url)
        cutoff = datetime.now(timezone.utc) - timedelta(hours=ACADEMIC_HOURS_OLD)
        rows = []
        for entry in feed.entries:
            if hasattr(entry, "published_parsed") and entry.published_parsed:
                pub = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
                if pub < cutoff:
                    continue
            location_tag = "Europe"
            if entry.get("tags"):
                location_tag = entry["tags"][0].get("term", "Europe")
            rows.append({
                "title":             entry.get("title", ""),
                "company":           entry.get("author", "Research Institute"),
                "location":          location_tag,
                "job_url":           entry.get("link", ""),
                "date_posted":       entry.get("published", ""),
                "site":              "euraxess",
                "location_priority": 5,
                "search_query":      query,
            })
        return pd.DataFrame(rows)
    except Exception as e:
        print(f"  [euraxess] {query}: {e}")
        return pd.DataFrame()


# ── jobs.ac.uk RSS ─────────────────────────────────────────────────────────────

def search_jobsac(query: str) -> pd.DataFrame:
    url = (
        f"https://www.jobs.ac.uk/search/rss/"
        f"?keywords={query.replace(' ', '+')}&location=europe"
    )
    try:
        feed = feedparser.parse(url)
        cutoff = datetime.now(timezone.utc) - timedelta(hours=ACADEMIC_HOURS_OLD)
        rows = []
        for entry in feed.entries:
            if hasattr(entry, "published_parsed") and entry.published_parsed:
                pub = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
                if pub < cutoff:
                    continue
            rows.append({
                "title":             entry.get("title", ""),
                "company":           entry.get("source", {}).get("title", "University"),
                "location":          "Europe",
                "job_url":           entry.get("link", ""),
                "date_posted":       entry.get("published", ""),
                "site":              "jobs.ac.uk",
                "location_priority": 5,
                "search_query":      query,
            })
        return pd.DataFrame(rows)
    except Exception as e:
        print(f"  [jobs.ac.uk] {query}: {e}")
        return pd.DataFrame()


# ── Main entry point ───────────────────────────────────────────────────────────

def run_search(status_callback=None, region: str = "all") -> tuple[pd.DataFrame, int]:
    """
    Returns (output_df, new_job_count).
    region: "all" | "sweden" | "eu"
      - "sweden" → LinkedIn/Indeed Sweden only, no academic RSS
      - "eu"     → LinkedIn/Indeed non-Sweden + Euraxess/jobs.ac.uk
      - "all"    → everything
    """
    seen = load_seen_jobs()
    frames = []

    # Commercial boards
    if status_callback:
        status_callback("Starting LinkedIn/Indeed search...")
    df_boards = search_all_jobspy(status_callback, region=region)
    if not df_boards.empty:
        frames.append(df_boards)

    # Academic boards — only for eu/all (RSS feeds are Europe-wide, not Sweden-specific)
    if region in ("eu", "all"):
        if status_callback:
            status_callback("Searching Euraxess & jobs.ac.uk...")
        for q in ACADEMIC_QUERIES:
            frames.append(search_euraxess(q))
            frames.append(search_jobsac(q))
            time.sleep(0.5)

    if not frames:
        return pd.DataFrame(), 0

    combined = pd.concat(frames, ignore_index=True)

    for col in ["title", "company", "location", "job_url", "date_posted", "site", "location_priority"]:
        if col not in combined.columns:
            combined[col] = ""

    combined = combined[combined["title"].apply(lambda t: is_relevant(str(t)))]
    combined = combined.drop_duplicates(subset=["job_url"], keep="first")
    combined["job_hash"] = combined.apply(make_job_hash, axis=1)

    new_jobs = combined[~combined["job_hash"].isin(seen)].copy()

    # Sort: location priority → Sweden city sub-priority → newest date
    new_jobs["city_priority"] = new_jobs["location"].fillna("").apply(assign_city_priority)
    new_jobs = new_jobs.sort_values(
        ["location_priority", "city_priority", "date_posted"],
        ascending=[True, True, False],
        na_position="last",
    )

    seen.update(new_jobs["job_hash"].tolist())
    save_seen_jobs(seen)

    output_cols = ["title", "company", "location", "date_posted", "site", "job_url"]
    output = new_jobs[[c for c in output_cols if c in new_jobs.columns]].reset_index(drop=True)

    return output, len(output)
