"""Seniority classification for scraped job ads.

Design: the classification *logic* is uniform across occupations (title-keyword
ladder first, years-of-experience extracted from the ad body as a fallback),
but the actual keyword lists and years-of-experience cutpoints are
occupation-specific config, not a single shared list. A shared list breaks
across occupations -- "Staff" means senior in tech ("Staff Engineer") and
entry-level in accounting ("Staff Accountant") -- so each occupation gets its
own entry in OCCUPATION_SENIORITY_RULES, falling back to
DEFAULT_OCCUPATION_SENIORITY_RULES until real ads have been checked for it.
See seniority_probe.py for the preliminary check used to populate these.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import polars as pl


SENIORITY_TIERS = ("entry", "mid", "senior")

DEFAULT_ENTRY_TITLE_KEYWORDS: tuple[str, ...] = (
    "entry level",
    "entry-level",
    "associate",
    "junior",
    "jr.",
    "trainee",
    "apprentice",
    "intern",
    "student",
)
DEFAULT_MID_TITLE_KEYWORDS: tuple[str, ...] = (
    "mid-level",
    "mid level",
    "intermediate",
)
# Stems ("supervis", "manag") rather than exact words on purpose, so
# "supervising"/"supervisor"/"supervisory" and "manager"/"managing" all match
# -- caught as a real gap by the preliminary probe, where "SUPERVISING
# PARALEGAL" fell through an exact "supervisor" keyword. Safe here because
# this only runs against the (short) title field, not the full ad body.
DEFAULT_SENIOR_TITLE_KEYWORDS: tuple[str, ...] = (
    "senior",
    "sr.",
    "lead",
    "principal",
    "manag",
    "director",
    "supervis",
    "master",
)

# <=2 years -> entry, >=5 years -> senior, in between -> mid. Occupation
# entries below override these where a real distribution says otherwise.
DEFAULT_YOE_LOW_MAX = 2
DEFAULT_YOE_HIGH_MIN = 5


@dataclass(frozen=True)
class OccupationSeniorityRules:
    """Keyword ladder and years-of-experience split points for one occupation."""

    entry_title_keywords: tuple[str, ...] = DEFAULT_ENTRY_TITLE_KEYWORDS
    mid_title_keywords: tuple[str, ...] = DEFAULT_MID_TITLE_KEYWORDS
    senior_title_keywords: tuple[str, ...] = DEFAULT_SENIOR_TITLE_KEYWORDS
    yoe_low_max: int = DEFAULT_YOE_LOW_MAX
    yoe_high_min: int = DEFAULT_YOE_HIGH_MIN


DEFAULT_OCCUPATION_SENIORITY_RULES = OccupationSeniorityRules()

# Per-occupation overrides, keyed by lowercased occupation title. Empty until
# the preliminary seniority-spread check (seniority_probe.py) has been run
# against real ads for each occupation in the panel -- fill these in from
# that output rather than guessing keyword lists up front.
OCCUPATION_SENIORITY_RULES: dict[str, OccupationSeniorityRules] = {}


def get_seniority_rules(occupation_title: str) -> OccupationSeniorityRules:
    return OCCUPATION_SENIORITY_RULES.get(
        occupation_title.strip().lower(), DEFAULT_OCCUPATION_SENIORITY_RULES
    )


# Requires the word "experience" to appear in the same sentence as the years
# figure (same-sentence = no "." or newline in between), within a short
# window that tolerates filler words ("years of *related* experience"). This
# is what keeps a minimum-age line like "Be at least 21 years of age" from
# being misread as 21 years of experience -- confirmed as a real failure mode
# by the preliminary seniority probe (see seniority_probe.py) against live
# truck-driving ads, where "age" requirements sit right next to the real,
# much-lower "1 year of related experience" line.
_YEARS_EXPERIENCE_PATTERN = re.compile(
    r"(\d+)\s*(?:\+|-\s*(\d+))?\s*years?[^.\n]{0,45}?\bexperience\b",
    re.IGNORECASE,
)


def extract_years_of_experience(text: str | None) -> int | None:
    """Extract the largest stated years-of-experience requirement in the text.

    Ads often mention experience more than once at different thresholds (e.g.
    "2+ years of experience required, 5+ years of experience preferred") --
    the max mentioned figure is treated as the binding requirement.
    """
    if not text:
        return None

    candidates: list[int] = []
    for match in _YEARS_EXPERIENCE_PATTERN.finditer(text):
        candidates.extend(int(group) for group in match.groups() if group)

    return max(candidates) if candidates else None


def classify_title_seniority(title: str | None, rules: OccupationSeniorityRules) -> str | None:
    """Match title keywords in senior -> entry -> mid order (most to least reliable)."""
    if not title:
        return None
    lowered = f" {title.lower()} "
    if any(keyword in lowered for keyword in rules.senior_title_keywords):
        return "senior"
    if any(keyword in lowered for keyword in rules.entry_title_keywords):
        return "entry"
    if any(keyword in lowered for keyword in rules.mid_title_keywords):
        return "mid"
    return None


def classify_yoe_seniority(years: int | None, rules: OccupationSeniorityRules) -> str | None:
    if years is None:
        return None
    if years <= rules.yoe_low_max:
        return "entry"
    if years >= rules.yoe_high_min:
        return "senior"
    return "mid"


def classify_seniority(
    *,
    title: str | None,
    description: str | None,
    occupation_title: str,
) -> dict[str, object]:
    """Classify one ad into entry/mid/senior/unclassified.

    Title keywords are checked first (the stronger signal when an occupation
    has a real title ladder, e.g. Paralegal I/II/III). Years-of-experience
    parsed from the ad body is the fallback -- the only signal available for
    occupations whose titles don't vary at all (e.g. "Teacher").

    Returns a dict rather than a bare label so the reasoning is auditable:
    which signal decided it, and what years-of-experience was actually found.
    """
    rules = get_seniority_rules(occupation_title)
    years = extract_years_of_experience(description)

    title_tier = classify_title_seniority(title, rules)
    if title_tier is not None:
        return {"seniority_tier": title_tier, "seniority_source": "title", "years_of_experience": years}

    yoe_tier = classify_yoe_seniority(years, rules)
    if yoe_tier is not None:
        return {"seniority_tier": yoe_tier, "seniority_source": "years_of_experience", "years_of_experience": years}

    return {"seniority_tier": "unclassified", "seniority_source": None, "years_of_experience": years}


def classify_seniority_df(
    df: pl.DataFrame,
    *,
    occupation_title_column: str = "query_occupation",
    title_column: str = "title",
    description_column: str = "description",
) -> pl.DataFrame:
    """Add seniority_tier / seniority_source / years_of_experience columns."""
    if df.is_empty():
        return df.with_columns(
            pl.Series("seniority_tier", [], dtype=pl.Utf8),
            pl.Series("seniority_source", [], dtype=pl.Utf8),
            pl.Series("years_of_experience", [], dtype=pl.Int64),
        )

    tiers: list[str] = []
    sources: list[str | None] = []
    years: list[int | None] = []
    rows = df.select([occupation_title_column, title_column, description_column]).iter_rows(named=True)
    for row in rows:
        result = classify_seniority(
            title=row[title_column],
            description=row[description_column],
            occupation_title=row[occupation_title_column],
        )
        tiers.append(result["seniority_tier"])
        sources.append(result["seniority_source"])
        years.append(result["years_of_experience"])

    return df.with_columns(
        pl.Series("seniority_tier", tiers),
        pl.Series("seniority_source", sources),
        pl.Series("years_of_experience", years, dtype=pl.Int64),
    )
