"""Market-size classification for sourcing locations.

Implements the big/small market-class rule adopted for the PAP (see
job_connections_pap_decisions.md, 2026-09-30): classify a sourcing city as
"big" or "small" by its *metropolitan statistical area* population (not city
population -- the two diverge a lot; see e.g. El Paso, city population 683k
but MSA only 881k), using the NCHS Urban-Rural Classification Scheme's
1,000,000 threshold (Ingram & Franco; CDC/NCHS Series 2, No. 154/166):

    big   = MSA population >= 1,000,000  (NCHS large central + large fringe)
    small = MSA population  < 1,000,000  (NCHS medium + small metro, merged)

CITY_MSA_POPULATIONS below is a STARTER lookup, not a finalized roster -- it
only covers cities actually used in this project's probes so far (the 9/30
and 10/2 ad-count-threshold checks). The city roster itself (how many big/
small cities, which ones) is still an open PAP decision. Populations are
2025 vintage estimates from the Census Bureau's own annual MSA population
file (cbsa-met-est2025-pop.xlsx, downloaded 2026-10-01) -- see that session's
work for the full small-metro candidate list (194 MSAs in the 50k-250k band)
if this needs expanding.
"""

from __future__ import annotations

import math

BIG_MARKET_THRESHOLD = 1_000_000


def classify_market_class(msa_population: int) -> str:
    """"big" if the MSA has >=1,000,000 residents, else "small"."""
    return "big" if msa_population >= BIG_MARKET_THRESHOLD else "small"


# Starter set -- expand as the city roster gets finalized. Keyed by the exact
# location string passed to JobSpy ("City, ST"), since that's what needs
# looking up at sourcing time.
CITY_MSA_POPULATIONS: dict[str, int] = {
    # Big (tested 9/30 and 10/2 probes)
    "Chicago, IL": 9_434_123,
    "Dallas, TX": 8_477_157,
    "Phoenix, AZ": 5_228_938,
    "Atlanta, GA": 6_482_182,
    "Denver, CO": 3_092_037,
    "Charlotte, NC": 2_938_830,
    "Columbus, OH": 2_242_028,
    "Indianapolis, IN": 2_205_695,
    "Raleigh, NC": 1_595_720,
    "Nashville, TN": 2_197_416,
    # Small -- genuinely small (NCHS 50k-250k band), tested 10/2
    "Carson City, NV": 58_571,
    "Enid, OK": 61_779,
    "Grand Island, NE": 78_076,
    "Elmira, NY": 80_415,
    "Casper, WY": 80_526,
    "Midland, MI": 83_754,
    "Great Falls, MT": 85_029,
    "Bozeman, MT": 128_740,
    "Rapid City, SD": 156_934,
    "Lafayette, IN": 228_468,
    "Charlottesville, VA": 228_597,
    "Las Cruces, NM": 229_091,
    "Topeka, KS": 233_052,
    "Fort Smith, AR": 234_140,
    "Bellingham, WA": 236_392,
    "Champaign, IL": 239_979,
    "Binghamton, NY": 243_189,
    "Lake Charles, LA": 244_655,
    # Small -- NCHS medium band (250k-999k), tested in the first (9/30) probe
    # round and earlier seniority-classification probes. Still "small" under
    # our two-way rule (it collapses NCHS medium+small), just worth knowing
    # these sit at the upper end of that range, not the lower end.
    "Boise, ID": 864_243,
    "Provo, UT": 773_426,
    "Spokane, WA": 608_012,
    "Chattanooga, TN": 594_530,
    "Springfield, MO": 500_694,
    "Lubbock, TX": 368_431,
    "Boulder, CO": 328_560,
}


def get_market_class(location: str) -> str | None:
    """Look up a sourcing location's market class, or None if not in the
    curated set above (callers should treat None as "unknown," not "small")."""
    population = CITY_MSA_POPULATIONS.get(location)
    if population is None:
        return None
    return classify_market_class(population)


