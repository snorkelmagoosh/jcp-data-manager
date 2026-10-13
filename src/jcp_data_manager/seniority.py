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

DECIDED (2026-10-13, Tanner): occupations with no clearly-defined tier
structure, or where real ads are too thin to populate three tiers, don't need
to be swapped out or forced through a classifier -- all of that occupation's
ads can be lumped into a single bucket instead of split entry/mid/senior. See
LUMPED_TIER_OCCUPATIONS below.

CORRECTED (2026-10-13, same day): Tanner separately confirmed he wants
treatment x tier pooled across occupations into one global coefficient, not
interacted per-occupation. That changes how lumping has to work -- lumped
ads are tagged "undifferentiated", a tier value distinct from entry/mid/
senior, rather than reusing one of those three labels. "Undifferentiated"
is excluded from the pooled entry/mid/senior heterogeneity comparison (same
treatment as "unclassified" below) but still counts toward the main/overall
treatment effect, and can be reported as its own occupation-specific effect
separately. This keeps the pooled entry/mid/senior coefficients clean --
built from occupations with a real tier structure only -- while still using
every lumped occupation's ads. The original "reuse an existing label"
choice (reusing "entry") was made before Tanner's pooling preference was
confirmed and is no longer safe under pooling: a lumped occupation's full
sample sitting inside the real "entry" bucket would have meant the pooled
entry coefficient partly measured "this occupation in general" rather than
a genuine cross-occupation entry-level effect.

