"""
SQLite persistence layer.
Schema:
  runs  — one row per search run (timestamp, region, count)
  jobs  — all jobs, linked to a run, with applied toggle
"""

import sqlite3
from datetime import datetime
from pathlib import Path

DB_PATH = Path("jobs.db")


def _conn():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


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
                site        TEXT,
                job_url     TEXT,
                applied     INTEGER NOT NULL DEFAULT 0
            );
        """)


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
            """INSERT INTO jobs (run_id, title, company, location, date_posted, site, job_url)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            [
                (
                    run_id,
                    str(row.get("title",       "")),
                    str(row.get("company",     "")),
                    str(row.get("location",    "")),
                    str(row.get("date_posted", "")),
                    str(row.get("site",        "")),
                    str(row.get("job_url",     "")),
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


def toggle_applied(job_id: int) -> int:
    with _conn() as con:
        con.execute("UPDATE jobs SET applied = 1 - applied WHERE id = ?", (job_id,))
        row = con.execute("SELECT applied FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return row["applied"]
