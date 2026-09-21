"""Measured denominators, and the reference data derived from them.

Everything here was measured on 2026-08-27 from a 25,000-company CVR scan
(25 API requests). Numbers, not estimates — `docs/predicate-selection.md`
refuses to admit a predicate without a denominator, and these are the first.

**Sample caveat, stated because it changes how the numbers should be read.**
The scan sorted by `cvrNummer` ascending and took the first 25 pages, so the
sample spans CVR 10000025–25177479: the *oldest* active Danish companies, not a
random draw. Old companies skew toward more directorship history, more ownership
events, and larger corporate groups. Treat these as denominators for an
old-company cohort until a spread sample confirms them.
"""
from __future__ import annotations

from decimal import Decimal

SCAN_DATE = "2026-08-27"
SCAN_COMPANIES = 25_000
SCAN_WITH_REGISTER = 24_100
SCAN_PEOPLE = 115_734
SCAN_FILED_SHARES = 35_071

#: Directorships (distinct companies) per natural person. The distribution
#: `poc.md` §7 assumed was trivially available and which exists in no published
#: statistic — Companies House cannot produce one because of unresolved
#: duplicate director identities.
DIRECTORSHIP_PERCENTILES: dict[int, float] = {
    1: 0.7684, 2: 0.9065, 3: 0.9496, 4: 0.9698, 5: 0.9802,
    10: 0.9954, 15: 0.9984, 20: 0.9991, 25: 0.9994, 30: 0.9996,
}
DIRECTORSHIP_MAX_OBSERVED = 2708

#: **A rare person attribute is not a rare company attribute.** Measured over
#: 49,000 companies: a person holding 20+ directorships is in the top 0.09% of
#: people — and 22.4% of *companies* have such a director, because a person with
#: many directorships is attached to many companies by definition. Screening on
#: the person-level percentile flags a quarter of Denmark.
#:
#: The cause is concentration. Exactly 12 enhedsNummer values hold 200+
#: directorships each, and between them they sit on **6,747 companies (13.8%)**.
#: They behave like corporate infrastructure — company-service providers acting
#: as director-of-record for mass-incorporated entities — not like nominees
#: concealing a beneficial owner. The top one holds 2,177 `Direktion` roles and
#: 233 `Stiftere` roles across companies that are all active.
#:
#: Excluding them restores the signal:
#:
#:     threshold   incl. infrastructure   excl.
#:            10                 29.87%   18.19%
#:            20                 22.42%    8.54%
#:            50                 18.38%    3.31%
#:           100                 16.54%    0.94%
INFRASTRUCTURE_MIN_DIRECTORSHIPS = 200
NOMINEE_DENSITY_THRESHOLD = 100
NOMINEE_DENSITY_BASE_RATE = 0.0094          # company-level, infrastructure excluded
NOMINEE_DENSITY_BASE_RATE_UNFILTERED = 0.1654

#: Filed ownership percentages are not a continuous distribution. 99.9% of
#: 35,071 filed values fall on one of these ~12 round numbers, and the buckets
#: immediately below every legal threshold are **empty**:
#:
#:     [0.25-0.02, 0.25)  ->      0 of 35,071
#:     [0.3333-0.02, ⅓)   ->      2
#:     [0.50-0.02, 0.50)  ->      0
#:     [0.6667-0.02, ⅔)   ->      0
#:
#: This kills the bunching estimator proposed in `predicate-selection.md`, and
#: with it `stake_just_below_threshold`, which read 0/79 not because its window
#: was wrong but because nothing is ever in the window. It inverts into a
#: better predicate: a NON-round filed percentage is itself rare.
COMMON_SHARES: dict[str, float] = {
    "1.0": 0.515, "0.5": 0.125, "0.3333": 0.070, "0.1": 0.067, "0.25": 0.056,
    "0.05": 0.045, "0.2": 0.038, "0.6667": 0.031, "0.15": 0.030, "0.9": 0.015,
    "0.0": 0.007,
}
ROUND_SHARES: frozenset[Decimal] = frozenset(Decimal(s) for s in COMMON_SHARES)
#: Share of all filed percentages landing on a value outside ROUND_SHARES.
UNUSUAL_SHARE_BASE_RATE = 0.0006

