"""
Job search logic:
  - JobSpy      → LinkedIn + Indeed (commercial job boards)
  - Euraxess    → EU academic/research positions (RSS) — may be blocked by Cloudflare
  - jobs.ac.uk  → UK/EU academic positions (RSS) — may return 500 intermittently
  - KTH         → Royal Institute of Technology open positions (HTML + detail-page scrape)
  - SU Varbi    → Stockholm University open positions (RSS feed)
"""

import calendar
import hashlib
import json
import os
import re
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone

import feedparser
import pandas as pd
import requests
from bs4 import BeautifulSoup

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

_MONTH_ABBR = {m.lower(): i for i, m in enumerate(calendar.month_abbr) if m}


def _normalize_date(s: str) -> str:
    """Convert any date string to YYYY-MM-DD, or return '' if unparseable."""
    if not s:
        return ""
    s = s.strip()
    if re.match(r"^\d{4}-\d{2}-\d{2}", s):
        return s[:10]
    # DD.Mon.YYYY  or  DD/Mon/YYYY  (e.g. "24.Feb.2026")
    m = re.match(r"^(\d{1,2})[./](\w+)[./](\d{4})$", s)
    if m:
        day, mon, year = m.groups()
        mon_num = _MONTH_ABBR.get(mon[:3].lower())
        if mon_num:
            return f"{year}-{mon_num:02d}-{int(day):02d}"
    return ""


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


RSS_TIMEOUT = 10  # seconds per HTTP request before giving up


def _fetch_feed(url: str):
    """Fetch an RSS URL with a hard timeout, then parse with feedparser."""
    resp = requests.get(url, timeout=RSS_TIMEOUT, headers={"User-Agent": "Mozilla/5.0"})
    resp.raise_for_status()
    return feedparser.parse(resp.content)


# ── Euraxess RSS ───────────────────────────────────────────────────────────────

def search_euraxess(query: str, country_id: int | None = None) -> pd.DataFrame:
    url = f"https://euraxess.ec.europa.eu/jobs/search/rss?query={query.replace(' ', '+')}"
    if country_id:
        url += f"&f%5B0%5D=job_country%3A{country_id}"
    try:
        feed   = _fetch_feed(url)
        cutoff = datetime.now(timezone.utc) - timedelta(hours=ACADEMIC_HOURS_OLD)
        rows   = []
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
    url = f"https://www.jobs.ac.uk/search/rss/?keywords={query.replace(' ', '+')}&location=europe"
    try:
        feed   = _fetch_feed(url)
        cutoff = datetime.now(timezone.utc) - timedelta(hours=ACADEMIC_HOURS_OLD)
        rows   = []
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


# ── KTH (Royal Institute of Technology) ───────────────────────────────────────

_KTH_SCHOOL = "electrical engineering and computer science"  # EECS filter


