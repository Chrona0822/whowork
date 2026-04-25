"""
Job tracker web UI.
Run:  python web.py
Open: http://localhost:8080
"""

import json
import re
from datetime import datetime
from difflib import SequenceMatcher

from flask import Flask, jsonify, redirect, render_template, request, url_for
from backend.db import (
    add_manual_job, delete_run, get_all_applied, get_jobs, get_runs, init_db,
    set_progress, toggle_applied, toggle_favorite,
    get_watchlist_jobs, get_watchlist_meta,
)

app = Flask(__name__)

# ── Job category classification ────────────────────────────────────────────────

_CATEGORIES = [
    (1, "ML",         ["machine learning", " ml", "ml ", "deep learning", "artificial intelligence",
                        " ai ", "ai-", "nlp", "llm", "large language", "neural network",
                        "computer vision", "data scientist", "data science"]),
    (2, "Data",       ["data engineer", "data analyst", "data pipeline", "etl", "analytics engineer"]),
    (3, "Full Stack", ["fullstack", "full stack", "full-stack"]),
    (4, "Frontend",   ["frontend", "front end", "front-end", "ui developer", "ui engineer",
                        "react developer", "angular", "vue developer","web developer","web"]),
    (5, "Backend",    ["backend", "back end", "back-end", "api developer", "api engineer",
                        "java developer", "python developer", "node developer", "microservice"]),
    (6, "DevOps",     ["devops", "sre", "site reliability", "platform engineer", "cloud engineer",
                        "infrastructure engineer", "kubernetes", "devsecops"]),
    (7, "QA",         ["qa", "quality assurance", "test automation", "testing", "tester",
                        "quality engineer", "test engineer", "test automation engineer",
                        "verification engineer"]),
    (8, "Dev",        ["software engineer", "software developer", "programmer",
                        "systemutvecklare", "mjukvaruutvecklare", "developer", "engineer"]),
    (9, "Grad",       ["researcher", "research", "forskare", "forskningsassistent",
                        "graduate", "intern", "trainee"]),
]

def _job_category(title: str) -> tuple[int, str]:
    t = title.lower()
    # PhD/doctoral always → Grad regardless of field
    if any(kw in t for kw in ["phd", "doctoral", "doktorand", "doktorander"]):
        return 9, "Grad"
    for pri, label, keywords in _CATEGORIES:
        if any(kw in t for kw in keywords):
            return pri, label
    return 10, "Other"

_CITY_PRIORITY = {
    "stockholm": 1,
    "gothenburg": 2, "göteborg": 2,
    "malmö": 3, "malmo": 3,
    "linköping": 4, "linkoping": 4,
    "lund": 5, "uppsala": 6,
}

def _location_sort_key(location: str) -> tuple:
    loc = str(location).lower()
    if "sweden" in loc or "sverige" in loc:
        for city, order in _CITY_PRIORITY.items():
            if city in loc:
                return (1, order)
        return (1, 99)
    if "denmark" in loc or "copenhagen" in loc:
        return (2, 99)
    if "germany" in loc or "deutschland" in loc:
        return (3, 99)
    return (4, 99)

def _sort_jobs(jobs):
    def key(job):
        cat_pri, _ = _job_category(str(job["title"]))
        return (cat_pri, *_location_sort_key(str(job["location"])))
    return sorted(jobs, key=key)


_COMPANY_NOISE = re.compile(
    r"\b(ab|ltd|inc|gmbh|ag|as|oy|bv|nv|plc|llc|corp|group|se|uk|de)\b"
)

def _norm(s: str) -> str:
    s = str(s).lower()
    s = _COMPANY_NOISE.sub("", s)
    s = re.sub(r"[^\w\s]", "", s)
    return s.strip()

