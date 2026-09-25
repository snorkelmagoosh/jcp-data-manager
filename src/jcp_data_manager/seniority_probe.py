"""Preliminary check of the seniority spread within each occupation.

Scrapes and filters real ads the same way get-jobs does, classifies each into
entry/mid/senior via seniority.py, and summarizes the tier shares per
occupation. Meant to be run before locking in occupation-specific seniority
keyword lists / years-of-experience cutpoints, so those are set from an
observed distribution rather than a guess.
"""

from __future__ import annotations

from typing import Iterable

import polars as pl

from .jobs import scrape_and_filter_jobs
from .seniority import classify_seniority_df


_EMPTY_CLASSIFIED_SCHEMA = {
    "query_occupation": pl.Utf8,
    "query_location": pl.Utf8,
    "title": pl.Utf8,
    "description": pl.Utf8,
    "seniority_tier": pl.Utf8,
    "seniority_source": pl.Utf8,
    "years_of_experience": pl.Int64,
}


def probe_seniority_spread(
    *,
    occupation_titles: Iterable[str],
    locations: Iterable[str],
    date_posted: str,
    results_wanted: int = 30,
    timeout: int = 10,
) -> pl.DataFrame:
    """Scrape+filter ads for every occupation x location cell and classify seniority.

    Reuses scrape_and_filter_jobs (the same qualification-keyword filter the
    production get-jobs pipeline applies), so the spread seen here reflects
    ads that would actually be eligible for posting, not a raw unfiltered
    scrape.
    """
    frames: list[pl.DataFrame] = []
    for occupation_title in occupation_titles:
        for location in locations:
            jobs_df = scrape_and_filter_jobs(
                occupation_title=occupation_title,
                date_posted=date_posted,
                location=location,
                results_wanted=results_wanted,
                timeout=timeout,
            )
            if jobs_df.is_empty():
                continue
            frames.append(
                jobs_df.with_columns(
                    pl.lit(occupation_title).alias("query_occupation"),
                    pl.lit(location).alias("query_location"),
                )
            )

    if not frames:
        return pl.DataFrame(schema=_EMPTY_CLASSIFIED_SCHEMA)

    combined = pl.concat(frames, how="diagonal_relaxed")
    return classify_seniority_df(combined)


def summarize_seniority_spread(classified_df: pl.DataFrame) -> pl.DataFrame:
    """Counts and within-occupation shares of each seniority tier."""
    if classified_df.is_empty():
        return pl.DataFrame(
            schema={
                "query_occupation": pl.Utf8,
                "seniority_tier": pl.Utf8,
                "count": pl.UInt32,
                "total": pl.UInt32,
                "share": pl.Float64,
            }
        )

    counts = classified_df.group_by(["query_occupation", "seniority_tier"]).agg(
        pl.len().alias("count")
    )
    totals = counts.group_by("query_occupation").agg(pl.col("count").sum().alias("total"))
    summary = counts.join(totals, on="query_occupation").with_columns(
        (pl.col("count") / pl.col("total")).round(3).alias("share")
    )
    return summary.sort(["query_occupation", "seniority_tier"])
