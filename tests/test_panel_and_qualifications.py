from __future__ import annotations

import polars as pl
import pytest

from jcp_data_manager.cli import build_parser
from jcp_data_manager.config import GithubModelsSettings
from jcp_data_manager.jobs import meets_all_qualifications, run_job_posting_pipeline, tag_qualifications
from jcp_data_manager.panel import run_panel_pipeline, summarize_panel_coverage


def test_tag_qualifications_adds_tier_source_years_and_market_class() -> None:
    df = pl.DataFrame(
        {
            "title": ["Senior Accountant", "Staff Accountant"],
            "description": ["", "2 years of experience required."],
        }
    )

    tagged = tag_qualifications(df, occupation_title="Accountant", location="Chicago, IL")

    assert tagged["seniority_tier"].to_list() == ["senior", "entry"]
    assert tagged["market_class"].to_list() == ["big", "big"]
    assert tagged["occupation_title"].to_list() == ["Accountant", "Accountant"]


def test_tag_qualifications_market_class_is_null_for_unknown_location() -> None:
    df = pl.DataFrame({"title": ["Senior Accountant"], "description": [""]})
    tagged = tag_qualifications(df, occupation_title="Accountant", location="Nowhereville, XX")
    assert tagged["market_class"].to_list() == [None]


def test_tag_qualifications_handles_empty_dataframe() -> None:
    df = pl.DataFrame(schema={"title": pl.Utf8, "description": pl.Utf8})
    tagged = tag_qualifications(df, occupation_title="Accountant", location="Chicago, IL")
    assert tagged.is_empty()
    assert "market_class" in tagged.columns


def test_meets_all_qualifications_requires_both_tier_and_market_class() -> None:
    df = pl.DataFrame(
        {
            "seniority_tier": ["senior", "unclassified", "entry"],
            "market_class": ["big", "big", None],
        }
    )
    kept = meets_all_qualifications(df)
    assert kept["seniority_tier"].to_list() == ["senior"]


def test_meets_all_qualifications_keeps_undifferentiated_unlike_unclassified() -> None:
    # "undifferentiated" (lumped-tier occupations, e.g. Truck Driver) is a
    # deliberate design choice, not missing data like "unclassified" --
    # it still counts toward the main/overall treatment effect, just
    # excluded from the pooled entry/mid/senior heterogeneity comparison
    # (that exclusion happens downstream, in analysis, not here).
    df = pl.DataFrame(
        {
            "seniority_tier": ["undifferentiated", "unclassified"],
            "market_class": ["big", "big"],
        }
    )
    kept = meets_all_qualifications(df)
    assert kept["seniority_tier"].to_list() == ["undifferentiated"]


def _fake_scrape_and_filter_jobs(*, occupation_title, date_posted, location, results_wanted, timeout, now=None):
    # market_class is tagged from the search location (the argument above),
    # not any per-row "location" text a job board happens to return -- that's
    # deliberate (it's "which market this cell was sourced from"), so both
    # rows below share one market_class regardless of row content.
    return pl.DataFrame(
        {
            "title": ["Senior Accountant", "Mystery Role With No Signal"],
            "description": ["", "Join our team!"],
            "location": [location, location],
            "company": ["Acme", "Acme"],
            "id": ["1", "2"],
        }
    )


