from __future__ import annotations

import polars as pl

from jcp_data_manager.title_filters import filter_adjacent_titles, get_title_exclude_keywords


def _titles_df(*titles: str) -> pl.DataFrame:
    return pl.DataFrame({"title": list(titles)})


def test_unlisted_occupation_has_no_exclude_keywords() -> None:
    assert get_title_exclude_keywords("Registered Nurse") == ()


def test_unlisted_occupation_is_a_no_op() -> None:
    df = _titles_df("Front Desk Receptionist")
    result = filter_adjacent_titles(df, occupation_title="Registered Nurse")
    assert result.height == 1


def test_food_service_manager_drops_production_and_line_staff_roles() -> None:
    df = _titles_df(
        "General Manager",
        "Restaurant Manager",
        "Lead Cook",
        "Sous chef/ Culinary Supervisor",
        "Lead Baker",
        "Barista/Supervisor",
    )
    result = filter_adjacent_titles(df, occupation_title="Food Service Manager")
    assert result["title"].to_list() == ["General Manager", "Restaurant Manager"]


def test_food_service_manager_drops_dual_titled_ads_even_with_manager_present() -> None:
    # "Manager" alone isn't enough to keep an ad -- Kitchen Manager/Head Chef,
    # Deli Manager, and Bartender Manager are real postings from the 10/8
    # audit that pair a legitimate-sounding management word with a
    # production/line-staff or wrong-department term.
    df = _titles_df(
        "Kitchen Manager",
        "Kitchen Manager/Head Chef",
        "Deli Manager",
        "Bartender Manager",
    )
    result = filter_adjacent_titles(df, occupation_title="Food Service Manager")
    assert result["title"].to_list() == ["Kitchen Manager"]


def test_food_service_manager_drops_wrong_occupation_hit() -> None:
    df = _titles_df("General Manager", "PATIENT SERVICES LEAD (FULL TIME)")
    result = filter_adjacent_titles(df, occupation_title="Food Service Manager")
    assert result["title"].to_list() == ["General Manager"]


def test_electrician_keeps_real_electrician_titles() -> None:
    df = _titles_df(
        "Electrician",
        "Journeyman Electrician",
        "Maintenance Electrician",
        "Industrial Electrician",
    )
    result = filter_adjacent_titles(df, occupation_title="Electrician")
    assert result.height == 4


def test_electrician_drops_adjacent_trades_and_mismatches() -> None:
    df = _titles_df(
        "Journeyman Electrician",
        "Electrical Sign Installer",
        "Lead Electric Sign Installer",
        "Sign Service Technician",
        "Electrical and Instrumentation Technician",
        "Experienced Lighting Technician",
        "Low Voltage Electrician / Security Camera Installer",
        "Temporary Trades Helper 1st Year (Tempe)",
        "Superintendent - Water - Manufacturing - Automation",
    )
    result = filter_adjacent_titles(df, occupation_title="Electrician")
    assert result["title"].to_list() == ["Journeyman Electrician"]


def test_matching_is_case_insensitive() -> None:
    df = _titles_df("chef manager", "CHEF MANAGER")
    result = filter_adjacent_titles(df, occupation_title="Food Service Manager")
    assert result.is_empty()


def test_empty_dataframe_is_returned_unchanged() -> None:
    df = pl.DataFrame({"title": []}, schema={"title": pl.Utf8})
    result = filter_adjacent_titles(df, occupation_title="Food Service Manager")
    assert result.is_empty()
