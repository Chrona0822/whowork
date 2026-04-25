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

DB_PATH = Path(__file__).parent / "data" / "jobs.db"


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
            CREATE TABLE IF NOT EXISTS watchlist_jobs (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                company_key TEXT    NOT NULL,
                title       TEXT    NOT NULL,
                job_url     TEXT    NOT NULL,
                post_date   TEXT    NOT NULL DEFAULT '',
                first_seen  TEXT    NOT NULL,
                last_seen   TEXT    NOT NULL,
                UNIQUE(job_url)
            );
            CREATE TABLE IF NOT EXISTS watchlist_meta (
                company_key  TEXT PRIMARY KEY,
                last_checked TEXT NOT NULL DEFAULT ''
            );
        """)
        # Migrations for existing databases
        for sql in [
            "ALTER TABLE jobs ADD COLUMN applied      INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE jobs ADD COLUMN progress     TEXT    NOT NULL DEFAULT ''",
            "ALTER TABLE jobs ADD COLUMN deadline     TEXT    NOT NULL DEFAULT ''",
            "ALTER TABLE jobs ADD COLUMN summary      TEXT    NOT NULL DEFAULT ''",
            "ALTER TABLE jobs ADD COLUMN description  TEXT    NOT NULL DEFAULT ''",
            "ALTER TABLE jobs ADD COLUMN favorite     INTEGER NOT NULL DEFAULT 0",
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
            """INSERT INTO jobs (run_id, title, company, location, date_posted, deadline, site, job_url, description)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
                    _safe_str(row.get("description")),
                )
                for _, row in df.iterrows()
            ],
        )
    return run_id


def get_runs() -> list:
    init_db()
    with _conn() as con:
        return con.execute("SELECT * FROM runs WHERE region != 'manual' ORDER BY id DESC").fetchall()


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


def toggle_favorite(job_id: int) -> int:
    with _conn() as con:
        con.execute("UPDATE jobs SET favorite = 1 - favorite WHERE id = ?", (job_id,))
        return con.execute("SELECT favorite FROM jobs WHERE id = ?", (job_id,)).fetchone()["favorite"]


def delete_run(run_id: int) -> None:
    with _conn() as con:
        con.execute("DELETE FROM jobs WHERE run_id = ?", (run_id,))
        con.execute("DELETE FROM runs WHERE id = ?", (run_id,))


def save_summary(job_id: int, summary_json: str) -> None:
    with _conn() as con:
        con.execute(
            "UPDATE jobs SET summary = ?, description = '' WHERE id = ?",
            (summary_json, job_id),
        )


def clear_descriptions(job_ids: list[int]) -> None:
    with _conn() as con:
        con.executemany(
            "UPDATE jobs SET description = '' WHERE id = ?",
            [(jid,) for jid in job_ids],
        )


def clear_summaries(run_id: int) -> None:
    """Clear all summaries for a run so they can be re-enriched."""
    with _conn() as con:
        con.execute("UPDATE jobs SET summary = '' WHERE run_id = ?", (run_id,))


def set_progress(job_id: int, value: str) -> str:
    allowed = {"", "hr_call", "interview", "oa_done", "closed"}
    value = value if value in allowed else ""
    with _conn() as con:
        con.execute("UPDATE jobs SET progress = ? WHERE id = ?", (value, job_id))
    return value


def _get_or_create_manual_run() -> int:
    """Return the id of the single 'manual' run, creating it if needed."""
    with _conn() as con:
        row = con.execute("SELECT id FROM runs WHERE region = 'manual' LIMIT 1").fetchone()
        if row:
            return row["id"]
        cur = con.execute(
            "INSERT INTO runs (timestamp, region, count) VALUES (?, 'manual', 0)",
            (datetime.now().strftime("%Y-%m-%d %H:%M"),),
        )
        return cur.lastrowid


def add_manual_job(title: str, company: str, location: str, job_url: str) -> int:
    """Insert a manually-tracked application. Returns the new job id."""
    init_db()
    run_id = _get_or_create_manual_run()
    today  = datetime.now().strftime("%Y-%m-%d")
    with _conn() as con:
        cur = con.execute(
            """INSERT INTO jobs (run_id, title, company, location, date_posted, site, job_url, applied)
               VALUES (?, ?, ?, ?, ?, 'manual', ?, 1)""",
            (run_id, title.strip(), company.strip(), location.strip(), today, job_url.strip()),
        )
        job_id = cur.lastrowid
        # keep run.count in sync
        con.execute("UPDATE runs SET count = count + 1 WHERE id = ?", (run_id,))
    return job_id


def get_watchlist_jobs(company_key: str) -> list:
    """Return all jobs for a company, newest first_seen first."""
    init_db()
    with _conn() as con:
        return con.execute(
            "SELECT * FROM watchlist_jobs WHERE company_key = ? ORDER BY first_seen DESC, id DESC",
            (company_key,),
        ).fetchall()


def upsert_watchlist_jobs(company_key: str, jobs: list[dict]) -> None:
    """Insert new jobs and update last_seen for existing ones. Prune jobs unseen for 14+ days."""
    if not jobs:
        return
    today = datetime.now().strftime("%Y-%m-%d")
    init_db()
    with _conn() as con:
        for j in jobs:
            con.execute(
                """
                INSERT INTO watchlist_jobs (company_key, title, job_url, post_date, first_seen, last_seen)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(job_url) DO UPDATE SET
                    last_seen = excluded.last_seen,
                    title     = CASE WHEN excluded.title != '' THEN excluded.title ELSE title END,
                    post_date = CASE WHEN excluded.post_date != '' THEN excluded.post_date ELSE post_date END
                """,
                (company_key, j.get("title", ""), j.get("url", ""), j.get("date", ""), today, today),
            )
        con.execute(
            "DELETE FROM watchlist_jobs WHERE company_key = ? AND last_seen < date('now', '-14 days')",
            (company_key,),
        )


def get_watchlist_meta(company_key: str) -> str | None:
    """Return last_checked timestamp string, or None if never scanned."""
    init_db()
    with _conn() as con:
        row = con.execute(
            "SELECT last_checked FROM watchlist_meta WHERE company_key = ?",
            (company_key,),
        ).fetchone()
        return row["last_checked"] if row else None


def set_watchlist_meta(company_key: str, ts: str) -> None:
    init_db()
    with _conn() as con:
        con.execute(
            """
            INSERT INTO watchlist_meta (company_key, last_checked) VALUES (?, ?)
            ON CONFLICT(company_key) DO UPDATE SET last_checked = excluded.last_checked
            """,
            (company_key, ts),
        )
