from __future__ import annotations

from jcp_data_manager.locations import (
    classify_market_class,
    expand_roster,
    get_census_region,
    get_market_class,
)


def test_classify_market_class_threshold_is_one_million() -> None:
    assert classify_market_class(1_000_000) == "big"
    assert classify_market_class(999_999) == "small"
    assert classify_market_class(9_434_123) == "big"
    assert classify_market_class(58_571) == "small"


def test_get_market_class_known_cities() -> None:
    assert get_market_class("Chicago, IL") == "big"
    assert get_market_class("Carson City, NV") == "small"
    # NCHS medium band (250k-999k) still collapses to "small" under the
    # two-way rule actually adopted.
    assert get_market_class("Boise, ID") == "small"


def test_get_market_class_unknown_city_returns_none() -> None:
    assert get_market_class("Nowhereville, XX") is None


def test_get_census_region_known_states() -> None:
    assert get_census_region("Chicago, IL") == "Midwest"
    assert get_census_region("Boston, MA") == "Northeast"
    assert get_census_region("Dallas, TX") == "South"
    assert get_census_region("Phoenix, AZ") == "West"


def test_get_census_region_is_case_insensitive() -> None:
    assert get_census_region("chicago, il") == "Midwest"


def test_get_census_region_unknown_state_returns_none() -> None:
    assert get_census_region("Nowhereville, XX") is None


def test_expand_roster_balances_across_regions_in_deterministic_order() -> None:
    # Chicago (Midwest) starts the roster. Among the three candidate
    # regions, Northeast and South are both at zero -- ties broken
    # alphabetically -- so Northeast goes first, then South (now the only
    # region still at zero), then Midwest (the last one left).
    result = expand_roster(
        base_roster=["Chicago, IL"],
        candidate_pool=["Boston, MA", "Columbus, OH", "Dallas, TX"],
        cap_multiplier=4,
    )
    assert result == ["Chicago, IL", "Boston, MA", "Dallas, TX", "Columbus, OH"]


def test_expand_roster_stops_at_the_cap_even_with_candidates_left() -> None:
    result = expand_roster(
        base_roster=["Chicago, IL"],
        candidate_pool=["Boston, MA", "Dallas, TX"],
        cap_multiplier=2,
    )
    assert result == ["Chicago, IL", "Boston, MA"]


def test_expand_roster_never_re_adds_a_city_already_in_the_roster() -> None:
    result = expand_roster(
        base_roster=["Chicago, IL", "Boston, MA"],
        candidate_pool=["Boston, MA", "Dallas, TX"],
        cap_multiplier=3,
    )
    assert result == ["Chicago, IL", "Boston, MA", "Dallas, TX"]


def test_expand_roster_stops_when_candidates_run_out_before_the_cap() -> None:
    result = expand_roster(
        base_roster=["Chicago, IL"],
        candidate_pool=["Boston, MA"],
        cap_multiplier=10,
    )
    assert result == ["Chicago, IL", "Boston, MA"]


def test_expand_roster_falls_back_to_pool_order_for_unrecognized_regions() -> None:
    # A candidate with no recognized state (region unknown) is passed over
    # in favor of region-balancing candidates, but still gets added via the
    # fallback once nothing with a known region is left.
    result = expand_roster(
        base_roster=["Chicago, IL"],
        candidate_pool=["Nowhereville, XX", "Boston, MA"],
        cap_multiplier=3,
    )
    assert result == ["Chicago, IL", "Boston, MA", "Nowhereville, XX"]


def test_expand_roster_takes_no_occupation_argument() -> None:
    # Structural guarantee behind the "roster-level, not occupation-specific"
    # rule (2026-10-09 proposal): there's no occupation parameter to even
    # call this with a per-occupation roster, so any caller is forced to
    # apply one expanded roster uniformly across every occupation.
    import inspect

    assert "occupation_title" not in inspect.signature(expand_roster).parameters
    assert "occupation" not in inspect.signature(expand_roster).parameters
