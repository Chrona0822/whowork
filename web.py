"""
Job tracker web UI.
Run:  python web.py
Open: http://localhost:5000
"""

from flask import Flask, jsonify, redirect, render_template, request, url_for
from db import get_jobs, get_runs, init_db, toggle_applied

app = Flask(__name__)


@app.route("/")
def index():
    runs = get_runs()
    if not runs:
        return render_template("index.html", runs=[], jobs=[], selected=None)
    return redirect(url_for("run_view", run_id=runs[0]["id"]))


@app.route("/run/<int:run_id>")
def run_view(run_id):
    runs = get_runs()
    jobs = get_jobs(run_id)
    selected = next((r for r in runs if r["id"] == run_id), None)
    return render_template("index.html", runs=runs, jobs=jobs, selected=selected)


@app.route("/toggle/<int:job_id>", methods=["POST"])
def toggle(job_id):
    new_status = toggle_applied(job_id)
    return jsonify({"applied": new_status})


if __name__ == "__main__":
    init_db()
    from config import WEB_PORT, WEB_URL
    print(f"Job tracker running at {WEB_URL}")
    app.run(port=WEB_PORT, debug=False)
