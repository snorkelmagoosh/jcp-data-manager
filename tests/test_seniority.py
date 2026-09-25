from __future__ import annotations

import polars as pl
import pytest

from jcp_data_manager.cli import build_parser
from jcp_data_manager.seniority import (
    DEFAULT_OCCUPATION_SENIORITY_RULES,
    OCCUPATION_SENIORITY_RULES,
    OccupationSeniorityRules,
    classify_seniority,
    classify_seniority_df,
    classify_title_seniority,
    classify_yoe_seniority,
    extract_years_of_experience,
)
from jcp_data_manager.seniority_probe import probe_seniority_spread, summarize_seniority_spread


def test_extract_years_of_experience_takes_the_max_mentioned_figure() -> None:
    text = "2+ years of experience required, 5+ years of experience preferred for senior candidates."
    assert extract_years_of_experience(text) == 5


def test_extract_years_of_experience_handles_a_range() -> None:
    assert extract_years_of_experience("3-7 years of relevant experience") == 7


def test_extract_years_of_experience_tolerates_a_filler_word_before_experience() -> None:
    # Real ad text: "1 year of related experience" -- the adjective in between
    # broke an earlier, stricter version of this regex.
    assert extract_years_of_experience("Must have 1 year of related experience.") == 1


def test_extract_years_of_experience_returns_none_when_absent() -> None:
    assert extract_years_of_experience("A great opportunity to join our team.") is None
    assert extract_years_of_experience(None) is None


def test_extract_years_of_experience_ignores_minimum_age_requirements() -> None:
    # Regression: a real Truck Driver ad said "Be at least 21 years of age"
    # (a minimum-age requirement) right next to "1 year of related
    # experience" (the real YOE requirement) -- the age line must not be
    # read as 21 years of experience.
    text = "Minimum qualifications:\n* Be at least 21 years of age\n* 1 year of related experience"
    assert extract_years_of_experience(text) == 1


def test_classify_title_seniority_prefers_senior_over_entry_keyword_collision() -> None:
    rules = DEFAULT_OCCUPATION_SENIORITY_RULES
    assert classify_title_seniority("Senior Associate Engineer", rules) == "senior"


def test_classify_title_seniority_matches_entry_keyword() -> None:
    rules = DEFAULT_OCCUPATION_SENIORITY_RULES
    assert classify_title_seniority("Junior Accountant", rules) == "entry"


def test_classify_title_seniority_returns_none_without_a_keyword_match() -> None:
    rules = DEFAULT_OCCUPATION_SENIORITY_RULES
    assert classify_title_seniority("Accountant", rules) is None
    assert classify_title_seniority(None, rules) is None


def test_classify_title_seniority_matches_supervising_via_stem() -> None:
    # Regression: a real ad titled "SUPERVISING PARALEGAL" fell through an
    # exact "supervisor" keyword.
    rules = DEFAULT_OCCUPATION_SENIORITY_RULES
    assert classify_title_seniority("Supervising Paralegal", rules) == "senior"


def test_classify_title_seniority_matches_student_as_entry() -> None:
    rules = DEFAULT_OCCUPATION_SENIORITY_RULES
    assert classify_title_seniority("Truck Driver Student", rules) == "entry"


def test_classify_yoe_seniority_splits_on_configured_cutpoints() -> None:
    rules = OccupationSeniorityRules(yoe_low_max=2, yoe_high_min=5)
    assert classify_yoe_seniority(1, rules) == "entry"
    assert classify_yoe_seniority(3, rules) == "mid"
    assert classify_yoe_seniority(5, rules) == "senior"
    assert classify_yoe_seniority(None, rules) is None


def test_classify_seniority_prefers_title_over_years_of_experience() -> None:
    result = classify_seniority(
        title="Senior Paralegal",
        description="0-1 years of experience welcome.",
        occupation_title="Paralegal",
    )
    assert result["seniority_tier"] == "senior"
    assert result["seniority_source"] == "title"
    assert result["years_of_experience"] == 1


def test_classify_seniority_falls_back_to_years_of_experience_without_a_title_ladder() -> None:
    result = classify_seniority(
        title="Elementary School Teacher",
        description="3+ years classroom experience preferred.",
        occupation_title="Elementary School Teacher",
    )
    assert result["seniority_tier"] == "mid"
    assert result["seniority_source"] == "years_of_experience"
    assert result["years_of_experience"] == 3


def test_classify_seniority_is_unclassified_without_any_signal() -> None:
    result = classify_seniority(title="Teacher", description="Join our team!", occupation_title="Teacher")
    assert result["seniority_tier"] == "unclassified"
    assert result["seniority_source"] is None


