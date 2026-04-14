"""
LinkedIn + Indeed scraping via JobSpy.
"""

import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd

from backend.config import HOURS_OLD, JOBSPY_QUERIES, RESULTS_PER_CALL
from backend.sources.utils import _unique_countries, assign_location_priority


def _search_jobspy_country(query: str, country: dict, hours_old: int = HOURS_OLD) -> pd.DataFrame:
    """Search one query across an entire country."""
    try:
        from jobspy import scrape_jobs
        df = scrape_jobs(
            site_name=["linkedin", "indeed"],
            search_term=query,
            location=country["country"],
            results_wanted=RESULTS_PER_CALL,
            hours_old=hours_old,
            country_indeed=country["indeed_country"],
            linkedin_fetch_description=True,
        )
        if not df.empty:
            df["search_query"] = query
        return df
    except Exception as e:
        print(f"  [jobspy] {query} / {country['country']}: {e}")
        return pd.DataFrame()


def search_all_jobspy(
    status_callback=None,
    region: str = "all",
    hours_old: int = HOURS_OLD,
) -> pd.DataFrame:
    """
    Search LinkedIn + Indeed for all queries across the relevant countries.

    region="sweden" → Sweden only
    region="eu"     → non-Sweden EU countries
    region="all"    → Sweden + EU
    """
    all_countries = [c for c in _unique_countries() if c["priority"] <= 3]
    if region == "sweden":
        countries = [c for c in all_countries if c["country"] == "Sweden"]
    elif region == "eu":
        countries = [c for c in all_countries if c["country"] != "Sweden"]
    else:
        countries = all_countries

    country_names = " | ".join(c["country"] for c in countries)
    frames = []

    for i, query in enumerate(JOBSPY_QUERIES, 1):
        if status_callback:
            status_callback(f"[{i}/{len(JOBSPY_QUERIES)}] \"{query}\"  →  {country_names}")

        with ThreadPoolExecutor(max_workers=len(countries)) as pool:
            futures = {
                pool.submit(_search_jobspy_country, query, c, hours_old): c
                for c in countries
            }
            for future in as_completed(futures):
                df = future.result()
                if not df.empty:
                    frames.append(df)

        time.sleep(2)

    if not frames:
        return pd.DataFrame()

    combined = pd.concat(frames, ignore_index=True)
    combined["location_priority"] = combined["location"].fillna("").apply(assign_location_priority)
    return combined
