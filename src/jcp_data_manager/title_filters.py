"""Title-level sourcing-precision filter.

Some occupation search terms pull in ads for a different, adjacent
occupation -- flagged as a side finding 9/25, confirmed with real link-level
evidence on 10/8 (see job_connections.md). This module drops those ads by
title before they reach the per-URL qualification-keyword check or
seniority tiering, the same way seniority.py's keyword ladder is
occupation-specific config rather than one shared list.

Food Service Manager (confirmed 10/8 against a 202-ad/118-title sample):
production/line-staff roles (cook, chef, baker, bread, barista, bartender,
pastry, deli) and one wrong-occupation hit (a healthcare "Patient Services
Lead" contractor role). Applied even when "manager"/"supervisor"/"lead" is
also present in the same title ("Kitchen Manager/Head Chef", "Deli Manager",
"Bartender Manager") -- the production/line-staff or wrong-occupation word
means the ad describes a different role, not a qualifying variant of Food
Service Manager.

Electrician (confirmed 10/8 against a 24-ad/19-title smoke sample across 4
cities): adjacent trades (sign installation, lighting, instrumentation,
low-voltage/security) plus two outright mismatches (a generic trades-helper
role, a water/manufacturing superintendent role).

Deliberately conservative: ambiguous titles seen in the same audits but not
clearly resolvable from the title alone (e.g. "Culinary Manager", "Line
Manager", "Store Manager", "Food Service Lead I/II") are left unfiltered
rather than guessed -- same posture as seniority.py's "left unencoded rather
than guessed" calls. Worth re-running both audits against a larger sample
before trusting these lists at full fielding volume.
"""

from __future__ import annotations

import re

import polars as pl

DEFAULT_TITLE_EXCLUDE_KEYWORDS: tuple[str, ...] = ()

_FOOD_SERVICE_MANAGER_EXCLUDES: tuple[str, ...] = (
    "cook",
    "chef",
    "baker",
    "bread",
    "barista",
    "bartender",
    "pastry",
    "deli",
    "patient services",
    "food service worker",
    "team member",
    "team lead",
    "boh",
)

_ELECTRICIAN_EXCLUDES: tuple[str, ...] = (
    "sign installer",
    "sign service",
    "sign technician",
    "lighting technician",
    "instrumentation",
    "security camera",
    "trades helper",
    "superintendent",
)

OCCUPATION_TITLE_EXCLUDE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "food service manager": _FOOD_SERVICE_MANAGER_EXCLUDES,
    "electrician": _ELECTRICIAN_EXCLUDES,
}


def get_title_exclude_keywords(occupation_title: str) -> tuple[str, ...]:
    return OCCUPATION_TITLE_EXCLUDE_KEYWORDS.get(
        occupation_title.strip().lower(), DEFAULT_TITLE_EXCLUDE_KEYWORDS
    )


def filter_adjacent_titles(df: pl.DataFrame, *, occupation_title: str) -> pl.DataFrame:
    """Drop ads whose title matches one of occupation_title's exclude
    keywords (case-insensitive substring match) -- adjacent-occupation
    contamination pulled in by the site search, not a real candidate for
    this occupation. No-op for occupations without a documented
    contamination pattern, and for an empty input.
    """
    keywords = get_title_exclude_keywords(occupation_title)
    if not keywords or df.is_empty():
        return df

    pattern = "|".join(re.escape(keyword) for keyword in keywords)
    return df.filter(~pl.col("title").str.to_lowercase().str.contains(pattern))
