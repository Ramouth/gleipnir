"""Regression tests for the determinism audit (docs/determinism-audit.md).

Each pins a defect that was reproduced empirically and produced a wrong or
unstable verdict over identical inputs.
"""
from datetime import date
from decimal import Decimal

import pytest

from gleipnir.claims import Claim, EntityRef, EpistemicTier, Predicate
from gleipnir.predicates.core import V
from gleipnir.predicates.cvr_only import (
    ALL_CVR_ONLY, MIN_DIVERGENCE, THRESHOLDS, audit_waived, ownership_residual,
    stake_just_below_threshold, voting_exceeds_equity,
)

CO = EntityRef(kind="company", key="99000147")
AS_OF = date(2026, 8, 27)


def own(holder, share, register="EJERREGISTER", frm=date(2020, 1, 1), to=None,
        pred=Predicate.OWNS):
    return Claim(subject=EntityRef(kind="company", key=holder), predicate=pred,
                 object=CO, source_id="cvr",
                 epistemic_tier=EpistemicTier.SELF_DECLARED_TO_REGISTRY,
                 raw_ref="r", valid_from=frm, valid_to=to,
                 qualifiers={"share": Decimal(share), "register": register})


def attr(pred, value, frm, to=None):
    return Claim(subject=CO, predicate=pred, object=value, source_id="cvr",
                 epistemic_tier=EpistemicTier.REGISTERED, raw_ref="r",
                 valid_from=frm, valid_to=to)


def test_every_predicate_requires_an_explicit_as_of():
    """Finding 1/2/3: `on = on or date.today()` made verdicts move with the
    wall clock and with the machine's timezone."""
    for p in ALL_CVR_ONLY:
        with pytest.raises(TypeError):
            p([])


def test_thresholds_are_decimal_and_the_window_is_exact():
    """Finding 11: the float window was 0.02 wide at five thresholds and
    0.0199999999999999998 at two."""
    for t in THRESHOLDS:
        assert isinstance(t, Decimal)
        assert (t - Decimal("0.02")) + Decimal("0.02") == t


def test_share_exactly_two_points_below_hits_at_every_threshold():
    for t in THRESHOLDS:
        r = stake_just_below_threshold([own("h", str(t - Decimal("0.02")))], AS_OF)
        assert r.value is V.TRUE, f"missed at threshold {t}"


def test_tightest_gap_is_reported_not_the_first_listed():
    """Finding 10: `hits[0]` cited whichever holder the JSON listed first, so
    the raw quantity used for recalibration was arbitrary."""
    a = stake_just_below_threshold([own("aaa", "0.24"), own("zzz", "0.499")], AS_OF)
    b = stake_just_below_threshold([own("zzz", "0.499"), own("aaa", "0.24")], AS_OF)
    assert a.raw == b.raw == "0.4990 vs 0.5000"


def test_duplicate_holder_is_not_collapsed_by_array_order():
    """Finding 8: one enhedsNummer filed in both registers with different
    percentages had the conflict resolved by JSON order."""
    claims = [own("h", "0.30", "EJERREGISTER"), own("h", "0.60", "REELLE EJERE")]
    a = ownership_residual(claims, AS_OF)
    b = ownership_residual(list(reversed(claims)), AS_OF)
    assert a.raw == b.raw
    assert a.detail["conflicted_holders"] == ["h"]


def test_residual_is_independent_of_how_the_cap_table_was_split():
    """Finding 19: 0.99985 as one holding was FALSE; the same total split in
    two was TRUE, because two roundings stacked on a float sum."""
    one = ownership_residual([own("a", "0.99985")], AS_OF)
    two = ownership_residual([own("a", "0.70"), own("b", "0.29985")], AS_OF)
    assert one.raw == two.raw
    assert one.value is two.value


def test_tiny_divergence_does_not_become_a_finding_of_zero_magnitude():
    """Finding 13: a 1e-7 divergence passed a 1e-9 gate and was reported as
    TRUE with raw='max +0.0pp'."""
    claims = [own("h", "0.5000"),
              own("h", "0.50001", pred=Predicate.HAS_VOTING_RIGHTS)]
    r = voting_exceeds_equity(claims, AS_OF)
    assert r.value is V.FALSE
    assert Decimal("0.00001") < MIN_DIVERGENCE


def test_real_divergence_is_still_caught():
    claims = [own("h", "0.49"),
              own("h", "0.75", pred=Predicate.HAS_VOTING_RIGHTS)]
    r = voting_exceeds_equity(claims, AS_OF)
    assert r.value is V.TRUE and r.raw == "max +0.2600"


def test_expired_version_is_not_reported_as_current():
    """Finding 7: `_history` ignored valid_to, so a version that closed in 2022
    was still reported as the current value in 2026."""
    claims = [attr(Predicate.AUDIT_WAIVED, "true", date(2021, 1, 1), date(2022, 1, 1)),
              attr(Predicate.AUDIT_WAIVED, "false", date(2022, 1, 2))]
    assert audit_waived(claims, AS_OF).value is V.FALSE


