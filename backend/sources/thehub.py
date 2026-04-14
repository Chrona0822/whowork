"""
thehub.io scraper.

thehub.io is a Nordic-focused job board with clean role/location filters.
URL structure:  https://thehub.io/jobs?roles[]=backend-developer&locations[]=sweden
The page is Next.js SSR, so job data lives in <script id="__NEXT_DATA__">.
Falls back to HTML parsing if the JSON structure changes.
"""

import json
from datetime import datetime, timedelta, timezone

import pandas as pd
import requests
from bs4 import BeautifulSoup

from backend.config import HOURS_OLD
from backend.sources.utils import is_relevant

# Role slugs on thehub.io that match our job categories
_ROLES = [
    "backend-developer",
    "frontend-developer",
    "full-stack-developer",
    "data-science",
    "devops",
    "quality-assurance",
    "machine-learning",
    "software-engineer",
    "engineering",
]

# Job types to include
_JOB_TYPES = ["full-time", "internship", "student"]

# Location slugs per region
_LOCATIONS: dict[str, list[str]] = {
    "sweden": ["sweden"],
    "eu":     ["denmark", "germany", "netherlands", "finland", "norway"],
}

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/123.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

_MAX_PAGES   = 3
_LOC_PRIORITY = {"sweden": 1, "eu": 2}


def _parse_next_data(soup: BeautifulSoup) -> list[dict]:
    """Extract job list from Next.js __NEXT_DATA__ script tag."""
    script = soup.find("script", id="__NEXT_DATA__")
    if not script:
        return []
    try:
        data      = json.loads(script.string)
        page_props = data.get("props", {}).get("pageProps", {})
        # thehub.io may use different keys across versions
        jobs = (
            page_props.get("jobs")
            or page_props.get("jobPostings")
            or page_props.get("jobOffers")
            or []
        )
        return jobs if isinstance(jobs, list) else []
    except Exception:
        return []


def _parse_html_fallback(soup: BeautifulSoup) -> list[dict]:
    """Fallback: scrape job links directly from HTML."""
    rows = []
    for a in soup.select("a[href*='/jobs/']"):
        title_el = a.select_one("h2, h3, [class*='title'], [class*='Title'], [class*='name']")
        if not title_el:
            continue
        title = title_el.get_text(strip=True)
        if not title:
            continue
        href = a.get("href", "")
        if href and not href.startswith("http"):
            href = "https://thehub.io" + href
        rows.append({"title": title, "job_url": href, "company": "", "date_posted": ""})
    return rows


def search_thehub(region: str = "sweden", hours_old: int = HOURS_OLD) -> pd.DataFrame:
    """
    Scrape thehub.io for relevant tech jobs.

    region="sweden" → Sweden only
    region="eu"     → Denmark, Germany, Netherlands, Finland, Norway
    """
    locations   = _LOCATIONS.get(region, _LOCATIONS["sweden"])
    loc_priority = _LOC_PRIORITY.get(region, 2)
    cutoff      = datetime.now(timezone.utc) - timedelta(hours=hours_old)

    base_params = (
        [("roles[]",    r)  for r in _ROLES]
        + [("jobTypes[]", jt) for jt in _JOB_TYPES]
        + [("locations[]", l) for l in locations]
    )

    all_rows = []

    for page in range(1, _MAX_PAGES + 1):
        try:
            resp = requests.get(
                "https://thehub.io/jobs",
                params=base_params + [("page", str(page))],
                headers=_HEADERS,
                timeout=15,
            )
            resp.raise_for_status()
            soup = BeautifulSoup(resp.content, "html.parser")

            jobs = _parse_next_data(soup)

            if jobs:
                page_rows = []
                for job in jobs:
                    title = job.get("title") or job.get("name") or ""
                    if not title or not is_relevant(str(title)):
                        continue

                    company = job.get("company") or {}
                    if isinstance(company, dict):
                        company = company.get("name", "")

                    location = job.get("location") or job.get("city") or ""
                    if isinstance(location, dict):
                        location = location.get("city") or location.get("name") or ""
                    if not location:
                        location = locations[0].title()

                    job_url = job.get("url") or job.get("slug") or ""
                    if job_url and not job_url.startswith("http"):
                        job_url = "https://thehub.io" + job_url

                    date_str = job.get("publishedAt") or job.get("createdAt") or ""
                    if date_str:
                        try:
                            pub = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
                            if pub.tzinfo is None:
                                pub = pub.replace(tzinfo=timezone.utc)
                            if pub < cutoff:
                                continue
                        except Exception:
                            pass

                    page_rows.append({
                        "title":             title,
                        "company":           company,
                        "location":          location,
                        "job_url":           job_url,
                        "date_posted":       date_str[:10] if date_str else "",
                        "site":              "thehub.io",
                        "location_priority": loc_priority,
                        "search_query":      "thehub",
                    })

                if not page_rows:
                    break   # no more results
                all_rows.extend(page_rows)

            else:
                # HTML fallback
                fallback = _parse_html_fallback(soup)
                if not fallback:
                    break
                for item in fallback:
                    if not is_relevant(item["title"]):
                        continue
                    all_rows.append({
                        **item,
                        "site":              "thehub.io",
                        "location_priority": loc_priority,
                        "search_query":      "thehub",
                        "location":          locations[0].title(),
                    })
                break  # HTML fallback doesn't support reliable pagination

        except Exception as e:
            print(f"  [thehub] page {page}: {e}")
            break

    if not all_rows:
        return pd.DataFrame()

    return pd.DataFrame(all_rows).drop_duplicates(subset=["job_url"])
