"""Panel-level sourcing: run the full occupation x location panel
automatically, instead of invoking get-jobs once per cell by hand.

This is the "automate the job posting process" piece discussed back on
9/18 -- a thin loop over run_job_posting_pipeline (jobs.py), which already
does the real work (scrape, filter, classify, tag, optionally post) for one
occupation x location cell. The occupation and city roster are NOT hardcoded
here -- both are still-open PAP decisions (see job_connections_pap_decisions.md)
-- callers pass whichever occupations/locations they want sourced.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

import polars as pl

from .config import GithubModelsSettings, WordPressSettings
from .jobs import run_job_posting_pipeline


def _slug(value: str) -> str:
    return value.strip().lower().replace(" ", "_").replace(",", "")


def run_panel_pipeline(
    *,
    occupation_titles: Iterable[str],
    locations: Iterable[str],
    date_posted: str,
    github_settings: GithubModelsSettings,
    wordpress_settings: WordPressSettings | None = None,
    output_dir: str | Path,
    results_wanted: int = 20,
    include_linkedin_popup: bool = True,
    experiment: int,
    skip_post: bool = True,
    keyword_timeout: int = 10,
    require_full_classification: bool = True,
) -> pl.DataFrame:
    """Run run_job_posting_pipeline across every occupation x location cell.

    Saves one output file per cell under output_dir (same naming convention
    as get-jobs) and returns the combined DataFrame across every cell, for
    summarize_panel_coverage(). A single cell's failure (a dead scraper, a
    rate limit, ...) is logged and skipped rather than aborting the whole
    panel -- learned the hard way probing the ad-count threshold on 10/2,
    where one failure killed a 33-minute run and lost everything.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    frames: list[pl.DataFrame] = []
    for occupation_title in occupation_titles:
        for location in locations:
            cell_output = output_dir / f"{_slug(occupation_title)}_{_slug(location)}.csv"
            try:
                cell_df, _ = run_job_posting_pipeline(
                    occupation_title=occupation_title,
                    date_posted=date_posted,
                    location=location,
                    github_settings=github_settings,
                    wordpress_settings=wordpress_settings,
                    output_path=cell_output,
                    results_wanted=results_wanted,
                    include_linkedin_popup=include_linkedin_popup,
                    experiment=experiment,
                    skip_post=skip_post,
                    keyword_timeout=keyword_timeout,
                    require_full_classification=require_full_classification,
                )
            except Exception as exc:  # noqa: BLE001 -- one bad cell must not sink the panel
                print(f"[{occupation_title} / {location}] failed: {exc}")
                continue

            print(f"[{occupation_title} / {location}] {cell_df.height} ads ready")
            if not cell_df.is_empty():
                frames.append(cell_df)

    if not frames:
        return pl.DataFrame()
    return pl.concat(frames, how="diagonal_relaxed")


def summarize_panel_coverage(panel_df: pl.DataFrame, *, threshold: int = 5) -> pl.DataFrame:
    """Pooled ad counts per occupation x seniority_tier x market_class,
    checked against the pre-committed ad-count threshold (>=5 qualifying
    ads per cell -- see job_connections_pap_decisions.md and the 9/30/10/2
    ad-count-threshold probes). Most useful run with
    require_full_classification=True (the default), since otherwise
    seniority_tier can be "unclassified" and market_class can be null,
    which this still reports but which aren't real design cells.
    """
    if panel_df.is_empty():
        return panel_df

    counts = panel_df.group_by(["occupation_title", "seniority_tier", "market_class"]).agg(
        pl.len().alias("n")
    )
    return counts.with_columns((pl.col("n") >= threshold).alias("passes_threshold")).sort(
        ["occupation_title", "seniority_tier", "market_class"]
    )
