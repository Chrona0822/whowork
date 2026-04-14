"""
Health checks for dependent services.
Called at the start of every job run to surface problems early.
"""

import sqlite3
import requests
from pathlib import Path

from backend.config import WEB_PORT

OLLAMA_URL = "http://localhost:11434/api/tags"
WEB_URL    = f"http://localhost:{WEB_PORT}"
DB_PATH    = Path(__file__).parent / "data" / "jobs.db"


def _check_ollama() -> tuple[bool, str]:
    try:
        requests.get(OLLAMA_URL, timeout=3)
        return True, "ok"
    except Exception:
        return False, "not running — start Ollama app or run: ollama serve"


def _check_web() -> tuple[bool, str]:
    try:
        requests.get(WEB_URL, timeout=3)
        return True, "ok"
    except Exception:
        return False, f"not reachable at {WEB_URL} — run: python web.py"


def _check_db() -> tuple[bool, str]:
    try:
        DB_PATH.parent.mkdir(exist_ok=True)
        con = sqlite3.connect(DB_PATH)
        con.execute("SELECT 1")
        con.close()
        return True, "ok"
    except Exception as e:
        return False, str(e)


CHECKS = [
    ("Ollama",   _check_ollama),
    ("Web UI",   _check_web),
    ("Database", _check_db),
]


def run_checks(warn_only: bool = False) -> dict[str, tuple[bool, str]]:
    """
    Run all health checks and return results.
    If warn_only=False, prints a status table to stdout.
    Returns {name: (ok, message)}.
    """
    results = {}
    for name, fn in CHECKS:
        ok, msg = fn()
        results[name] = (ok, msg)
        if not warn_only:
            status = "✓" if ok else "✗"
            print(f"  [{status}] {name}: {msg}")
    return results


def print_banner(results: dict[str, tuple[bool, str]]) -> None:
    failures = [name for name, (ok, _) in results.items() if not ok]
    if failures:
        print(f"\n  ⚠ Warning: {', '.join(failures)} {'is' if len(failures) == 1 else 'are'} unavailable.")
        print("  Job search will continue but some features may not work.\n")
    else:
        print("  All services healthy.\n")