def _fetch_kth_detail(job_url: str) -> tuple[str, str, str]:
    """
    Fetch a KTH job detail page.
    Returns (published_date, deadline, school_name).
    school_name is '' if it could not be determined.
    """
    try:
        resp = requests.get(job_url, timeout=RSS_TIMEOUT, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
        soup = BeautifulSoup(resp.content, "html.parser")

        pub = dl = school = ""

        # Try structured JSON first
        script = soup.find("script", string=re.compile(r"__compressedData__DATA"))
        if script:
            m = re.search(r"window\.__compressedData__DATA\s*=\s*'([^']+)'", script.string)
            if m:
                detail = json.loads(urllib.parse.unquote(m.group(1)))
                if isinstance(detail, dict):
                    pub = (detail.get("publishedDate") or detail.get("publicationDate")
                           or detail.get("startDate") or "")
                    dl  = (detail.get("applicationDeadline") or detail.get("lastApplicationDate")
                           or detail.get("endDate") or detail.get("closingDate") or "")
                    school = (detail.get("school") or detail.get("department")
                              or detail.get("organisationName") or detail.get("unit")
                              or detail.get("schoolName") or detail.get("departmentName") or "")
                    pub = _normalize_date(str(pub))
                    dl  = _normalize_date(str(dl))

        # Fallback: regex/text scan
        if not pub or not dl or not school:
            text = soup.get_text(" ", strip=True)
            if not pub:
                m2 = re.search(
                    r'[Pp]ublished[\s:]+(\d{1,2}[\./]\w+[\./]\d{4}|\d{4}-\d{2}-\d{2})', text
                )
                pub = _normalize_date(m2.group(1)) if m2 else ""
            if not dl:
                m3 = re.search(
                    r'[Ll]ast application date[\s:]+(\d{1,2}[\./]\w+[\./]\d{4}|\d{4}-\d{2}-\d{2})',
                    text,
                )
                dl = _normalize_date(m3.group(1)) if m3 else ""
            if not school:
                # KTH school names follow pattern "School of …"
                m4 = re.search(r'School of [A-Z][^\n<]{5,60}', text)
                school = m4.group(0).strip() if m4 else ""

        return pub, dl, school
    except Exception:
        return ("", "", "")


def search_kth() -> pd.DataFrame:
    """Scrape open positions from KTH's job listing page."""
    try:
        resp = requests.get(
            "https://www.kth.se/lediga-jobb?l=en",
            timeout=RSS_TIMEOUT,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        resp.raise_for_status()
        soup = BeautifulSoup(resp.content, "html.parser")

        # KTH embeds job data as URL-encoded JSON in a <script> tag
        script = soup.find("script", string=re.compile(r"__compressedData__DATA"))
        rows = []
        if script:
            m = re.search(r"window\.__compressedData__DATA\s*=\s*'([^']+)'", script.string)
            if m:
                data = json.loads(urllib.parse.unquote(m.group(1)))
                jobs_list = data if isinstance(data, list) else (
                    data.get("jobs") or data.get("items") or data.get("positions") or []
                )
                for job in jobs_list:
                    title = job.get("title") or job.get("name") or ""
                    if not title or not is_relevant(str(title)):
                        continue
                    link = job.get("url") or job.get("link") or job.get("applyUrl") or ""
                    if link and not link.startswith("http"):
                        link = "https://www.kth.se" + link
                    # Try to extract dates from listing JSON first
                    pub = (job.get("publishedDate") or job.get("publicationDate")
                           or job.get("startDate") or "")
                    dl  = (job.get("applicationDeadline") or job.get("lastApplicationDate")
                           or job.get("endDate") or job.get("closingDate") or "")
                    rows.append({
                        "title":             title,
                        "company":           "KTH",
                        "location":          "Stockholm, Sweden",
                        "job_url":           link,
                        "date_posted":       _normalize_date(str(pub)) if pub else "",
                        "deadline":          _normalize_date(str(dl))  if dl  else "",
                        "site":              "kth",
                        "location_priority": 1,
                        "search_query":      "kth",
                    })

        # Fallback: parse job links directly if JSON approach yielded nothing
        if not rows:
            for a in soup.select("a[href*='lediga-jobb']"):
                title = a.get_text(strip=True)
                if not title or not is_relevant(title):
                    continue
                href = a.get("href", "")
                if href and not href.startswith("http"):
                    href = "https://www.kth.se" + href
                rows.append({
                    "title":             title,
                    "company":           "KTH",
                    "location":          "Stockholm, Sweden",
                    "job_url":           href,
                    "date_posted":       "",
                    "deadline":          "",
                    "site":              "kth",
                    "location_priority": 1,
                    "search_query":      "kth",
                })

        if not rows:
            return pd.DataFrame()

        # Fetch every detail page in parallel — needed for school name + dates
        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = {
                pool.submit(_fetch_kth_detail, rows[i]["job_url"]): i
                for i in range(len(rows))
                if rows[i]["job_url"]
            }
            for future in as_completed(futures):
                i = futures[future]
                pub, dl, school = future.result()
                # PhD/doctoral roles → EECS school filter
                # All other roles (engineer, researcher, …) → no school filter
                title_lower = rows[i]["title"].lower()
                is_phd = "phd" in title_lower or "doctoral" in title_lower
                if is_phd and _KTH_SCHOOL not in school.lower():
                    rows[i] = None
                    continue
                if pub:
                    rows[i]["date_posted"] = pub
                if dl:
                    rows[i]["deadline"] = dl

        rows = [r for r in rows if r is not None]
        return pd.DataFrame(rows) if rows else pd.DataFrame()
    except Exception as e:
        print(f"  [kth] {e}")
        return pd.DataFrame()


# ── Stockholm University (Varbi) ───────────────────────────────────────────────

# For SU Varbi: Swedish role titles that always need a field context
_SU_ROLE_SV = ["doktorand", "forskare", "forskningsassistent"]

# If title contains one of the above AND one of these field terms → keep
_SU_FIELD_SV = [
    "informatik", "datalogi", "datateknik",     # computer science
    "mjukvara", "mjukvaruteknik",               # software
    "machine learning", "deep learning",        # ML (used in Swedish academia)
    "artificiell intelligens",                  # AI
    "statistik",                                # statistics (relevant for ML/data)
    "data",                                     # data science
    "it ", " it", "it-",                        # IT
    "cybersäkerhet", "datasäkerhet",            # security
    "matematik",                                # maths (relevant for ML/quant)
    "naturliga språk", "nlp",                   # NLP
    "visualisering",                            # data vis
]

# Swedish job titles that are relevant on their own (no field qualifier needed)
_SU_DIRECT_SV = [
    "programmerare",      # programmer
    "systemutvecklare",   # system developer
    "mjukvaruutvecklare", # software developer
    "it-",                # IT roles (network, security…)
]

_SU_EXCLUDE_SV = [
    "professor",     # too senior
    "lektor",        # senior lecturer
    "adjunkt",       # lecturer
    "administratör", # admin
    "koordinator",   # coordinator
    "ekonom",        # finance admin
    "jurist",        # lawyer
    "postdoktor",    # postdoc (Swedish)
]


def _is_relevant_su(title: str) -> bool:
    """Relevance check for SU Varbi — handles both Swedish and English titles."""
    t = title.lower()
    for kw in TITLE_EXCLUDE + _SU_EXCLUDE_SV:
        if kw in t:
            return False
    # English titles: use standard filter
    if any(kw in t for kw in TITLE_INCLUDE):
        return True
    # Swedish: direct role titles (no field required)
    if any(kw in t for kw in _SU_DIRECT_SV):
        return True
    # Swedish: role + field required
    if any(role in t for role in _SU_ROLE_SV):
        return any(field in t for field in _SU_FIELD_SV)
    return False


# Swedish-title Varbi feeds (use _is_relevant_su for filtering)
_VARBI_UNIVERSITIES = [
    ("Stockholm University", "https://su.varbi.com/en/what:rssfeed/", "Stockholm, Sweden", 1),
    ("Uppsala University",   "https://uu.varbi.com/en/what:rssfeed/", "Uppsala, Sweden",   2),
    ("Lund University",      "https://lu.varbi.com/en/what:rssfeed/", "Lund, Sweden",      2),
]

# English-title feeds — use standard is_relevant() filter
_ENGLISH_UNI_FEEDS = [
    ("Linköping University", "https://liu.se/rss/liu-jobs-en.rss", "Linköping, Sweden", 2),
]


def _search_varbi(name: str, rss_url: str, location: str, loc_priority: int) -> pd.DataFrame:
    """Fetch positions from any Swedish university Varbi RSS feed."""
    try:
        feed   = _fetch_feed(rss_url)
        cutoff = datetime.now(timezone.utc) - timedelta(hours=ACADEMIC_HOURS_OLD)
        rows   = []
        for entry in feed.entries:
            title = entry.get("title", "")
            if not title or not _is_relevant_su(title):
                continue
            if hasattr(entry, "published_parsed") and entry.published_parsed:
                pub = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
                if pub < cutoff:
                    continue
                date_posted = pub.strftime("%Y-%m-%d")
            else:
                date_posted = ""
            rows.append({
                "title":             title,
                "company":           name,
                "location":          location,
                "job_url":           entry.get("link", ""),
                "date_posted":       date_posted,
                "deadline":          "",
                "site":              "varbi",
                "location_priority": loc_priority,
                "search_query":      "varbi",
            })
        return pd.DataFrame(rows)
    except Exception as e:
        print(f"  [varbi/{name}] {e}")
        return pd.DataFrame()


def search_uni_feeds() -> pd.DataFrame:
    """Fetch positions from all configured university RSS feeds in parallel."""
    all_feeds = _VARBI_UNIVERSITIES + _ENGLISH_UNI_FEEDS
    frames = []
    with ThreadPoolExecutor(max_workers=len(all_feeds)) as pool:
        futures = {
            pool.submit(_search_varbi, name, url, loc, pri): name
            for name, url, loc, pri in all_feeds
        }
        for future in as_completed(futures):
            df = future.result()
            if not df.empty:
                frames.append(df)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


search_su_varbi = search_uni_feeds  # keep old name working


# ── Main entry point ───────────────────────────────────────────────────────────

def run_search(status_callback=None, region: str = "all") -> tuple[pd.DataFrame, int]:
    """
    Returns (output_df, new_job_count).
    region: "all" | "sweden" | "eu" | "academic"
      - "sweden"   → LinkedIn/Indeed Sweden only
      - "eu"       → LinkedIn/Indeed non-Sweden (Denmark, Germany, ...)
      - "academic" → Euraxess + jobs.ac.uk only (no LinkedIn/Indeed)
      - "all"      → everything
    """
    seen = load_seen_jobs()
    frames = []

    # Commercial boards (skip for academic-only search)
    if region != "academic":
        if status_callback:
            status_callback("Starting LinkedIn/Indeed search...")
        df_boards = search_all_jobspy(status_callback, region=region)
        if not df_boards.empty:
            frames.append(df_boards)

    # Academic boards — only for academic/all, all queries in parallel
    if region in ("academic", "all"):
        n = len(ACADEMIC_QUERIES)
        if status_callback:
            status_callback(f"Searching KTH & SU Varbi in parallel...")

        # jobs.ac.uk returning 500; Euraxess RSS endpoint gone (404/503)
        # Only run them if you want to re-enable later — they fail silently via try/except
        _EURAXESS_ENABLED  = False
        _JOBSAC_ENABLED    = False

        def _euraxess_sweden(q):
            return search_euraxess(q, country_id=770)

        active_rss = []
        if _EURAXESS_ENABLED:
            active_rss += [(q, "euraxess") for q in ACADEMIC_QUERIES]
            active_rss += [(q, "euraxess_se") for q in ACADEMIC_QUERIES]
        if _JOBSAC_ENABLED:
            active_rss += [(q, "jobsac") for q in ACADEMIC_QUERIES]

        with ThreadPoolExecutor(max_workers=12) as pool:
            futures: dict = {
                pool.submit(
                    search_euraxess if src == "euraxess" else
                    _euraxess_sweden  if src == "euraxess_se" else
                    search_jobsac,
                    q,
                ): (q, src)
                for q, src in active_rss
            }
            # KTH and SU Varbi don't take a query — submit once
            futures[pool.submit(search_kth)]       = ("kth",)
            futures[pool.submit(search_su_varbi)]  = ("su_varbi",)

            for future in as_completed(futures):
                df = future.result()
                if not df.empty:
                    frames.append(df)

    if not frames:
        return pd.DataFrame(), 0

    combined = pd.concat(frames, ignore_index=True)

    for col in ["title", "company", "location", "job_url", "date_posted", "site", "location_priority"]:
        if col not in combined.columns:
            combined[col] = ""

    # Sources that do their own pre-filtering — don't double-filter with English keywords
    _pre_filtered = {"kth", "su.varbi", "varbi", "euraxess", "jobs.ac.uk"}
    combined = combined[combined.apply(
        lambda r: True if r.get("site") in _pre_filtered else is_relevant(str(r["title"])),
        axis=1,
    )]
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
