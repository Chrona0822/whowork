"""
Job description summarization using Ollama (local LLM).
Fetches each job's detail page and asks llama3.2:1b to extract:
  - years_exp   : e.g. "2+ years", "No experience required", "PhD"
  - tech_stack  : up to 5 key technologies / skills
  - level       : "Junior" | "Mid" | "Senior" | "PhD/Research" | "Any"

Requires Ollama running locally:
  brew install ollama
  ollama pull llama3.2:1b
  ollama serve
"""

import json
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from bs4 import BeautifulSoup

from whowork.db import get_jobs, save_summary

OLLAMA_URL      = "http://localhost:11434/api/generate"
OLLAMA_MODEL    = "llama3.2:1b"
_FETCH_WORKERS  = 20   # parallel page fetches (network I/O)
_OLLAMA_WORKERS = 4    # parallel Ollama calls (CPU-bound on local machine)
_DESC_CHARS     = 2000


def _fetch_description(url: str) -> str:
    """Fetch visible text from a job detail page."""
    try:
        r = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        soup = BeautifulSoup(r.content, "html.parser")
        for tag in soup(["script", "style", "nav", "header", "footer"]):
            tag.decompose()
        return soup.get_text(" ", strip=True)[:_DESC_CHARS]
    except Exception:
        return ""


_PROMPT_TEMPLATE = """\
Extract key info from this job posting and return ONLY a JSON object with these fields:
- "years_exp": required experience as a short string (e.g. "2+ years", "No experience", "PhD required", "N/A")
- "tech_stack": array of up to 5 key technologies or skills
- "level": one of "Junior", "Mid", "Senior", "PhD/Research", "Any"

Job title: {title}

{desc}

Return only the JSON object, nothing else."""


def _ollama_one(job_id: int, title: str, desc: str) -> tuple[int, dict]:
    """Call Ollama for one job given a pre-fetched description."""
    try:
        resp = requests.post(
            OLLAMA_URL,
            json={
                "model":  OLLAMA_MODEL,
                "prompt": _PROMPT_TEMPLATE.format(title=title, desc=desc),
                "stream": False,
                "format": "json",
            },
            timeout=60,
        )
        resp.raise_for_status()
        text = resp.json().get("response", "").strip()
        return job_id, json.loads(text)
    except requests.ConnectionError:
        print("  [summarize] Ollama not running — start with: ollama serve")
        return job_id, {}
    except Exception as e:
        print(f"  [summarize] job {job_id}: {e}")
        return job_id, {}


def enrich_run(run_id: int) -> int:
    """Summarize all un-summarized jobs in a run. Returns count saved."""
    jobs = [j for j in get_jobs(run_id) if j["job_url"] and not j["summary"]]
    if not jobs:
        return 0

    # Phase 1: fetch all pages in parallel (network I/O — high concurrency)
    with ThreadPoolExecutor(max_workers=_FETCH_WORKERS) as pool:
        fetch_futures = {
            pool.submit(_fetch_description, j["job_url"]): j
            for j in jobs
        }
        fetched = []  # list of (job_id, title, desc)
        for future in as_completed(fetch_futures):
            j = fetch_futures[future]
            desc = future.result()
            if desc:
                fetched.append((j["id"], j["title"], desc))

    # Phase 2: send to Ollama in parallel
    saved = 0
    with ThreadPoolExecutor(max_workers=_OLLAMA_WORKERS) as pool:
        ollama_futures = {
            pool.submit(_ollama_one, job_id, title, desc): job_id
            for job_id, title, desc in fetched
        }
        for future in as_completed(ollama_futures):
            job_id, data = future.result()
            if data:
                save_summary(job_id, json.dumps(data))
                saved += 1

    return saved
