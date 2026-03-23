"""
Job tracker web UI.
Run:  python web.py
Open: http://localhost:8080
"""

from flask import Flask, jsonify, redirect, render_template, request, url_for
from db import get_all_applied, get_jobs, get_runs, init_db, set_progress, toggle_applied

app = Flask(__name__)


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
    all_jobs = get_jobs(run_id)
    jobs     = [j for j in all_jobs if j["applied"]] if filter_mode == "applied" else all_jobs
    selected = next((r for r in runs if r["id"] == run_id), None)
    return render_template("index.html", runs=runs, jobs=jobs, all_jobs=all_jobs,
                           selected=selected, filter_mode=filter_mode,
                           page="run", applied=[])


@app.route("/toggle/<int:job_id>", methods=["POST"])
def toggle(job_id):
    return jsonify({"applied": toggle_applied(job_id)})


@app.route("/set_progress/<int:job_id>", methods=["POST"])
def update_progress(job_id):
    value = request.json.get("value", "")
    return jsonify({"progress": set_progress(job_id, value)})


if __name__ == "__main__":
    init_db()
    from config import WEB_PORT, WEB_URL
    print(f"Job tracker running at {WEB_URL}")
    app.run(port=WEB_PORT, debug=False)