#: Measured base rates, as fractions of companies WITH a filed ownership register.
#: Stratified across the CVR range (49,000 companies, two scans). The first scan
#: sampled the oldest companies only; the stratified re-run shifted every rate
#: down modestly — the largest move was `owner_not_cvr_and_not_person`, 5.86% to
#: 4.27%. Directions held, so the earlier conclusions stand.
BASE_RATES: dict[str, float] = {
    "voting_exceeds_equity": 0.0290,
    "voting_exceeds_equity_by_10pp": 0.0252,
    "subthreshold_aggregate_over_50": 0.0626,
    "majority_owner_unresolvable": 0.0394,
    "unusual_ownership_percentage": 0.0001,
    "ownership_residual_unaccounted": 0.1363,
    "nominee_density": NOMINEE_DENSITY_BASE_RATE,
}

#: Observed / expected-if-independent, over 39,806 companies with a register.
#: **Predicates are not independent, so base rates must never be multiplied.**
#: Doing so would rebuild MYCIN's certainty-factor error with measured evidence
#: that it is wrong.
CO_OCCURRENCE_LIFT: dict[tuple[str, str], float] = {
    ("voting_exceeds_equity", "subthreshold_aggregate_over_50"): 7.7,
    ("voting_exceeds_equity", "ownership_residual_unaccounted"): 6.1,
    ("subthreshold_aggregate_over_50", "ownership_residual_unaccounted"): 4.9,
    ("nominee_density", "majority_owner_unresolvable"): 2.0,
    ("nominee_density", "ownership_residual_unaccounted"): 1.1,
    # Anti-correlated, and therefore the most useful thing to stack: a single
    # unresolvable majority holder leaves no room for aggregation or residual.
    ("majority_owner_unresolvable", "ownership_residual_unaccounted"): 0.4,
    ("voting_exceeds_equity", "majority_owner_unresolvable"): 0.2,
    ("majority_owner_unresolvable", "subthreshold_aggregate_over_50"): 0.0,
}

#: How many predicates fire at once, over the same 39,806 companies.
PREDICATE_COUNT_DISTRIBUTION: dict[int, float] = {
    0: 0.6342, 1: 0.2646, 2: 0.0767, 3: 0.0217, 4: 0.0028,
}


def build_directorship_index(store, tag: str = "calib") -> dict[str, int]:
    """person enhedsNummer -> distinct companies where they hold a management role.

    Built from the bulk pages, so it is a **lower bound**: the scan covered
    25,000 of Denmark's several hundred thousand active companies, and a person
    is counted only in the ones sampled. A count of 25 here means *at least* 25.
    That direction is safe — it can only understate nominee density, never
    invent it.
    """
    from collections import defaultdict

    seen_pages: set[str] = set()
    per_person: dict[str, set[str]] = defaultdict(set)
    for f in store.fetches():
        if (f.source != "cvr" or f.resource_type != "bulk_page"
                or not f.resource_id.startswith(tag) or f.resource_id in seen_pages):
            continue
        seen_pages.add(f.resource_id)
        for hit in store.get_json(f.content_hash):
            v = hit["_source"]["Vrvirksomhed"]
            cvr = str(v.get("cvrNummer"))
            for rel in v.get("deltagerRelation") or []:
                d = rel.get("deltager") or {}
                if d.get("enhedstype") != "PERSON":
                    continue
                for org in rel.get("organisationer") or []:
                    if org.get("hovedtype") == "LEDELSESORGAN":
                        per_person[str(d.get("enhedsNummer"))].add(cvr)
    return {k: len(v) for k, v in per_person.items()}


# ─────────────────────────────────────────────────────────────────────────────
# Stratified base rates — 39,806 UNIQUE companies, 2026-08-27
#
# Deduplicated: the stratified scan's first four starting points fell inside the
# ascending scan's range, so 8,000 of 49,000 rows were the same company twice.
#
# **Owner count is a confounder large enough to invalidate an unconditioned
# rate.** Mean predicates firing by owner count: 1 -> 0.08, 2 -> 0.49, 3 -> 1.45,
# 6+ -> 1.88. Two predicates turn out to be near-deterministic functions of it.
# ─────────────────────────────────────────────────────────────────────────────

