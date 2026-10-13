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
    get_seniority_rules,
)
from jcp_data_manager.seniority_probe import probe_seniority_spread, summarize_seniority_spread


def test_electrician_ladder_is_apprentice_journeyman_master() -> None:
    assert classify_title_seniority("Apprentice Electrician", get_seniority_rules("Electrician")) == "entry"
    assert classify_title_seniority("Journeyman Electrician", get_seniority_rules("Electrician")) == "mid"
    assert classify_title_seniority("Non-Licensed Journeyman Electrician", get_seniority_rules("Electrician")) == "mid"
    assert (
        classify_title_seniority("Licensed Master Electrician", get_seniority_rules("Electrician")) == "senior"
    )


def test_software_developer_treats_staff_as_senior_unlike_accounting() -> None:
    rules = get_seniority_rules("Software Developer")
    assert classify_title_seniority("Staff Software Engineer", rules) == "senior"
    assert classify_title_seniority("Entry Level C++ Software Engineer", rules) == "entry"


def test_software_developer_yoe_high_min_raised_from_calibration_check() -> None:
    # 9/29 calibration: real ads independently title-tagged "senior" had a
    # median of 6.5 stated years (n=10) -- above the shared default of 5.
    rules = get_seniority_rules("Software Developer")
    assert classify_yoe_seniority(5, rules) == "mid"
    assert classify_yoe_seniority(6, rules) == "mid"
    assert classify_yoe_seniority(7, rules) == "senior"
    # Unrelated occupations keep the shared default (5), unaffected by the
    # Software Developer-specific override.
    assert classify_yoe_seniority(5, get_seniority_rules("Accountant")) == "senior"


def test_accountant_treats_staff_as_entry_unlike_software() -> None:
    rules = get_seniority_rules("Accountant")
    assert classify_title_seniority("Staff Accountant", rules) == "entry"
    assert classify_title_seniority("Corporate Staff Accountant", rules) == "entry"
    assert classify_title_seniority("Senior Accountant", rules) == "senior"


def test_auditor_is_aliased_to_the_same_rules_as_accountant() -> None:
    assert get_seniority_rules("Auditor") is get_seniority_rules("Accountant")


def test_loan_officer_assistant_is_entry() -> None:
    rules = get_seniority_rules("Loan Officer")
    assert classify_title_seniority("Loan Officer Assistant", rules) == "entry"
    assert classify_title_seniority("Sr. Commercial Loan Officer", rules) == "senior"


def test_credit_counselor_is_aliased_to_the_same_rules_as_loan_officer() -> None:
    assert get_seniority_rules("Credit Counselor") is get_seniority_rules("Loan Officer")


def test_food_service_manager_plain_manager_title_is_mid_not_senior() -> None:
    # Regression: the occupation's own base title ("Manager") collides with
    # the shared "manag" senior stem -- without the override, the dominant
    # real-world case (plain "Restaurant Manager"/"General Manager") would be
    # misclassified as senior by default.
    rules = get_seniority_rules("Food Service Manager")
    assert classify_title_seniority("Restaurant Manager", rules) == "mid"
    assert classify_title_seniority("General Manager", rules) == "mid"
    assert classify_title_seniority("Assistant Manager", rules) == "entry"
    assert classify_title_seniority("Regional Director of Operations", rules) == "senior"


def test_paralegal_numbered_tiers_and_legal_assistant() -> None:
    rules = get_seniority_rules("Paralegal")
    assert classify_title_seniority("Legal Assistant", rules) == "entry"
    assert classify_title_seniority("Paralegal II", rules) == "mid"
    assert classify_title_seniority("Paralegal III", rules) == "senior"
    # "Paralegal I" is deliberately left unencoded (thin survey evidence).
    assert classify_title_seniority("Paralegal I", rules) is None


def test_legal_assistant_is_aliased_to_the_same_rules_as_paralegal() -> None:
    assert get_seniority_rules("Legal Assistant") is get_seniority_rules("Paralegal")


def test_registered_nurse_clinical_ladder() -> None:
    rules = get_seniority_rules("Registered Nurse")
    assert classify_title_seniority("Staff Nurse I - Neuroscience Center (Float)", rules) == "entry"
    assert classify_title_seniority("Registered Nurse Inpatient II - Treasure Valley Hospital", rules) == "mid"
    assert classify_title_seniority("Registered Nurse III", rules) == "senior"
    assert classify_title_seniority("Registered Nurse IV", rules) == "senior"


def test_administrative_assistant_self_confirmed_numbering() -> None:
    rules = get_seniority_rules("Administrative Assistant")
    assert classify_title_seniority("Administrative Assistant II", rules) == "mid"
    assert classify_title_seniority("Administrative Assistant III", rules) == "senior"
    assert classify_title_seniority("Executive Assistant", rules) == "senior"
    # "Administrative Assistant I" is deliberately left unencoded.
    assert classify_title_seniority("Administrative Assistant I", rules) is None


