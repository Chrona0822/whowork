"""
Watchlist: monitors career pages of curated game studios and fintech companies
in Sweden / Denmark. Scraped jobs are cached in the DB (refreshed on demand).
"""

import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from backend.db import get_watchlist_jobs, get_watchlist_meta, set_watchlist_meta, upsert_watchlist_jobs

_TIMEOUT = 8
_WORKERS = 14
_UA = {"User-Agent": "Mozilla/5.0"}

# ── Tech-role keyword filter ──────────────────────────────────────────────────
_TECH_KW = [
    "engineer", "developer", "programmer", "software", "backend", "frontend",
    "full stack", "fullstack", "devops", "sre", "data ", " data", "machine learning",
    " ml", "ml ", "qa ", " qa", "quality", "security", "cloud", "platform",
    "architect", "tech lead", "graphics", "gameplay", "server",
    "infrastructure", "technical", "rendering", "tools",
]

def _is_tech(title: str) -> bool:
    t = title.lower()
    return any(kw in t for kw in _TECH_KW)


# ── Company registry ──────────────────────────────────────────────────────────

GAME_COMPANIES = [
    # Sweden
    {"key": "paradox",    "name": "Paradox Interactive",    "loc": "Stockholm",  "type": "html",     "url": "https://www.paradoxinteractive.com/careers"},
    {"key": "king",       "name": "King",                   "loc": "Stockholm",  "type": "html",     "url": "https://careers.king.com/us/en"},
    {"key": "embark",     "name": "Embark Studios",         "loc": "Stockholm",  "type": "html",     "url": "https://careers.embark-studios.com/jobs"},
    {"key": "avalanche",  "name": "Avalanche Studios",      "loc": "Stockholm",  "type": "lever",    "slug": "avalanchestudios"},
    {"key": "fatshark",   "name": "Fatshark",               "loc": "Stockholm",  "type": "html",     "url": "https://jobs.fatsharkgames.com/jobs"},
    {"key": "arrowhead",  "name": "Arrowhead Game Studios", "loc": "Stockholm",  "type": "html",     "url": "https://jobs.arrowheadgamestudios.com/"},
    {"key": "resolution", "name": "Resolution Games",       "loc": "Stockholm",  "type": "html",     "url": "https://jobs.resolutiongames.com/"},
    {"key": "sharkmob",   "name": "Sharkmob",               "loc": "Malmö",      "type": "html",     "url": "https://career.sharkmob.com/jobs"},
    {"key": "tarsier",    "name": "Tarsier Studios",        "loc": "Malmö",      "type": "html",     "url": "https://www.tarsierstudios.com/join-us/"},
    {"key": "thunderful", "name": "Thunderful",             "loc": "Gothenburg", "type": "html",     "url": "https://www.thunderful.com/career/"},
    {"key": "coffeestain","name": "Coffee Stain Studios",   "loc": "Skövde",     "type": "html",     "url": "https://www.coffeestain.com/careers/"},
    {"key": "stunlock",   "name": "Stunlock Studios",       "loc": "Borås",      "type": "html",     "url": "https://jobs.stunlockstudios.com/jobs"},
    # Denmark
    {"key": "ioi",        "name": "IO Interactive",         "loc": "Copenhagen", "type": "html",     "url": "https://apply.ioi.dk/jobs"},
    {"key": "playdead",   "name": "Playdead",               "loc": "Copenhagen", "type": "html",     "url": "https://playdead.com/jobs/"},
    {"key": "sybo",       "name": "SYBO Games",             "loc": "Copenhagen", "type": "html",     "url": "https://sybogames.com/careers/"},
    {"key": "ghostship",  "name": "Ghost Ship Games",       "loc": "Copenhagen", "type": "html",     "url": "https://jobs.ghostship.dk/jobs"},
    # Belgium
    {"key": "larian",     "name": "Larian Studios",         "loc": "Ghent",      "type": "lever",    "slug": "larian"},
]

