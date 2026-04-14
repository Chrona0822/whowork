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

from backend.db import get_jobs, save_summary

OLLAMA_URL      = "http://localhost:11434/api/generate"
OLLAMA_MODEL    = "llama3.2:1b"
_FETCH_WORKERS  = 20   # parallel page fetches (network I/O)
_OLLAMA_WORKERS = 2    # Ollama queues requests anyway; 2 is enough
_DESC_CHARS     = 4000
_REQ_CHARS      = 1500  # chars sent to Ollama (requirements section only)

# Section headings that typically precede requirements / skills
_REQ_HEADINGS = [
    "requirements", "qualifications", "what you need", "what we're looking for",
    "what we look for", "must have", "skills", "you have", "you bring",
    "you are", "your background", "minimum qualifications", "basic qualifications",
    "required skills", "we expect", "we require",
]

# CSS selectors that contain the job description on common sites
_DESC_SELECTORS = [
    # LinkedIn
    "div.description__text",
    "div.show-more-less-html__markup",
    # Indeed
    "div#jobDescriptionText",
    # Glassdoor
    "div.jobDescriptionContent",
    # Generic fallback
    "div[class*='description']",
    "section[class*='description']",
]


def _extract_requirements(text: str) -> str:
    """Return the requirements/qualifications section of a job description.
    Falls back to the full text if no known heading is found."""
    lower = text.lower()
    best_idx = len(text)
    for heading in _REQ_HEADINGS:
        idx = lower.find(heading)
        if 0 < idx < best_idx:
            best_idx = idx
    if best_idx < len(text):
        return text[best_idx:best_idx + _REQ_CHARS]
    return text[:_REQ_CHARS]


def _fetch_description(url: str) -> str:
    """Fetch job description text from a job detail page.
    Tries targeted CSS selectors first to skip login walls / cookie banners,
    then falls back to full visible page text."""
    try:
        r = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        soup = BeautifulSoup(r.content, "html.parser")

        # Try to extract the description section directly
        for selector in _DESC_SELECTORS:
            el = soup.select_one(selector)
            if el:
                text = el.get_text(" ", strip=True)
                if len(text) > 100:   # ignore tiny/empty matches
                    return text[:_DESC_CHARS]

        # Fallback: strip boilerplate and return all visible text
        for tag in soup(["script", "style", "nav", "header", "footer"]):
            tag.decompose()
        return soup.get_text(" ", strip=True)[:_DESC_CHARS]
    except Exception:
        return ""


# Common Swedish words unlikely to appear in English text
_SWEDISH_MARKERS = {
    "och", "att", "för", "som", "med", "på", "är", "vi", "du", "ska",
    "söker", "erfarenhet", "arbete", "tjänst", "hos", "vill", "eller",
    "inom", "våra", "har", "dig", "det", "din", "dina", "vara", "bli",
}

def _is_swedish(text: str) -> bool:
    words = set(text.lower().split())
    hits = words & _SWEDISH_MARKERS
    return len(hits) >= 4   # 4+ Swedish marker words → likely Swedish


_PROMPT_TEMPLATE = """\
{lang_instruction}Extract key info from this job posting and return ONLY a JSON object with these fields:
- "years_exp": required experience as a short string (e.g. "2+ years", "No experience", "PhD required", "N/A")
- "tech_stack": array of up to 5 key technologies or skills
- "level": one of "Junior", "Mid", "Senior", "PhD/Research", "Any"

Job title: {title}

{desc}

Return only the JSON object, nothing else."""

_LANG_INSTRUCTION_SV = "The following job description is in Swedish. Translate it to English, then extract the info.\n\n"


def _ollama_one(job_id: int, title: str, desc: str) -> tuple[int, dict]:
    """Call Ollama for one job given a pre-fetched description."""
    snippet = _extract_requirements(desc)
    swedish = _is_swedish(snippet)
    lang_instruction = _LANG_INSTRUCTION_SV if swedish else ""
    try:
        resp = requests.post(
            OLLAMA_URL,
            json={
                "model":  OLLAMA_MODEL,
                "prompt": _PROMPT_TEMPLATE.format(title=title, desc=snippet, lang_instruction=lang_instruction),
                "stream": False,
                "format": "json",
                "keep_alive": "10m",
                "options": {
                    "num_predict": 150,
                    "num_ctx":     1024,
                },
            },
            timeout=30,
        )
        resp.raise_for_status()
        text = resp.json().get("response", "").strip()
        data = json.loads(text)
        if swedish:
            data["lang"] = "sv"
        return job_id, data
    except requests.ConnectionError:
        print("  [summarize] Ollama not running — start with: ollama serve")
        return job_id, {}
    except Exception as e:
        print(f"  [summarize] job {job_id}: {e}")
        return job_id, {}


def _ollama_reachable() -> bool:
    try:
        requests.get("http://localhost:11434/api/tags", timeout=3)
        return True
    except Exception:
        return False


def enrich_run(run_id: int) -> int:
    """Summarize all un-summarized jobs in a run.
    Returns count saved, 0 if nothing to enrich, or -1 if Ollama is unreachable."""
    jobs = [j for j in get_jobs(run_id) if j["job_url"] and not j["summary"]]
    if not jobs:
        return 0

    if not _ollama_reachable():
        print("  [summarize] Ollama not running — start with: ollama serve")
        return -1

    # Phase 1: use stored description if available, otherwise fetch the page
    needs_fetch = [j for j in jobs if not j["description"]]
    has_desc    = [j for j in jobs if j["description"]]

    fetched = [(j["id"], j["title"], j["description"]) for j in has_desc]

    if needs_fetch:
        with ThreadPoolExecutor(max_workers=_FETCH_WORKERS) as pool:
            fetch_futures = {
                pool.submit(_fetch_description, j["job_url"]): j
                for j in needs_fetch
            }
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
