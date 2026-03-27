"""
Job tracker web UI.
Run:  python web.py
Open: http://localhost:8080
"""

import json

from flask import Flask, jsonify, redirect, render_template, request, url_for
from whowork.db import delete_run, get_all_applied, get_jobs, get_runs, init_db, set_progress, toggle_applied

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


@app.template_filter("job_category")
def job_category_filter(title: str) -> str:
    _, label = _job_category(str(title))
    return label


@app.template_filter("parse_summary")
def parse_summary_filter(val: str) -> dict:
    if not val:
        return {}
    try:
        return json.loads(val)
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
    return render_template("index.html", runs=runs, applied=applied, page="home",
                           selected=None, jobs=[], all_jobs=[], filter_mode="all")


@app.route("/run/<int:run_id>")
def run_view(run_id):
    filter_mode = request.args.get("filter", "all")
    runs     = get_runs()
    all_jobs = _sort_jobs(get_jobs(run_id))
    jobs     = [j for j in all_jobs if j["applied"]] if filter_mode == "applied" else all_jobs
    selected = next((r for r in runs if r["id"] == run_id), None)
    return render_template("index.html", runs=runs, jobs=jobs, all_jobs=all_jobs,
                           selected=selected, filter_mode=filter_mode,
                           has_api_key=True,
                           page="run", applied=[])


@app.route("/delete_run/<int:run_id>", methods=["POST"])
def delete_run_view(run_id):
    delete_run(run_id)
    return redirect(url_for("index"))


@app.route("/enrich/<int:run_id>", methods=["POST"])
def enrich_run_view(run_id):
    from whowork.summarize import enrich_run
    count = enrich_run(run_id)
    return jsonify({"count": count})


@app.route("/toggle/<int:job_id>", methods=["POST"])
def toggle(job_id):
    return jsonify({"applied": toggle_applied(job_id)})


@app.route("/set_progress/<int:job_id>", methods=["POST"])
def update_progress(job_id):
    value = request.json.get("value", "")
    return jsonify({"progress": set_progress(job_id, value)})


if __name__ == "__main__":
    init_db()
    from whowork.config import WEB_PORT, WEB_URL
    print(f"Job tracker running at {WEB_URL}")
    app.run(port=WEB_PORT, debug=False)