FINANCE_COMPANIES = [
    # Sweden
    {"key": "klarna",        "name": "Klarna",          "loc": "Stockholm", "type": "html",     "url": "https://www.klarna.com/careers/"},
    {"key": "trustly",       "name": "Trustly",         "loc": "Stockholm", "type": "lever",    "slug": "trustly"},
    {"key": "anyfin",        "name": "Anyfin",          "loc": "Stockholm", "type": "html",     "url": "https://career.anyfin.com/"},
    {"key": "seb",           "name": "SEB",             "loc": "Stockholm", "type": "lever_eu", "slug": "seb"},
    {"key": "nordea",        "name": "Nordea",          "loc": "Stockholm", "type": "html",     "url": "https://www.nordea.com/en/careers/open-jobs"},
    {"key": "swedbank",      "name": "Swedbank",        "loc": "Stockholm", "type": "html",     "url": "https://jobs.swedbank.com/"},
    {"key": "handelsbanken", "name": "Handelsbanken",   "loc": "Stockholm", "type": "html",     "url": "https://www.handelsbanken.com/en/careers/vacancies"},
    {"key": "revolut",       "name": "Revolut",         "loc": "Stockholm", "type": "html",     "url": "https://www.revolut.com/en-SE/careers/"},
    # Denmark
    {"key": "pleo",          "name": "Pleo",            "loc": "Copenhagen","type": "greenhouse","slug": "pleo"},
    {"key": "saxo",          "name": "Saxo Bank",       "loc": "Copenhagen","type": "html",     "url": "https://www.home.saxo/about-us/careers"},
    {"key": "simcorp",       "name": "SimCorp",         "loc": "Copenhagen","type": "html",     "url": "https://simcorp.wd3.myworkdayjobs.com/SimCorp_Jobs"},
    {"key": "lunar",         "name": "Lunar",           "loc": "Copenhagen","type": "html",     "url": "https://jobs.lunar.app/"},
    {"key": "nets",          "name": "Nets",            "loc": "Copenhagen","type": "html",     "url": "https://www.nets.eu/careers"},
    # Belgium
    {"key": "kbc",           "name": "KBC Group",       "loc": "Brussels",  "type": "html",     "url": "https://careers.kbc-group.com/"},
    {"key": "worldline",     "name": "Worldline",       "loc": "Brussels",  "type": "html",     "url": "https://careers.worldline.com/en"},
    {"key": "swift",         "name": "Swift",           "loc": "Brussels",  "type": "html",     "url": "https://www.swift.com/about-us/careers"},
]

ALL_COMPANIES = {c["key"]: c for c in GAME_COMPANIES + FINANCE_COMPANIES}


# ── Fetchers ──────────────────────────────────────────────────────────────────

def _fetch_greenhouse(slug: str) -> list[dict]:
    try:
        r = requests.get(
            f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs",
            timeout=_TIMEOUT, headers=_UA,
        )
        r.raise_for_status()
        return [
            {
                "title": j.get("title", ""),
                "url":   j.get("absolute_url", ""),
                "date":  (j.get("updated_at") or "")[:10],
            }
            for j in r.json().get("jobs", [])
            if j.get("title") and j.get("absolute_url")
        ]
    except Exception:
        return []


