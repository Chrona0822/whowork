"""
Academic / research job sources:
  - Euraxess  (RSS — may be blocked by Cloudflare)
  - jobs.ac.uk (RSS — may return 500 intermittently)
  - KTH       (Royal Institute of Technology — HTML + detail-page scrape)
  - Varbi     (Stockholm, Uppsala, Lund universities RSS)
  - LiU       (Linköping University RSS)
"""

import json
import re
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone

import pandas as pd
import requests
from bs4 import BeautifulSoup

from backend.config import ACADEMIC_HOURS_OLD, TITLE_EXCLUDE, TITLE_INCLUDE
from backend.sources.utils import _fetch_feed, _normalize_date, is_relevant, RSS_TIMEOUT


# ── Euraxess RSS ──────────────────────────────────────────────────────────────

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


# ── jobs.ac.uk RSS ────────────────────────────────────────────────────────────

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


# ── KTH (Royal Institute of Technology) ──────────────────────────────────────

_KTH_SCHOOL = "electrical engineering and computer science"


def _fetch_kth_detail(job_url: str) -> tuple[str, str, str]:
    """Fetch a KTH detail page. Returns (published_date, deadline, school_name)."""
    try:
        resp = requests.get(job_url, timeout=RSS_TIMEOUT, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
        soup = BeautifulSoup(resp.content, "html.parser")

        pub = dl = school = ""

        script = soup.find("script", string=re.compile(r"__compressedData__DATA"))
        if script:
            m = re.search(r"window\.__compressedData__DATA\s*=\s*'([^']+)'", script.string)
            if m:
                detail = json.loads(urllib.parse.unquote(m.group(1)))
                if isinstance(detail, dict):
                    pub    = (detail.get("publishedDate") or detail.get("publicationDate")
                              or detail.get("startDate") or "")
                    dl     = (detail.get("applicationDeadline") or detail.get("lastApplicationDate")
                              or detail.get("endDate") or detail.get("closingDate") or "")
                    school = (detail.get("school") or detail.get("department")
                              or detail.get("organisationName") or detail.get("unit")
                              or detail.get("schoolName") or detail.get("departmentName") or "")
                    pub = _normalize_date(str(pub))
                    dl  = _normalize_date(str(dl))

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

        script = soup.find("script", string=re.compile(r"__compressedData__DATA"))
        rows = []
        if script:
            m = re.search(r"window\.__compressedData__DATA\s*=\s*'([^']+)'", script.string)
            if m:
                data      = json.loads(urllib.parse.unquote(m.group(1)))
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

        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = {
                pool.submit(_fetch_kth_detail, rows[i]["job_url"]): i
                for i in range(len(rows))
                if rows[i]["job_url"]
            }
            for future in as_completed(futures):
                i = futures[future]
                pub, dl, school = future.result()
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


# ── Swedish university Varbi / RSS feeds ──────────────────────────────────────

_SU_ROLE_SV    = ["doktorand", "forskare", "forskningsassistent"]
_SU_FIELD_SV   = [
    "informatik", "datalogi", "datateknik",
    "mjukvara", "mjukvaruteknik",
    "machine learning", "deep learning",
    "artificiell intelligens",
    "statistik", "data",
    "it ", " it", "it-",
    "cybersäkerhet", "datasäkerhet",
    "matematik", "naturliga språk", "nlp", "visualisering",
]
_SU_DIRECT_SV  = ["programmerare", "systemutvecklare", "mjukvaruutvecklare", "it-"]
_SU_EXCLUDE_SV = ["professor", "lektor", "adjunkt", "administratör",
                  "koordinator", "ekonom", "jurist", "postdoktor"]

_VARBI_UNIVERSITIES = [
    ("Stockholm University", "https://su.varbi.com/en/what:rssfeed/",  "Stockholm, Sweden",  1),
    ("Uppsala University",   "https://uu.varbi.com/en/what:rssfeed/",  "Uppsala, Sweden",    2),
    ("Lund University",      "https://lu.varbi.com/en/what:rssfeed/",  "Lund, Sweden",       2),
    ("Aarhus University",    "https://au.varbi.com/en/what:rssfeed/",  "Aarhus, Denmark",    2),
]
_ENGLISH_UNI_FEEDS = [
    ("Linköping University", "https://liu.se/rss/liu-jobs-en.rss", "Linköping, Sweden", 2),
]

# ── University of Copenhagen (KU) ─────────────────────────────────────────────
# Note: DTU (dtu.dk) uses Oracle Cloud HCM (JS-rendered SPA) and cannot be
# scraped without a headless browser — skipped for now.

# KU positions cover all faculties; restrict to CS/ML/data topics only.
_KU_CS_KEYWORDS = [
    "computer science", "computer", "software", "machine learning",
    " ml ", "ml-", "artificial intelligence", " ai ", "deep learning",
    "neural", "natural language", "nlp", "data science", "data-driven",
    "algorithm", "computational", "robotics", "cybersecurity", "security",
    "bioinformatics", "network", "distributed systems", "cloud",
    "quantitative", "statistics", "programming",
]

def _is_ku_cs(title: str) -> bool:
    t = title.lower()
    return any(kw in t for kw in _KU_CS_KEYWORDS)


def search_ku() -> pd.DataFrame:
    """Scrape open PhD/research positions from University of Copenhagen.
    Only returns positions related to computer science / ML / data."""
    try:
        resp = requests.get(
            "https://employment.ku.dk/phd/?pagelen=9999",
            timeout=RSS_TIMEOUT,
            headers={"User-Agent": "Mozilla/5.0"},
            verify=False,
        )
        resp.raise_for_status()
        soup = BeautifulSoup(resp.content, "html.parser")
        rows = []
        for a in soup.select("a[href]"):
            title = a.get_text(strip=True)
            if not title or len(title) < 8:
                continue
            if not is_relevant(title):
                continue
            if not _is_ku_cs(title):
                continue
            href = a["href"]
            if not href.startswith("http"):
                href = "https://employment.ku.dk" + href
            if "employment.ku.dk" not in href:
                continue
            rows.append({
                "title":             title,
                "company":           "University of Copenhagen",
                "location":          "Copenhagen, Denmark",
                "job_url":           href,
                "date_posted":       "",
                "deadline":          "",
                "site":              "ku",
                "location_priority": 2,
                "search_query":      "ku",
            })
        seen_urls: set = set()
        unique = []
        for r in rows:
            if r["job_url"] not in seen_urls:
                seen_urls.add(r["job_url"])
                unique.append(r)
        return pd.DataFrame(unique) if unique else pd.DataFrame()
    except Exception as e:
        print(f"  [ku] {e}")
        return pd.DataFrame()


def _is_relevant_su(title: str) -> bool:
    t = title.lower()
    for kw in TITLE_EXCLUDE + _SU_EXCLUDE_SV:
        if kw in t:
            return False
    if any(kw in t for kw in TITLE_INCLUDE):
        return True
    if any(kw in t for kw in _SU_DIRECT_SV):
        return True
    if any(role in t for role in _SU_ROLE_SV):
        return any(field in t for field in _SU_FIELD_SV)
    return False


def _search_varbi(name: str, rss_url: str, location: str, loc_priority: int) -> pd.DataFrame:
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
    """Fetch all configured university RSS feeds + University of Copenhagen in parallel."""
    all_feeds = _VARBI_UNIVERSITIES + _ENGLISH_UNI_FEEDS
    frames = []
    with ThreadPoolExecutor(max_workers=len(all_feeds) + 1) as pool:
        futures: dict = {
            pool.submit(_search_varbi, name, url, loc, pri): name
            for name, url, loc, pri in all_feeds
        }
        futures[pool.submit(search_ku)] = "ku"
        for future in as_completed(futures):
            df = future.result()
            if not df.empty:
                frames.append(df)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


search_su_varbi = search_uni_feeds  # backwards-compat alias