#: {owner-count bucket (6 = six or more): {predicate: rate}}
STRATIFIED_RATES: dict[int, dict[str, float]] = {
    1: {"n": 30344, "nominee_density": 0.010, "voting_exceeds_equity": 0.002,
        "majority_owner_unresolvable": 0.049, "unusual_ownership_percentage": 0.000,
        "subthreshold_aggregate_over_50": 0.000, "ownership_residual_unaccounted": 0.013},
    2: {"n": 5641, "nominee_density": 0.008, "voting_exceeds_equity": 0.054,
        "majority_owner_unresolvable": 0.014, "unusual_ownership_percentage": 0.000,
        "subthreshold_aggregate_over_50": 0.015, "ownership_residual_unaccounted": 0.391},
    3: {"n": 1964, "nominee_density": 0.014, "voting_exceeds_equity": 0.195,
        "majority_owner_unresolvable": 0.004, "unusual_ownership_percentage": 0.001,
        "subthreshold_aggregate_over_50": 0.518, "ownership_residual_unaccounted": 0.711},
    4: {"n": 965, "nominee_density": 0.010, "voting_exceeds_equity": 0.255,
        "majority_owner_unresolvable": 0.004, "unusual_ownership_percentage": 0.001,
        "subthreshold_aggregate_over_50": 0.659, "ownership_residual_unaccounted": 0.747},
    5: {"n": 382, "nominee_density": 0.013, "voting_exceeds_equity": 0.238,
        "majority_owner_unresolvable": 0.000, "unusual_ownership_percentage": 0.000,
        "subthreshold_aggregate_over_50": 0.759, "ownership_residual_unaccounted": 0.762},
    6: {"n": 510, "nominee_density": 0.002, "voting_exceeds_equity": 0.137,
        "majority_owner_unresolvable": 0.002, "unusual_ownership_percentage": 0.000,
        "subthreshold_aggregate_over_50": 0.914, "ownership_residual_unaccounted": 0.822},
}

#: Predicates whose rate is a near-deterministic function of owner count, and
#: which therefore carry no information beyond "this cap table has N owners".
#: Both are STRUCTURAL, not evidential. They may appear in a report as context;
#: they may never contribute to a finding.
#:
#:     subthreshold_aggregate_over_50   1 owner 0.0%  ->  6+ owners 91.4%
#:     ownership_residual_unaccounted   1 owner 1.3%  ->  6+ owners 82.2%
#:
#: This also explains iteration 5's 4.9x co-occurrence lift between them: they
#: are two measurements of the same underlying variable.
STRUCTURAL_ONLY: frozenset[str] = frozenset({
    "subthreshold_aggregate_over_50",
    "ownership_residual_unaccounted",
})

#: Flat across every owner-count stratum (1.0 / 0.8 / 1.4 / 1.0 / 1.3 / 0.2 %),
#: i.e. orthogonal to cap-table size — the property that makes a predicate
#: evidential rather than structural. `majority_owner_unresolvable` is also
#: orthogonal, but inversely: 4.9% at one owner falling to 0.2-0.4% at three or
#: more, and anti-correlated with every other predicate (see CO_OCCURRENCE_LIFT).
OWNER_COUNT_INDEPENDENT: frozenset[str] = frozenset({
    "nominee_density",
    "majority_owner_unresolvable",
})


def stratum_rate(predicate: str, owners: int) -> float | None:
    """Base rate for a predicate conditioned on the subject's owner count.

    An unconditioned rate is the wrong comparator for anything owner-dependent:
    `subthreshold_aggregate_over_50` at 6.3% overall is 0.0% for a single-owner
    company and 91.4% for one with six.
    """
    return STRATIFIED_RATES.get(min(max(owners, 1), 6), {}).get(predicate)


# ─────────────────────────────────────────────────────────────────────────────
# Outcome backtest — 2026-08-28. The first denominators in this project that
# are conditioned on an OUTCOME rather than on the population.
#
# 12,000 companies in 24 requests (`scripts/cohort_scan.py`), one field
# projection for all three cohorts, sampled at the same eight `search_after`
# points across the CVR range. Every predicate evaluated at `event - lead` on
# the register as it stood then, against controls matched 1:1 on legal form,
# age band and owner count and evaluated at the SAME calendar date
# (`scripts/backtest.py`).
#
# **What this cohort is.** Companies the registrar struck off (forced
# dissolution) or that went bankrupt. That is an administrative and economic
# outcome, NOT concealed ownership — `docs/loop-log.md` iteration 1 made
# exactly this substitution and the entry records why it invalidated the
# result. These rates say what the booleans predict; they do not say the
# booleans detect what `threat-model.md` is about.
# ─────────────────────────────────────────────────────────────────────────────

OUTCOME_SCAN_DATE = "2026-08-28"
OUTCOME_COHORT_N = {"control": 4000, "bankruptcy": 4000, "forced-dissolution": 4000}