def _patch_posting_pipeline_deps(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("jcp_data_manager.jobs.scrape_and_filter_jobs", _fake_scrape_and_filter_jobs)
    monkeypatch.setattr(
        "jcp_data_manager.jobs.generate_job_post_html",
        lambda df, settings, *, experiment: ["<p>html</p>"] * df.height,
    )
    monkeypatch.setattr(
        "jcp_data_manager.jobs.generate_similar_job_search_terms",
        lambda df, settings: ["accounting"] * df.height,
    )


def test_run_job_posting_pipeline_excludes_unclassified_and_unknown_market_by_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    _patch_posting_pipeline_deps(monkeypatch)

    final_df, _ = run_job_posting_pipeline(
        occupation_title="Accountant",
        date_posted="9/17/2026",
        location="Chicago, IL",
        github_settings=GithubModelsSettings(endpoint="x", token="x", model="x"),
        output_path=tmp_path / "out.csv",
        experiment=0,
        skip_post=True,
    )

    # Only the Senior Accountant ad survives -- the unclassified-title ad is
    # excluded by the default qualification gate, even though both came from
    # a known (big) market.
    assert final_df.height == 1
    assert final_df["seniority_tier"].to_list() == ["senior"]
    assert final_df["market_class"].to_list() == ["big"]


def test_run_job_posting_pipeline_excludes_the_whole_cell_for_an_unknown_market(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    _patch_posting_pipeline_deps(monkeypatch)

    final_df, _ = run_job_posting_pipeline(
        occupation_title="Accountant",
        date_posted="9/17/2026",
        location="Nowhereville, XX",
        github_settings=GithubModelsSettings(endpoint="x", token="x", model="x"),
        output_path=tmp_path / "out.csv",
        experiment=0,
        skip_post=True,
    )

    # Even the clearly-senior ad is excluded, because the whole cell was
    # sourced from a location not in the market-class lookup.
    assert final_df.is_empty()


def test_run_job_posting_pipeline_keeps_everything_with_allow_unclassified_ads(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    _patch_posting_pipeline_deps(monkeypatch)

    final_df, _ = run_job_posting_pipeline(
        occupation_title="Accountant",
        date_posted="9/17/2026",
        location="Nowhereville, XX",
        github_settings=GithubModelsSettings(endpoint="x", token="x", model="x"),
        output_path=tmp_path / "out.csv",
        experiment=0,
        skip_post=True,
        require_full_classification=False,
    )

    assert final_df.height == 2


def test_run_panel_pipeline_runs_every_cell_and_survives_one_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    calls = []

    def fake_run_job_posting_pipeline(*, occupation_title, location, output_path, **kwargs):
        calls.append((occupation_title, location))
        if location == "Boise, ID":
            raise RuntimeError("simulated scrape failure")
        df = pl.DataFrame(
            {
                "occupation_title": [occupation_title],
                "seniority_tier": ["senior"],
                "market_class": ["big" if location == "Chicago, IL" else "small"],
            }
        )
        return df, output_path

    monkeypatch.setattr("jcp_data_manager.panel.run_job_posting_pipeline", fake_run_job_posting_pipeline)

    panel_df = run_panel_pipeline(
        occupation_titles=["Accountant", "Electrician"],
        locations=["Chicago, IL", "Boise, ID"],
        date_posted="9/17/2026",
        github_settings=GithubModelsSettings(endpoint="x", token="x", model="x"),
        output_dir=tmp_path,
        experiment=0,
    )

    # 2 occupations x 2 locations = 4 cells attempted; Boise fails for both
    # occupations but doesn't abort the panel -- only the 2 Chicago cells
    # make it into the combined result.
    assert len(calls) == 4
    assert panel_df.height == 2
    assert set(panel_df["market_class"].to_list()) == {"big"}


def test_summarize_panel_coverage_checks_the_ad_count_threshold() -> None:
    panel_df = pl.DataFrame(
        {
            "occupation_title": ["Accountant"] * 6 + ["Electrician"],
            "seniority_tier": ["senior"] * 6 + ["entry"],
            "market_class": ["big"] * 6 + ["small"],
        }
    )
    summary = summarize_panel_coverage(panel_df, threshold=5)
    rows = {(r["occupation_title"], r["seniority_tier"], r["market_class"]): r for r in summary.to_dicts()}
    assert rows[("Accountant", "senior", "big")]["passes_threshold"] is True
    assert rows[("Electrician", "entry", "small")]["passes_threshold"] is False


def test_cli_parser_accepts_run_panel_with_repeated_flags() -> None:
    parser = build_parser()
    args = parser.parse_args(
        [
            "run-panel",
            "--occupation-title", "Accountant",
            "--occupation-title", "Electrician",
            "--location", "Chicago, IL",
            "--location", "Boise, ID",
            "--date-posted", "9/17/2026",
            "--experiment", "1",
            "--output-dir", "./panel-output",
        ]
    )
    assert args.command == "run-panel"
    assert args.occupation_titles == ["Accountant", "Electrician"]
    assert args.locations == ["Chicago, IL", "Boise, ID"]
    assert args.post is False


def test_cli_parser_get_jobs_allow_unclassified_ads_flag() -> None:
    parser = build_parser()
    args = parser.parse_args(
        [
            "get-jobs",
            "--occupation-title", "Accountant",
            "--date-posted", "9/17/2026",
            "--location", "Chicago, IL",
            "--experiment", "1",
            "--allow-unclassified-ads",
        ]
    )
    assert args.allow_unclassified_ads is True