def test_classify_seniority_uses_occupation_specific_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(
        OCCUPATION_SENIORITY_RULES,
        "staff accountant",
        OccupationSeniorityRules(entry_title_keywords=("staff",), senior_title_keywords=("controller", "manager")),
    )

    result = classify_seniority(title="Staff Accountant", description="", occupation_title="Staff Accountant")
    assert result["seniority_tier"] == "entry"
    assert result["seniority_source"] == "title"


def test_classify_seniority_df_adds_expected_columns() -> None:
    df = pl.DataFrame(
        {
            "query_occupation": ["Paralegal", "Truck Driver"],
            "title": ["Senior Paralegal", "Truck Driver"],
            "description": ["", "2 years of experience required."],
        }
    )

    classified = classify_seniority_df(df)

    assert classified["seniority_tier"].to_list() == ["senior", "entry"]
    assert classified["seniority_source"].to_list() == ["title", "years_of_experience"]
    assert classified["years_of_experience"].to_list() == [None, 2]


def test_classify_seniority_df_handles_an_empty_dataframe() -> None:
    df = pl.DataFrame(schema={"query_occupation": pl.Utf8, "title": pl.Utf8, "description": pl.Utf8})
    classified = classify_seniority_df(df)
    assert classified.is_empty()
    assert "seniority_tier" in classified.columns


def test_probe_seniority_spread_tags_each_cell_and_classifies(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_scrape_and_filter_jobs(*, occupation_title, date_posted, location, results_wanted, timeout):
        if occupation_title == "Paralegal" and location == "Chicago, IL":
            return pl.DataFrame(
                {
                    "title": ["Senior Paralegal", "Paralegal"],
                    "description": ["", "1 year of experience."],
                }
            )
        return pl.DataFrame()

    monkeypatch.setattr(
        "jcp_data_manager.seniority_probe.scrape_and_filter_jobs",
        fake_scrape_and_filter_jobs,
    )

    classified = probe_seniority_spread(
        occupation_titles=["Paralegal"],
        locations=["Chicago, IL", "Boise, ID"],
        date_posted="9/1/2026",
    )

    assert classified.height == 2
    assert set(classified["query_location"].to_list()) == {"Chicago, IL"}
    assert classified["seniority_tier"].to_list() == ["senior", "entry"]


def test_probe_seniority_spread_returns_empty_schema_when_nothing_found(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "jcp_data_manager.seniority_probe.scrape_and_filter_jobs",
        lambda **kwargs: pl.DataFrame(),
    )

    classified = probe_seniority_spread(
        occupation_titles=["Paralegal"],
        locations=["Chicago, IL"],
        date_posted="9/1/2026",
    )

    assert classified.is_empty()
    assert "seniority_tier" in classified.columns


def test_summarize_seniority_spread_computes_within_occupation_shares() -> None:
    classified = pl.DataFrame(
        {
            "query_occupation": ["Paralegal", "Paralegal", "Paralegal", "Truck Driver"],
            "seniority_tier": ["entry", "entry", "senior", "mid"],
        }
    )

    summary = summarize_seniority_spread(classified)
    rows = {(row["query_occupation"], row["seniority_tier"]): row for row in summary.to_dicts()}

    assert rows[("Paralegal", "entry")]["count"] == 2
    assert rows[("Paralegal", "entry")]["share"] == pytest.approx(2 / 3, rel=1e-3)
    assert rows[("Paralegal", "senior")]["share"] == pytest.approx(1 / 3, rel=1e-3)
    assert rows[("Truck Driver", "mid")]["share"] == 1.0


def test_summarize_seniority_spread_handles_empty_input() -> None:
    summary = summarize_seniority_spread(pl.DataFrame(schema={"query_occupation": pl.Utf8, "seniority_tier": pl.Utf8}))
    assert summary.is_empty()


def test_cli_parser_accepts_check_seniority_spread_with_repeated_flags() -> None:
    parser = build_parser()
    args = parser.parse_args(
        [
            "check-seniority-spread",
            "--occupation-title",
            "Paralegal",
            "--occupation-title",
            "Truck Driver",
            "--location",
            "Chicago, IL",
            "--location",
            "Boise, ID",
            "--date-posted",
            "9/1/2026",
        ]
    )

    assert args.command == "check-seniority-spread"
    assert args.occupation_titles == ["Paralegal", "Truck Driver"]
    assert args.locations == ["Chicago, IL", "Boise, ID"]
    assert args.results_wanted == 30


def test_check_seniority_spread_requires_at_least_one_occupation_and_location() -> None:
    parser = build_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(["check-seniority-spread", "--date-posted", "9/1/2026"])