# Standard Census Bureau 4-region breakdown by state, keyed by the 2-letter
# abbreviation already embedded in every "City, ST" location string -- no
# separate lookup data needed. This is a fixed federal classification, not a
# judgment call: https://www2.census.gov/geo/pdfs/maps-data/maps/reference/us_regdiv.pdf
_NORTHEAST_STATES = {"CT", "ME", "MA", "NH", "RI", "VT", "NJ", "NY", "PA"}
_MIDWEST_STATES = {"IL", "IN", "MI", "OH", "WI", "IA", "KS", "MN", "MO", "NE", "ND", "SD"}
_SOUTH_STATES = {
    "DE", "FL", "GA", "MD", "NC", "SC", "VA", "DC", "WV",
    "AL", "KY", "MS", "TN", "AR", "LA", "OK", "TX",
}
_WEST_STATES = {"AZ", "CO", "ID", "MT", "NV", "NM", "UT", "WY", "AK", "CA", "HI", "OR", "WA"}

STATE_TO_CENSUS_REGION: dict[str, str] = {
    **{state: "Northeast" for state in _NORTHEAST_STATES},
    **{state: "Midwest" for state in _MIDWEST_STATES},
    **{state: "South" for state in _SOUTH_STATES},
    **{state: "West" for state in _WEST_STATES},
}


def get_census_region(location: str) -> str | None:
    """"Northeast"/"Midwest"/"South"/"West" from a "City, ST" location
    string, or None if the state abbreviation isn't recognized."""
    state = location.strip().split(",")[-1].strip().upper()
    return STATE_TO_CENSUS_REGION.get(state)


def expand_roster(
    base_roster: list[str],
    candidate_pool: list[str],
    *,
    cap_multiplier: float = 1.5,
) -> list[str]:
    """Grow one market class's shared city roster by adding backup cities,
    balanced across Census region (see job_connections_pap_decisions.md,
    "Lumped-tier occupations" entry's sibling, the backup-city-rule
    proposal -- 2026-10-09, not yet accepted by Tanner).

    Deliberately takes no occupation argument: the whole point of this rule
    is that a roster expansion applies to every occupation sourcing from
    this market class, never to just the one occupation whose cell failed
    -- otherwise occupations would end up sourced from different cities,
    re-confounding occupation with geography (the original 9/17 concern).
    Any caller must apply the returned roster uniformly across occupations.

    candidate_pool should already be in the caller's intended priority
    order (e.g. by MSA population descending) -- ties within a region are
    broken by that order, which is what makes this deterministic and
    pre-registerable rather than picked after seeing which cities have more
    ads.

    Stops at whichever comes first: candidates exhausted, or the roster
    reaching ceil(len(base_roster) * cap_multiplier) cities (the proposed
    +50% cap at the default 1.5 multiplier). Cities already in base_roster
    are never re-added even if they also appear in candidate_pool.
    """
    roster = list(base_roster)
    cap = math.ceil(len(base_roster) * cap_multiplier)
    remaining = [city for city in candidate_pool if city not in roster]

    region_counts: dict[str, int] = {}
    for city in roster:
        region = get_census_region(city)
        if region is not None:
            region_counts[region] = region_counts.get(region, 0) + 1

    while len(roster) < cap and remaining:
        remaining_regions = {
            region
            for city in remaining
            if (region := get_census_region(city)) is not None
        }
        if not remaining_regions:
            # No region data for anything left -- fall back to candidate
            # order rather than stalling.
            next_city = remaining.pop(0)
        else:
            target_region = min(remaining_regions, key=lambda r: (region_counts.get(r, 0), r))
            next_city = next(city for city in remaining if get_census_region(city) == target_region)
            remaining.remove(next_city)

        roster.append(next_city)
        region = get_census_region(next_city)
        if region is not None:
            region_counts[region] = region_counts.get(region, 0) + 1

    return roster
