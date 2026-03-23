# ── Search queries ────────────────────────────────────────────────────────────
# JobSpy queries (LinkedIn + Indeed)
JOBSPY_QUERIES = [
    "graduate software engineer",
    "junior software developer",
    "QA engineer",
    "Test engineer",
    "Devops engineer",
    "junior machine learning engineer",
    "data scientist",
    "fintech developer",
    "graduate program software",
    "AI infrastructure",
    "graduate program",
    "front-end engineer"
]

# Academic queries (Euraxess + jobs.ac.uk)
ACADEMIC_QUERIES = [
    "PhD computer science",
    "PhD machine learning",
    "PhD finance computer science",
    "research assistant machine learning",
    "research assistant software",
    "research assistant"
]

# ── Locations (priority 1 = top) ───────────────────────────────────────────────
LOCATIONS = [
    # Sweden
    {"city": "Stockholm",  "country": "Sweden",      "indeed_country": "Sweden",      "priority": 1},
    {"city": "Gothenburg", "country": "Sweden",      "indeed_country": "Sweden",      "priority": 1},
    {"city": "Malmö",      "country": "Sweden",      "indeed_country": "Sweden",      "priority": 1},
    # Denmark
    {"city": "Copenhagen", "country": "Denmark",     "indeed_country": "Denmark",     "priority": 2},
    # Germany
    {"city": "Berlin",     "country": "Germany",     "indeed_country": "Germany",     "priority": 3},
    {"city": "Munich",     "country": "Germany",     "indeed_country": "Germany",     "priority": 3},
    {"city": "Hamburg",    "country": "Germany",     "indeed_country": "Germany",     "priority": 3},
    # Other Europe
    {"city": "Amsterdam",  "country": "Netherlands", "indeed_country": "Netherlands", "priority": 4},
    {"city": "Zurich",     "country": "Switzerland", "indeed_country": "Switzerland", "priority": 4},
    {"city": "Helsinki",   "country": "Finland",     "indeed_country": "Finland",     "priority": 4},
]

# ── Filtering ──────────────────────────────────────────────────────────────────
# Any of these in the job *title* → skip
TITLE_EXCLUDE = [
    #  "lead", "principal", "staff engineer", "director",
    "vp ", 
    # "head of", "manager", "architect", "5+ years", 
    "7+ years",
]

# At least one of these must appear in the job *title* to keep it
TITLE_INCLUDE = [
    "graduate", "junior", "entry", "trainee", "intern",
    "associate", "assistant", "researcher", "phd", "doctoral",
    "engineer", "developer", "qa", "quality", "test", "devops",
    "machine learning", "ml ", " ml", "data scientist", "software",
    "full stack", "fullstack", "backend", "frontend",
    "fintech", "quant",
]

# ── Sweden city sub-priority (for !jobsv sorting) ─────────────────────────────
# Lower number = shown first. Cities not listed default to 9.
SWEDEN_CITY_PRIORITY = {
    "stockholm":  1,
    "gothenburg": 2,
    "göteborg":   2,
    "malmö":      3,
    "malmo":      3,
}

# ── Tuning ─────────────────────────────────────────────────────────────────────
HOURS_OLD          = 24    # look back window for LinkedIn/Indeed
ACADEMIC_HOURS_OLD = 168   # 7 days for PhD/research (posted less frequently)
RESULTS_PER_CALL   = 25    # JobSpy results per query×country call
MAX_SUMMARY_JOBS   = 3     # how many jobs to show inline in Discord

# ── Web UI ─────────────────────────────────────────────────────────────────────
WEB_PORT = 8080
WEB_URL  = f"http://localhost:{WEB_PORT}"