def _sim(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()

def _dedup_jobs(jobs: list) -> list:
    """Collapse duplicates (same role on multiple sites) into one dict.
    The canonical record keeps the best URL and gains an 'extra_sites' list."""
    result: list[dict] = []
    norms: list[tuple[str, str]] = []   # (norm_title, norm_company) for each kept job

    for job in jobs:
        nt = _norm(job["title"])
        nc = _norm(job["company"])
        merged = False
        for i, (kt, kc) in enumerate(norms):
            if _sim(nc, kc) >= 0.88 and _sim(nt, kt) >= 0.90:
                # Duplicate — annotate the canonical record with the extra site
                result[i].setdefault("extra_sites", []).append(job["site"])
                merged = True
                break
        if not merged:
            result.append(dict(job))
            norms.append((nt, nc))

    return result


@app.template_filter("job_category")
def job_category_filter(title: str) -> str:
    _, label = _job_category(str(title))
    return label


@app.template_filter("parse_summary")
def parse_summary_filter(val: str) -> dict:
    if not val:
        return {}
    try:
        data = json.loads(val)
        # Normalise tech_stack — LLM occasionally returns a dict instead of a list
        ts = data.get("tech_stack")
        if isinstance(ts, dict):
            data["tech_stack"] = list(ts.keys())
        elif not isinstance(ts, list):
            data["tech_stack"] = []
        return data
    except Exception:
        return {}


@app.template_filter("short_location")
def short_location(loc: str) -> str:
    """'Stockholm, Stockholm County, Sweden' → 'Stockholm, Sweden'"""
    parts = [p.strip() for p in str(loc).split(",")]
    if len(parts) >= 3:
        return f"{parts[0]}, {parts[-1]}"
    return loc


@app.template_filter("format_date")
def format_date(val: str) -> str:
    """Return '—' for missing/NaN dates, otherwise YYYY-MM-DD."""
    if not val or str(val).lower() in ("nan", "none", "nat", ""):
        return "—"
    return str(val)[:10]


@app.route("/")
def index():
    runs    = get_runs()
    applied = get_all_applied()
    return render_template("home.html", runs=runs, applied=applied, page="home",
                           selected=None)


@app.route("/run/<int:run_id>")
def run_view(run_id):
    filter_mode = request.args.get("filter", "all")
    runs     = get_runs()
    all_jobs = _dedup_jobs(_sort_jobs(get_jobs(run_id)))
    if filter_mode == "applied":
        jobs = [j for j in all_jobs if j["applied"]]
    elif filter_mode == "toapply":
        jobs = [j for j in all_jobs if not j["applied"]]
    else:
        jobs = all_jobs
    selected = next((r for r in runs if r["id"] == run_id), None)
    # Patch sidebar counts to reflect dedup (sqlite3.Row is read-only, convert to dict)
    dedup_count = len(all_jobs)
    runs = [dict(r, count=dedup_count) if r["id"] == run_id else dict(r) for r in runs]
    selected = next((r for r in runs if r["id"] == run_id), None)
    return render_template("run.html", runs=runs, jobs=jobs, all_jobs=all_jobs,
                           selected=selected, filter_mode=filter_mode,
                           has_api_key=True, page="run")


@app.route("/delete_run/<int:run_id>", methods=["POST"])
def delete_run_view(run_id):
    delete_run(run_id)
    return redirect(url_for("index"))


@app.route("/enrich/<int:run_id>", methods=["POST"])
def enrich_run_view(run_id):
    from backend.summarize import enrich_run
    force = request.args.get("force", "0") == "1"
    count = enrich_run(run_id, force=force)
    return jsonify({"count": count})


@app.route("/toggle/<int:job_id>", methods=["POST"])
def toggle(job_id):
    return jsonify({"applied": toggle_applied(job_id)})


@app.route("/toggle_favorite/<int:job_id>", methods=["POST"])
def toggle_fav(job_id):
    return jsonify({"favorite": toggle_favorite(job_id)})


@app.route("/set_progress/<int:job_id>", methods=["POST"])
def update_progress(job_id):
    value = request.json.get("value", "")
    return jsonify({"progress": set_progress(job_id, value)})


@app.route("/add_manual", methods=["POST"])
def add_manual():
    data = request.json
    job_id = add_manual_job(
        title=data.get("title", ""),
        company=data.get("company", ""),
        location=data.get("location", ""),
        job_url=data.get("job_url", ""),
    )
    return jsonify({"id": job_id})


@app.route("/favourite")
def favourite():
    from backend.sources.watchlist import GAME_COMPANIES, FINANCE_COMPANIES, _career_url
    today = datetime.now().strftime("%Y-%m-%d")

    def _company_data(company: dict) -> dict:
        raw   = get_watchlist_jobs(company["key"])
        jobs  = [dict(j) for j in raw]
        new_jobs    = [j for j in jobs if j["first_seen"] == today or j["post_date"] == today]
        recent_jobs = jobs[:3]
        return {
            **company,
            "career_url":   _career_url(company),
            "new_jobs":     new_jobs,
            "recent_jobs":  recent_jobs,
            "total":        len(jobs),
            "has_new":      len(new_jobs) > 0,
            "last_checked": get_watchlist_meta(company["key"]),
        }

    game_data    = [_company_data(c) for c in GAME_COMPANIES]
    finance_data = [_company_data(c) for c in FINANCE_COMPANIES]
    runs = get_runs()
    return render_template(
        "favourite.html",
        game_companies=game_data,
        finance_companies=finance_data,
        today=today,
        runs=runs,
        selected=None,
        page="favourite",
    )


@app.route("/watchlist/refresh", methods=["POST"])
def watchlist_refresh():
    from backend.sources.watchlist import scan_all_watchlist
    count = scan_all_watchlist()
    return jsonify({"count": count})


if __name__ == "__main__":
    init_db()
    from backend.config import WEB_PORT, WEB_URL
    print(f"Job tracker running at {WEB_URL}")
    app.run(port=WEB_PORT, debug=False)