def _fetch_lever_eu(slug: str) -> list[dict]:
    try:
        r = requests.get(
            f"https://api.eu.lever.co/v0/postings/{slug}?mode=json&limit=200",
            timeout=_TIMEOUT, headers=_UA,
        )
        r.raise_for_status()
        jobs = []
        for j in r.json():
            ts = j.get("createdAt", 0)
            date = (
                datetime.fromtimestamp(ts / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
                if ts else ""
            )
            if j.get("text") and j.get("hostedUrl"):
                jobs.append({"title": j["text"], "url": j["hostedUrl"], "date": date})
        return jobs
    except Exception:
        return []


def _fetch_lever(slug: str) -> list[dict]:
    try:
        r = requests.get(
            f"https://api.lever.co/v0/postings/{slug}?mode=json&limit=200",
            timeout=_TIMEOUT, headers=_UA,
        )
        r.raise_for_status()
        jobs = []
        for j in r.json():
            ts = j.get("createdAt", 0)
            date = (
                datetime.fromtimestamp(ts / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
                if ts else ""
            )
            if j.get("text") and j.get("hostedUrl"):
                jobs.append({"title": j["text"], "url": j["hostedUrl"], "date": date})
        return jobs
    except Exception:
        return []


_JOB_HREF_RE = re.compile(
    r"/(job|jobs|career|careers|opening|openings|position|positions|vacancy|vacancies|role|roles|opportunities)/",
    re.IGNORECASE,
)
_GENERIC_TITLES = {
    "view all", "apply now", "read more", "see all", "all jobs",
    "all positions", "back to", "learn more", "show all", "open positions",
    "see open", "job opportunities", "current openings",
}

def _fetch_html(page_url: str) -> list[dict]:
    try:
        r = requests.get(page_url, timeout=_TIMEOUT, headers=_UA, verify=False)
        r.raise_for_status()
        soup = BeautifulSoup(r.content, "html.parser")
        jobs, seen = [], set()
        for a in soup.find_all("a", href=True):
            title = a.get_text(" ", strip=True)
            href  = a["href"]
            if not title or not (8 <= len(title) <= 120):
                continue
            tl = title.lower()
            if any(phrase in tl for phrase in _GENERIC_TITLES):
                continue
            if not _JOB_HREF_RE.search(href):
                continue
            if not href.startswith("http"):
                href = urljoin(page_url, href)
            if href in seen:
                continue
            seen.add(href)
            jobs.append({"title": title, "url": href, "date": ""})
        return jobs
    except Exception:
        return []


def _raw_jobs(company: dict) -> list[dict]:
    t = company["type"]
    if t == "greenhouse":
        return _fetch_greenhouse(company["slug"])
    if t == "lever":
        return _fetch_lever(company["slug"])
    if t == "lever_eu":
        return _fetch_lever_eu(company["slug"])
    return _fetch_html(company["url"])


def _career_url(company: dict) -> str:
    """Return the human-facing career page URL for a company."""
    if company["type"] == "greenhouse":
        return f"https://boards.greenhouse.io/{company['slug']}"
    if company["type"] == "lever":
        return f"https://jobs.lever.co/{company['slug']}"
    if company["type"] == "lever_eu":
        return f"https://jobs.eu.lever.co/{company['slug']}"
    return company.get("url", "")


# ── Scan ─────────────────────────────────────────────────────────────────────

def scan_company(company: dict, tech_only: bool = True) -> list[dict]:
    jobs = _raw_jobs(company)
    if tech_only:
        jobs = [j for j in jobs if _is_tech(j["title"])]
    return jobs


def scan_all_watchlist() -> int:
    """Scan all watchlist companies in parallel and persist to DB. Returns count updated."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    tasks = (
        [(c, True) for c in GAME_COMPANIES] +
        [(c, True) for c in FINANCE_COMPANIES]
    )

    def _scan_one(company: dict, tech_only: bool) -> str:
        jobs = scan_company(company, tech_only=tech_only)
        upsert_watchlist_jobs(company["key"], jobs)
        set_watchlist_meta(company["key"], now)
        return company["key"]

    updated = 0
    with ThreadPoolExecutor(max_workers=_WORKERS) as pool:
        futures = {pool.submit(_scan_one, c, t): c["key"] for c, t in tasks}
        for f in as_completed(futures):
            try:
                f.result()
                updated += 1
            except Exception as e:
                print(f"  [watchlist/{futures[f]}] {e}")
    return updated
