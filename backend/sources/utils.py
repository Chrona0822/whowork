"""
Shared utilities used across all job sources:
  - Date normalisation
  - Job hash (deduplication key)
  - Relevance filter (title include/exclude)
  - Location priority scoring
  - RSS fetch helper
"""

import calendar
import hashlib
import re

import feedparser
import requests

from backend.config import (
    LOCATIONS,
    SWEDEN_CITY_PRIORITY,
    TITLE_EXCLUDE,
    TITLE_INCLUDE,
)

RSS_TIMEOUT = 10  # seconds per HTTP request

_MONTH_ABBR = {m.lower(): i for i, m in enumerate(calendar.month_abbr) if m}


# ── Date ──────────────────────────────────────────────────────────────────────

def _normalize_date(s: str) -> str:
    """Convert any date string to YYYY-MM-DD, or return '' if unparseable."""
    if not s:
        return ""
    s = s.strip()
    if re.match(r"^\d{4}-\d{2}-\d{2}", s):
        return s[:10]
    m = re.match(r"^(\d{1,2})[./](\w+)[./](\d{4})$", s)
    if m:
        day, mon, year = m.groups()
        mon_num = _MONTH_ABBR.get(mon[:3].lower())
        if mon_num:
            return f"{year}-{mon_num:02d}-{int(day):02d}"
    return ""


# ── Deduplication ─────────────────────────────────────────────────────────────

def make_job_hash(row) -> str:
    key = f"{row.get('title', '')}|{row.get('company', '')}|{row.get('location', '')}"
    return hashlib.md5(key.encode()).hexdigest()


# ── Relevance filter ──────────────────────────────────────────────────────────

def is_relevant(title: str) -> bool:
    t = title.lower()
    for kw in TITLE_EXCLUDE:
        if kw in t:
            return False
    for kw in TITLE_INCLUDE:
        if kw in t:
            return True
    return False


# ── Location priority scoring ─────────────────────────────────────────────────

def _unique_countries() -> list[dict]:
    """One entry per country, keeping highest priority. Sorted by priority asc."""
    seen: dict[str, dict] = {}
    for loc in LOCATIONS:
        c = loc["country"]
        if c not in seen or loc["priority"] < seen[c]["priority"]:
            seen[c] = loc
    return sorted(seen.values(), key=lambda x: x["priority"])


def assign_city_priority(location_str: str) -> int:
    """Sub-priority within Sweden: Stockholm first, then Gothenburg, Malmö."""
    loc = location_str.lower()
    for city, pri in SWEDEN_CITY_PRIORITY.items():
        if city in loc:
            return pri
    return 9


def assign_location_priority(location_str: str) -> int:
    """Score a location string against LOCATIONS. Unknown → 99."""
    loc_lower = location_str.lower()
    best = 99
    for loc in LOCATIONS:
        if loc["city"].lower() in loc_lower:
            return loc["priority"]
        if loc["country"].lower() in loc_lower:
            best = min(best, loc["priority"])
    return best


# ── RSS helper ────────────────────────────────────────────────────────────────

def _fetch_feed(url: str):
    """Fetch an RSS URL with a hard timeout, then parse with feedparser."""
    resp = requests.get(url, timeout=RSS_TIMEOUT, headers={"User-Agent": "Mozilla/5.0"})
    resp.raise_for_status()
    return feedparser.parse(resp.content)