def test_same_day_versions_do_not_flip_with_array_order():
    """Finding 5: two versions dated the same day produced opposite verdicts
    depending on JSON order, because Python's sort is stable."""
    claims = [attr(Predicate.AUDIT_WAIVED, "false", date(2021, 1, 1)),
              attr(Predicate.AUDIT_WAIVED, "true", date(2021, 1, 1))]
    assert (audit_waived(claims, AS_OF).value
            is audit_waived(list(reversed(claims)), AS_OF).value)


def test_as_of_actually_filters_history():
    """Finding 4: five predicates accepted `on` and silently ignored it."""
    claims = [attr(Predicate.AUDIT_WAIVED, "true", date(2021, 1, 1), date(2025, 1, 1)),
              attr(Predicate.AUDIT_WAIVED, "false", date(2025, 1, 2))]
    assert audit_waived(claims, date(2023, 6, 1)).value is V.TRUE
    assert audit_waived(claims, date(2026, 6, 1)).value is V.FALSE


def test_unusual_percentage_is_measured_rarity_not_a_chosen_threshold():
    """Replaces stake_just_below_threshold, which measured a phenomenon that
    does not occur: the 2-point bucket below every legal threshold held 0, 2, 0
    and 0 of 35,071 filed percentages."""
    from gleipnir.predicates.cvr_only import unusual_ownership_percentage
    assert unusual_ownership_percentage([own("h", "0.50")], AS_OF).value is V.FALSE
    assert unusual_ownership_percentage([own("h", "0.3333")], AS_OF).value is V.FALSE
    r = unusual_ownership_percentage([own("h", "0.4900")], AS_OF)
    assert r.value is V.TRUE and r.raw == "0.4900"


def test_majority_owner_unresolvable_distinguishes_majority_from_minority():
    from gleipnir.predicates.cvr_only import majority_owner_unresolvable
    minority = majority_owner_unresolvable([own("anden:1", "0.30"), own("h", "0.70")], AS_OF)
    assert minority.value is V.FALSE and "none holding a majority" in minority.evidence
    major = majority_owner_unresolvable([own("anden:1", "0.70")], AS_OF)
    assert major.value is V.TRUE


def test_unresolvable_finding_does_not_claim_the_owner_is_foreign():
    """ANDEN_DELTAGER covers partnerships, estates, foundations and trusts as
    well as foreign entities. The field does not say which, and the finding
    must not either."""
    from gleipnir.predicates.cvr_only import majority_owner_unresolvable
    r = majority_owner_unresolvable([own("anden:1", "0.90")], AS_OF)
    assert "foreign" not in r.evidence.lower()
    assert "not determinable" in r.evidence


def test_nominee_density_excludes_mass_incorporation_infrastructure():
    """12 enhedsNummer values hold 200+ directorships each and sit on 13.8% of
    all Danish companies. Counting them made this fire on 16-22% of Denmark."""
    from gleipnir.calibration import INFRASTRUCTURE_MIN_DIRECTORSHIPS
    from gleipnir.claims import Claim, EntityRef, EpistemicTier
    from gleipnir.predicates.cvr_only import nominee_density

    def role(pkey):
        return Claim(subject=CO, predicate=Predicate.HAS_ROLE,
                     object=EntityRef("person", pkey, f"P{pkey}"), source_id="cvr",
                     epistemic_tier=EpistemicTier.REGISTERED, raw_ref="r",
                     valid_from=date(2020, 1, 1), qualifiers={"role": "Direktion"})

    infra = {"p1": INFRASTRUCTURE_MIN_DIRECTORSHIPS + 500}
    assert nominee_density([role("p1")], infra, AS_OF).value is V.UNKNOWN
    real = {"p1": 150}
    assert nominee_density([role("p1")], real, AS_OF).value is V.TRUE


def test_structural_predicates_report_their_stratum_base_rate():
    """A finding that fires on 91% of six-owner companies must say so in its own
    evidence line. Owner count is a large enough confounder that an
    unconditioned rate is the wrong comparator."""
    from gleipnir.calibration import STRUCTURAL_ONLY, stratum_rate
    from gleipnir.predicates.cvr_only import subthreshold_aggregation
    assert stratum_rate("subthreshold_aggregate_over_50", 1) == 0.0
    assert stratum_rate("subthreshold_aggregate_over_50", 6) == 0.914
    assert stratum_rate("subthreshold_aggregate_over_50", 99) == 0.914
    r = subthreshold_aggregation(
        [own("a", "0.34"), own("b", "0.33"), own("c", "0.33")], AS_OF)
    assert "52%" in r.evidence
    assert "subthreshold_aggregate_over_50" in STRUCTURAL_ONLY


def test_owner_count_independent_predicates_are_flat_across_strata():
    """nominee_density is 1.0/0.8/1.4/1.0/1.3/0.2% across owner-count strata —
    orthogonal to cap-table size, which is what makes it evidential rather than
    structural."""
    from gleipnir.calibration import OWNER_COUNT_INDEPENDENT, stratum_rate
    rates = [stratum_rate("nominee_density", n) for n in range(1, 7)]
    assert max(rates) - min(rates) < 0.02
    assert "nominee_density" in OWNER_COUNT_INDEPENDENT