OPEN, UNRESOLVED (as of 2026-09-29): whether to add JobSpy's own `job_level`
field (LinkedIn's entry/mid-senior/associate/director/executive taxonomy) as
a third-priority fallback below title and years-of-experience. It's real
signal -- ~54% filled across a 1,101-ad sample, and would rescue roughly half
of currently-unclassified ads, disproportionately helping Truck Driver, which
has no working title signal at all. But two things make this a real decision,
not a simple addition: (1) confirmed via a direct site-by-site check that
`job_level` is LinkedIn-only (100% filled for LinkedIn results, 0% for
Indeed in the same pull) -- using it would mean whichever ads get rescued
from "unclassified" are disproportionately LinkedIn-sourced, a new selection
effect on top of the market-size/employer-formality ones already being
tracked elsewhere in this design; (2) it doesn't just extend coverage, it
sometimes disagrees with our own title-based tier where both exist (~41%
disagreement within ads job_level calls "entry level" that we also
independently tier). If this does get built, the recommendation on the table
is: title -> years-of-experience -> job_level (as a distinct
seniority_source value, not folded into "title") -> unclassified, so the
platform-specific, coarser signal never overrides the hand-vetted,
occupation-specific one, and stays auditable in analysis.
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
    # Added 9/29 after a targeted search for entry-vocabulary gaps across
    # ~1,150 real titles. Unambiguous across white-collar occupations (unlike
    # "staff"/"manager"), so kept as a shared default rather than an
    # occupation-specific override.
    "new grad",
    "new graduate",
    "graduate program",
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


def _rules(
    *,
    extra_entry: tuple[str, ...] = (),
    extra_mid: tuple[str, ...] = (),
    extra_senior: tuple[str, ...] = (),
    remove_entry: tuple[str, ...] = (),
    remove_mid: tuple[str, ...] = (),
    remove_senior: tuple[str, ...] = (),
    yoe_low_max: int = DEFAULT_YOE_LOW_MAX,
    yoe_high_min: int = DEFAULT_YOE_HIGH_MIN,
) -> OccupationSeniorityRules:
    """Build an override that starts from the shared defaults and adjusts them.

    Occupations almost always want the generic ladder (senior/lead/manag/...)
    plus a couple of occupation-specific additions, not a from-scratch list --
    this keeps each override to just its actual deltas from the defaults,
    which is what's diffed against real ad titles in seniority_probe.py
    output. `remove_*` exists for the rarer case where a default keyword is
    actively wrong for one occupation (see Food Service Manager below, where
    the default "manag" senior stem collides with the occupation's own name).
    """

    def _apply(defaults: tuple[str, ...], extra: tuple[str, ...], remove: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(keyword for keyword in defaults if keyword not in remove) + extra

    return OccupationSeniorityRules(
        entry_title_keywords=_apply(DEFAULT_ENTRY_TITLE_KEYWORDS, extra_entry, remove_entry),
        mid_title_keywords=_apply(DEFAULT_MID_TITLE_KEYWORDS, extra_mid, remove_mid),
        senior_title_keywords=_apply(DEFAULT_SENIOR_TITLE_KEYWORDS, extra_senior, remove_senior),
        yoe_low_max=yoe_low_max,
        yoe_high_min=yoe_high_min,
    )


# Numbered-tier keywords are written with explicit padding (" ii " rather than
# "ii") because classify_title_seniority matches on plain substring
# containment, not word boundaries -- an unpadded "i" would match inside
# nearly every title ("administrative", "financial", ...). The padding
# requires the roman numeral to appear as its own space-delimited token,
# matching how classify_title_seniority itself pads the compared title.
_ROMAN_I = " i "
_ROMAN_II = " ii "
_ROMAN_III = " iii "
_ROMAN_IV = " iv "

# Built from the 2026-09-25 title-token survey (see seniority_probe.py) --
# live ads pulled per occupation, not guessed. Confidence varies by
# occupation; see the research log / session notes for the underlying
# evidence behind each one. Each rules object is built once and reused for
# its panel-naming alias (e.g. "loan officer" / "credit counselor") rather
# than rebuilt, so the two names can never silently drift apart later.

# High confidence: Apprentice -> Journeyman -> Master is an external,
# licensing-based trade convention, not employer-specific guesswork.
# "Apprentice" and "Master" are already in the shared defaults; only
# "journeyman" (the middle rung) needed adding.
_ELECTRICIAN_RULES = _rules(extra_mid=("journeyman",))

# High confidence for named keywords; numbering not trusted (see
# "financial analyst" below for why). "Staff" is the opposite convention
# from accounting -- "Staff Software Engineer" is senior, not entry.
# yoe_high_min raised from the shared default of 5 to 7: the 9/29
# YOE-vs-title-tier calibration check found a median of 6.5 stated years
# among real ads independently title-tagged "senior" (n=10) -- consistent
# with tech's well-known convention of wanting more years at "senior" than
# other fields. Every other occupation's calibration sample was too thin
# (n<=7, several n=1) to justify a similar occupation-specific change.
_SOFTWARE_DEVELOPER_RULES = _rules(extra_senior=("staff",), yoe_high_min=7)

# High confidence. "Loan Officer Assistant" is a real, clearly lower-tier
# role; the default senior/entry keywords already cover "Senior"/"Sr. Loan
# Officer" correctly. ("Manager"/"Director" hits in the survey were mostly a
# different job family -- Relationship Manager -- pulled in by search-term
# overlap, a sourcing precision issue, not a seniority-tagging one.)
_LOAN_OFFICER_RULES = _rules(extra_entry=("assistant",))

# Medium-high confidence, but needed a real fix, not just an addition: the
# occupation's own base title ("Manager") collides with the shared "manag"
# senior stem, which would tag the dominant plain "Restaurant Manager"/
# "General Manager" titles (57/120 in the survey) as senior by default.
# Removed "manag" from senior and added bare "manager" as a MID keyword
# instead, so the baseline role reads as mid, "Assistant Manager" (checked
# first, since entry beats mid) reads as entry, and only an explicit
# higher-scope qualifier ("director") reads as senior. "Lead"/"supervisor"
# hits in the survey looked like contamination from adjacent grocery/retail
# search results, not real Food Service Manager titles -- left as default
# for now pending a cleaner sample.
_FOOD_SERVICE_MANAGER_RULES = _rules(
    extra_entry=("assistant",),
    extra_mid=("manager",),
    remove_senior=("manag",),
)

# High confidence for the "Staff Accountant" = entry trap (opposite of
# tech's "Staff Engineer" = senior). Numbered suffixes ("Senior Accountant
# II") only ever appeared paired with a named tier in the survey, never
# standalone with a confirmed meaning -- left unencoded rather than guessed,
# consistent with the survey's "medium, not confirmed" rating.
_ACCOUNTANT_RULES = _rules(extra_entry=("staff",))

# Medium confidence. "Legal Assistant" is a real, lower-tier role distinct
# from "Paralegal" (already established before this survey). Numbered
# "Paralegal I/II" appeared only once each in the survey -- thinner
# same-employer evidence than Registered Nurse's clinical ladder below -- so
# only II/III are encoded here, not I, and this is the override most worth
# re-checking against a larger sample later.
_PARALEGAL_RULES = _rules(extra_entry=("assistant",), extra_mid=(_ROMAN_II,), extra_senior=(_ROMAN_III,))

# High confidence: RN I/II/III/IV is a well-documented, standard hospital
# clinical-ladder convention (not one employer's internal scheme), so
# trusted here unlike Truck Driver's or Financial Analyst's numbering.
# "Manager"/"Supervisor" hits in the survey are a separate nursing
# leadership track, not this clinical ladder -- already covered correctly by
# the shared defaults, no change needed for those.
_REGISTERED_NURSE_RULES = _rules(
    extra_entry=(_ROMAN_I,),
    extra_mid=(_ROMAN_II,),
    extra_senior=(_ROMAN_III, _ROMAN_IV),
)

# Medium-high confidence. One posting in the survey directly self-confirmed
# the numbering: "Senior Administrative Assistant (Administrative Support
# Assistant III)" pairs "Senior" with "III" in the same title. "I" was left
# unencoded (unlike Registered Nurse) since nothing in the survey confirmed
# it means entry here specifically. "Executive Assistant" is treated as a
# senior variant (supports higher-level staff, generally more experience/
# discretion) rather than a separate occupation.
_ADMINISTRATIVE_ASSISTANT_RULES = _rules(
    extra_mid=(_ROMAN_II,),
    extra_senior=("executive", _ROMAN_III, _ROMAN_IV),
)

OCCUPATION_SENIORITY_RULES: dict[str, OccupationSeniorityRules] = {
    "electrician": _ELECTRICIAN_RULES,
    "software developer": _SOFTWARE_DEVELOPER_RULES,
    "loan officer": _LOAN_OFFICER_RULES,
    "credit counselor": _LOAN_OFFICER_RULES,
    "food service manager": _FOOD_SERVICE_MANAGER_RULES,
    "accountant": _ACCOUNTANT_RULES,
    "auditor": _ACCOUNTANT_RULES,
    "paralegal": _PARALEGAL_RULES,
    "legal assistant": _PARALEGAL_RULES,
    "registered nurse": _REGISTERED_NURSE_RULES,
    "administrative assistant": _ADMINISTRATIVE_ASSISTANT_RULES,
    "secretary": _ADMINISTRATIVE_ASSISTANT_RULES,
}
# Deliberately left on DEFAULT_OCCUPATION_SENIORITY_RULES (no override, named
# keywords only where applicable) -- not an oversight:
#   - "truck driver": see LUMPED_TIER_OCCUPATIONS above -- as of 2026-10-13
#     it no longer goes through this ladder at all, lumped into one tier
#     instead. Left here since get_seniority_rules() is still reachable for
#     it in principle (the lumped check in classify_seniority() runs first).
#     Original finding: the 9/25 investigation found "DRIVER I/II" numbering
#     tracks CDL license class and vehicle type, not tenure (same-employer
#     paired postings had overlapping, non-monotonic experience requirements
#     and an inverted pay ceiling). No seniority signal to encode.
#   - "financial analyst": the shared default keywords already classified the
#     survey sample correctly (associate/intern -> entry, senior/lead/manag/
#     principal -> senior) with no changes needed. Numbered suffixes
#     ("Financial Analyst II") are contradictory across employers in the
#     survey itself -- new-grad entry-level at one, paired with "Senior" at
#     another -- so, as with Truck Driver, left unencoded rather than guessed.


# Occupations that don't get an entry/mid/senior split at all -- every ad
# is lumped into UNDIFFERENTIATED_TIER (not one of the real three tiers),
# regardless of its title or stated years of experience. Decided 2026-10-13
# (Tanner): rather than swap these occupations out or build a new classifier
# for them, it's fine for them not to have jobs in every tier -- lump them
# into one bucket instead.
#   - "truck driver": no real tier signal exists at all (9/25 finding --
#     "DRIVER I/II" tracks CDL class/vehicle type, not tenure).
#   - "loan officer" / "credit counselor": real signal exists (title-based
#     classification wasn't contaminated -- see title_filters.py's 10/8
#     audit) but at near-zero volume -- 0/6 occupation x tier x market-class
#     cells passed the >=5-ad threshold in the round-3 probe, 4/6 were
#     literal zero-ad cells.
# Uses a dedicated tier value rather than reusing "entry" -- Tanner wants
# treatment x tier pooled across occupations, not interacted per-occupation
# (confirmed 2026-10-13, after this lumping decision), so a lumped
# occupation's full sample can't sit inside a real tier label without
# distorting that tier's pooled coefficient for every other occupation. See
# the module docstring for how "undifferentiated" is meant to be used.
UNDIFFERENTIATED_TIER = "undifferentiated"

LUMPED_TIER_OCCUPATIONS: dict[str, str] = {
    "truck driver": UNDIFFERENTIATED_TIER,
    "loan officer": UNDIFFERENTIATED_TIER,
    "credit counselor": UNDIFFERENTIATED_TIER,
}


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

# No individual candidate requirement realistically exceeds this. Exists to
# reject company-history/marketing boilerplate that otherwise matches the
# same pattern as a real requirement -- confirmed as a real failure mode by
# the 9/29 YOE-vs-title-tier calibration check, which turned up "over 100
# years of battery experience," "thrived for more than 120 years... customer
# experience," "more than 135 years of financial experience," "built on more
# than 50 years of experience," "over 70 years of experience advising," and
# (at the first, more permissive cap of 40 this constant used) "over 40 years
# of experience in the ho[spitality industry]" -- all company/industry legacy
# claims, all sitting in the same ad as (and pulling the max() above) a real,
# much lower candidate requirement like "3-5 years." Set well above the
# largest genuine individual requirement seen across the calibration pull (14,
# for a senior Software Developer posting) and well below the smallest
# confirmed-bogus one (40), rather than right at the boundary.
MAX_PLAUSIBLE_YEARS_OF_EXPERIENCE = 25


def extract_years_of_experience(text: str | None) -> int | None:
    """Extract the largest plausible years-of-experience requirement in the text.

    Ads often mention experience more than once at different thresholds (e.g.
    "2+ years of experience required, 5+ years of experience preferred") --
    the max mentioned figure is treated as the binding requirement, after
    dropping any implausibly large match (see MAX_PLAUSIBLE_YEARS_OF_EXPERIENCE).
    """
    if not text:
        return None

    candidates: list[int] = []
    for match in _YEARS_EXPERIENCE_PATTERN.finditer(text):
        candidates.extend(
            int(group)
            for group in match.groups()
            if group and int(group) <= MAX_PLAUSIBLE_YEARS_OF_EXPERIENCE
        )

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
    """Classify one ad into entry/mid/senior/unclassified/undifferentiated.

    Title keywords are checked first (the stronger signal when an occupation
    has a real title ladder, e.g. Paralegal I/II/III). Years-of-experience
    parsed from the ad body is the fallback -- the only signal available for
    occupations whose titles don't vary at all (e.g. "Teacher"). Occupations
    in LUMPED_TIER_OCCUPATIONS skip both and always return "undifferentiated"
    -- not to be confused with "unclassified" (extraction failed for an
    occupation that otherwise has a real tier structure).

    Returns a dict rather than a bare label so the reasoning is auditable:
    which signal decided it, and what years-of-experience was actually found.
    """
    years = extract_years_of_experience(description)

    lumped_tier = LUMPED_TIER_OCCUPATIONS.get(occupation_title.strip().lower())
    if lumped_tier is not None:
        return {"seniority_tier": lumped_tier, "seniority_source": "lumped", "years_of_experience": years}

    rules = get_seniority_rules(occupation_title)
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
