"""
SQLite persistence layer.
Schema:
  runs  — one row per search run (timestamp, region, count)
  jobs  — all jobs, linked to a run, with applied + progress tracking
"""

import sqlite3
from datetime import datetime
from pathlib import Path

import pandas as pd

DB_PATH = Path(__file__).parent.parent / "data" / "jobs.db"


def _conn():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def _safe_str(val) -> str:
    """Convert a value to string, returning '' for NaN/None/NaT."""
    if val is None:
        return ""
    try:
        if pd.isna(val):
            return ""
    except (TypeError, ValueError):
        pass
    return str(val).strip()


def init_db() -> None:
    with _conn() as con:
        con.executescript("""
            CREATE TABLE IF NOT EXISTS runs (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT    NOT NULL,
                region    TEXT    NOT NULL,
                count     INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS jobs (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id      INTEGER NOT NULL REFERENCES runs(id),
                title       TEXT,
                company     TEXT,
                location    TEXT,
                date_posted TEXT,
                deadline    TEXT    NOT NULL DEFAULT '',
                site        TEXT,
                job_url     TEXT,
                applied     INTEGER NOT NULL DEFAULT 0,
                progress    TEXT    NOT NULL DEFAULT ''
            );
        """)
        # Migrations for existing databases
        for sql in [
            "ALTER TABLE jobs ADD COLUMN applied   INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE jobs ADD COLUMN progress  TEXT    NOT NULL DEFAULT ''",
            "ALTER TABLE jobs ADD COLUMN deadline  TEXT    NOT NULL DEFAULT ''",
            "ALTER TABLE jobs ADD COLUMN summary   TEXT    NOT NULL DEFAULT ''",
        ]:
            try:
                con.execute(sql)
            except Exception:
                pass


def save_run(df, region: str) -> int:
    init_db()
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    with _conn() as con:
        cur = con.execute(
            "INSERT INTO runs (timestamp, region, count) VALUES (?, ?, ?)",
            (ts, region, len(df)),
        )
        run_id = cur.lastrowid
        con.executemany(
            """INSERT INTO jobs (run_id, title, company, location, date_posted, deadline, site, job_url)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                (
                    run_id,
                    _safe_str(row.get("title")),
                    _safe_str(row.get("company")),
                    _safe_str(row.get("location")),
                    _safe_str(row.get("date_posted")),
                    _safe_str(row.get("deadline")),
                    _safe_str(row.get("site")),
                    _safe_str(row.get("job_url")),
                )
                for _, row in df.iterrows()
            ],
        )
    return run_id


def get_runs() -> list:
    init_db()
    with _conn() as con:
        return con.execute("SELECT * FROM runs ORDER BY id DESC").fetchall()


def get_jobs(run_id: int) -> list:
    with _conn() as con:
        return con.execute(
            "SELECT * FROM jobs WHERE run_id = ? ORDER BY id", (run_id,)
        ).fetchall()


def get_all_applied() -> list:
    """All applied jobs across every run, newest run first."""
    init_db()
    with _conn() as con:
        return con.execute("""
            SELECT jobs.*, runs.timestamp AS run_timestamp, runs.region AS run_region
            FROM jobs
            JOIN runs ON jobs.run_id = runs.id
            WHERE jobs.applied = 1
            ORDER BY runs.id DESC, jobs.id
        """).fetchall()


def toggle_applied(job_id: int) -> int:
    with _conn() as con:
        con.execute("UPDATE jobs SET applied = 1 - applied WHERE id = ?", (job_id,))
        return con.execute("SELECT applied FROM jobs WHERE id = ?", (job_id,)).fetchone()["applied"]


def delete_run(run_id: int) -> None:
    with _conn() as con:
        con.execute("DELETE FROM jobs WHERE run_id = ?", (run_id,))
        con.execute("DELETE FROM runs WHERE id = ?", (run_id,))


def save_summary(job_id: int, summary_json: str) -> None:
    with _conn() as con:
        con.execute("UPDATE jobs SET summary = ? WHERE id = ?", (summary_json, job_id))


def set_progress(job_id: int, value: str) -> str:
    allowed = {"", "interview", "closed"}
    value = value if value in allowed else ""
    with _conn() as con:
        con.execute("UPDATE jobs SET progress = ? WHERE id = ?", (value, job_id))
    return value