#: {predicate: {outcome: (case_true, case_n, control_true, control_n, ratio, lo, hi)}}
#: Matched arm only, lead 365 days. Predicates absent from a cohort's dict did
#: not clear both the pre-registered LR floor of 1.5 and an interval excluding 1.
OUTCOME_LR_365: dict[str, dict[str, tuple[int, int, int, int, float, float, float]]] = {
    "ownership_register_events": {
        "bankruptcy": (912, 1975, 563, 1976, 1.62, 1.49, 1.76)},
    "signing_rule_changed": {
        "bankruptcy": (349, 2077, 210, 2042, 1.63, 1.39, 1.92)},
    "majority_owner_unresolvable": {
        "bankruptcy": (68, 1975, 35, 1975, 1.94, 1.30, 2.91)},
    "audit_waived": {
        "forced-dissolution": (437, 622, 477, 1166, 1.72, 1.58, 1.87)},
    # Listed and supervised companies are suppressed: they may not waive audit,
    # so no election is filed and its absence carries no information. Example Bank A/S
    # — listed, under Finanstilsynet supervision — was being reported as having
    # withheld a filing Danish law forbade it from making. 14 subjects across
    # both cohorts, so the correction is for the individual screen, not the rate.
    "registered_audit_election_absent": {
        "forced-dissolution": (561, 1183, 17, 1180, 32.92, 20.46, 52.97),
        "bankruptcy": (106, 2120, 2, 2118, 52.95, 13.09, 214.22)},
    # Not authored. `scripts/questions.py` enumerated every dated filing in the
    # register, ranked what separates matched pairs under a Bonferroni
    # correction, and this was the top surviving row for bankruptcy. Nobody had
    # thought to look for it. Not established for forced dissolution — 5 of
    # 1,183 against 1, interval [0.59, 42.73] — which is counter-intuitive and
    # recorded rather than smoothed.
    "dissolution_threat_on_file": {
        "bankruptcy": (25, 2126, 5, 2126, 5.00, 1.92, 13.04)},
}

#: Ratio at 1, 3 and 5 years before the transition, matched arm. A predicate
#: whose ratio is flat across the sweep is a durable property; one that decays
#: toward 1 as the lead grows is reading the run-up, not the company.
OUTCOME_LEAD_SWEEP: dict[str, dict[str, dict[int, float]]] = {
    "ownership_register_events": {"bankruptcy": {365: 1.62, 1095: 1.53, 1825: 1.43}},
    "signing_rule_changed": {"bankruptcy": {365: 1.63, 1095: 1.57, 1825: 1.37}},
    "majority_owner_unresolvable": {"bankruptcy": {365: 1.94, 1095: 1.22, 1825: 1.25}},
    "audit_waived": {"forced-dissolution": {365: 1.72, 1095: 1.69, 1825: 1.62}},
    "registered_audit_election_absent": {
        "forced-dissolution": {365: 32.92, 1095: 22.93, 1825: 33.19},
        "bankruptcy": {365: 52.95, 1095: 15.51, 1825: 35.02}},
    "dissolution_threat_on_file": {
        "bankruptcy": {365: 5.00, 1095: 3.17, 1825: 4.75}},
}

#: Rejected on measurement, with the reason. Kept because a predicate that was
#: tested and failed is evidence, and rediscovering it costs another 24 requests.
OUTCOME_REJECTED: dict[str, str] = {
    "capital_change_same_day_as_ownership":
        "1.59 unmatched -> 0.93 matched (bankruptcy), 1.73 -> 0.89 (forced "
        "dissolution). The whole apparent signal was legal form and age.",
    "voting_exceeds_equity":
        "0.59 (bankruptcy) and 0.50 (forced dissolution) — fires LESS on the "
        "outcome cohorts than on matched controls.",
    "nominee_density":
        "0 of 2,126 and 0 of 1,183. At threshold 100 with infrastructure "
        "excluded it cannot fire on a 53,000-company index; not evaluated.",
    "subthreshold_aggregate_over_50":
        "0 of 1,975. Matching on owner count removes the variable it measures, "
        "which is the point of iteration 6's finding that it IS that variable.",
    "unusual_ownership_percentage":
        "1 of 2,245 unmatched, 0 matched. Population rate 0.01% — this corpus "
        "is two orders of magnitude too small to rate it.",
}

#: Presence of the audit-election attribute in the whole cohort's CURRENT
#: document, unmatched — the check that rules out document completeness as the
#: explanation. Every neighbouring attribute is present at ~99.9% in all three:
#: FORMÅL 100.0 / 99.9 / 99.9, TEGNINGSREGEL 100.0 / 99.9 / 99.9,
#: KAPITAL 98.4 / 99.1 / 97.8. Mean distinct attribute types per company is
#: 13.15 / 13.30 / 12.17. Only this one attribute separates them.
AUDIT_ELECTION_ON_FILE: dict[str, float] = {
    "control": 0.999, "bankruptcy": 0.861, "forced-dissolution": 0.338,
}