def test_secretary_is_aliased_to_the_same_rules_as_administrative_assistant() -> None:
    assert get_seniority_rules("Secretary") is get_seniority_rules("Administrative Assistant")


def test_truck_driver_and_financial_analyst_have_no_override() -> None:
    # Deliberate: Truck Driver's numbering tracks CDL class, not seniority;
    # Financial Analyst already classifies correctly on shared defaults.
    assert "truck driver" not in OCCUPATION_SENIORITY_RULES
    assert "financial analyst" not in OCCUPATION_SENIORITY_RULES
    assert get_seniority_rules("Truck Driver") is DEFAULT_OCCUPATION_SENIORITY_RULES
    assert get_seniority_rules("Financial Analyst") is DEFAULT_OCCUPATION_SENIORITY_RULES


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


def test_extract_years_of_experience_ignores_company_history_boilerplate() -> None:
    # Regression: real ads pulled 9/29 had company-legacy marketing copy
    # ("over 100 years of battery experience", "more than 135 years of
    # financial experience", "built on more than 50 years of experience")
    # sitting in the same ad as, and outranking via max(), the real, much
    # lower candidate requirement.
    text = (
        "With locations throughout the world, and over 100 years of battery "
        "experience, EnerSys is a great place to work.\n"
        "Qualifications:\n* 3-5 years progressive financial experience required."
    )
    assert extract_years_of_experience(text) == 5


def test_extract_years_of_experience_ignores_industry_legacy_boilerplate_near_the_cap() -> None:
    # Regression: a real ad said "over 40 years of experience in the
    # hospitality industry" (industry/company legacy, not the candidate) --
    # this slipped through the first, more permissive cap of 40 exactly
    # because it landed right at the boundary.
    text = "People Focused. Performance Driven. With over 40 years of experience in the hospitality industry."
    assert extract_years_of_experience(text) is None


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


def test_classify_title_seniority_matches_new_grad_phrases_as_entry() -> None:
    # Added 9/29 after a real-title vocabulary search turned up "New Grad
    # Staff Nurse", "New Graduate Registered Nurse", "Finance Graduate
    # Program" as unclassified titles with an obvious entry signal.
    rules = DEFAULT_OCCUPATION_SENIORITY_RULES
    assert classify_title_seniority("New Graduate Registered Nurse", rules) == "entry"
    assert classify_title_seniority("New Grad Staff Nurse - Family Medicine", rules) == "entry"
    assert classify_title_seniority("Finance Graduate Program", rules) == "entry"


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


def test_classify_seniority_lumps_truck_driver_into_one_tier_regardless_of_title() -> None:
    # 2026-10-13 (Tanner): Truck Driver has no real tier signal at all, so
    # every ad is lumped into one tier instead of run through the ladder --
    # a title that would otherwise read as senior/entry/mid is ignored.
    senior_looking = classify_seniority(
        title="Senior Truck Driver", description="", occupation_title="Truck Driver"
    )
    plain = classify_seniority(title="Truck Driver", description="", occupation_title="Truck Driver")
    assert senior_looking["seniority_tier"] == plain["seniority_tier"] == "entry"
    assert senior_looking["seniority_source"] == "lumped"


def test_classify_seniority_lumps_loan_officer_and_its_credit_counselor_alias() -> None:
    # Unlike Truck Driver, Loan Officer's title-based signal isn't broken --
    # it's just too thin in volume (0/6 cells passed the round-3 ad-count
    # threshold) -- lumped for the same reason Tanner approved regardless.
    result = classify_seniority(
        title="Sr. Commercial Loan Officer", description="", occupation_title="Loan Officer"
    )
    aliased = classify_seniority(
        title="Sr. Commercial Loan Officer", description="", occupation_title="Credit Counselor"
    )
    assert result["seniority_tier"] == aliased["seniority_tier"] == "entry"
    assert result["seniority_source"] == aliased["seniority_source"] == "lumped"


def test_classify_seniority_still_extracts_years_for_a_lumped_occupation() -> None:
    # Lumping skips the tiering decision, not the underlying extraction --
    # years_of_experience stays available for audit/reporting.
    result = classify_seniority(
        title="Truck Driver", description="2+ years of experience required.", occupation_title="Truck Driver"
    )
    assert result["seniority_tier"] == "entry"
    assert result["years_of_experience"] == 2


def test_classify_seniority_does_not_lump_unrelated_occupations() -> None:
    result = classify_seniority(title="Senior Accountant", description="", occupation_title="Accountant")
    assert result["seniority_tier"] == "senior"
    assert result["seniority_source"] == "title"


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
            "query_occupation": ["Paralegal", "Elementary School Teacher"],
            "title": ["Senior Paralegal", "Elementary School Teacher"],
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
