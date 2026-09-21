"""The outcome backtest, and the hindsight it was built to keep out.

`docs/loop-log.md` iteration 1 measured the predicates against eight companies
picked from coverage of their own collapse, and the entry records three defects
that made the numbers worthless: the cohort was selected on the outcome, the
predicates were evaluated on documents that already contained the collapse, and
the controls were unmatched.

Every test here pins one of those three shut. They are regression tests in the
literal sense — each one failed against the code as it stood.
"""
from datetime import date

import pytest

from gleipnir.backtest import (LABEL_PREDICATES, Subject, Tally, age_band,
                               clean_controls, directorships_at, eligible,
                               is_adverse, katz_ci, match_pairs, owner_band,
                               subject_from_hit)
from gleipnir.claims import Claim, EntityRef, EpistemicTier, Predicate
from gleipnir.extract.cvr import extract_company
from gleipnir.predicates.core import V
from gleipnir.predicates.cvr_only import (insolvency_status,
                                          registered_audit_election_absent)

CO = EntityRef(kind="company", key="12345678")


def own(holder, frm, to=None):
    return Claim(subject=EntityRef("company", holder, holder), predicate=Predicate.OWNS,
                 object=CO, source_id="cvr",
                 epistemic_tier=EpistemicTier.SELF_DECLARED_TO_REGISTRY,
                 raw_ref="r", valid_from=frm, valid_to=to, qualifiers={})


def doc(status_history, *, sammensat="NORMAL", founded="2010-01-01",
        form="Anpartsselskab", attributter=None, cvr=12345678):
    return {"_source": {"Vrvirksomhed": {
        "cvrNummer": cvr,
        "attributter": attributter or [],
        "virksomhedsstatus": [
            {"status": s, "periode": {"gyldigFra": f, "gyldigTil": t}}
            for s, f, t in status_history],
        "virksomhedMetadata": {
            "sammensatStatus": sammensat, "stiftelsesDato": founded,
            "nyesteNavn": {"navn": "TEST ApS"},
            "nyesteVirksomhedsform": {"langBeskrivelse": form}},
    }}}


# ── 1. selection on the outcome ─────────────────────────────────────────────

def test_the_label_predicate_is_named_and_never_rated():
    """`insolvency_or_dissolution` scored 59x in iteration 1 purely because the
    cohort was chosen for having failed. It is excluded by name, not caveated."""
    assert "insolvency_or_dissolution" in LABEL_PREDICATES


def test_voluntary_liquidation_and_merger_are_not_adverse_outcomes():
    """Counting every non-NORMAL end as a red flag would put companies that were
    acquired, merged or wound up solvently into the positive cohort."""
    assert is_adverse("UNDER TVANGSOPLØSNING")
    assert is_adverse("OPLØST EFTER KONKURS")
    assert not is_adverse("OPLØST EFTER FUSION")
    assert not is_adverse("UNDER FRIVILLIG LIKVIDATION")
    assert not is_adverse("NORMAL")
    assert not is_adverse(None)


def test_a_recovered_failure_is_not_a_control():
    """`sammensatStatus = NORMAL` today does not mean never failed: a company
    struck off and later reinstated reads NORMAL. Leaving it in the control arm
    puts genuine positives there and biases every ratio toward 1."""
    recovered = subject_from_hit(
        doc([("NORMAL", "2010-01-01", "2015-01-01"),
             ("UNDER TVANGSOPLØSNING", "2015-01-02", "2016-01-01"),
             ("NORMAL", "2016-01-02", None)]), "control", "blob")
    never = subject_from_hit(doc([("NORMAL", "2010-01-01", None)]), "control", "blob")
    kept, dropped = clean_controls([recovered, never])
    assert [s.cvr for s in kept] == [never.cvr]
    assert dropped == 1


# ── 2. hindsight ────────────────────────────────────────────────────────────

def test_composite_status_is_dated_from_the_current_status_period():
    """`sammensatStatus` is filed with no period, so `claims.at()` admitted it at
    every date. A screen of Example Bank A/S as of 2010 reported `UNDERKONKURS` a
    year before the bank failed."""
    claims = extract_company(
        doc([("NORMAL", "1906-02-27", "2011-02-06"),
             ("UNDER KONKURS", "2011-02-07", None)], sammensat="UNDERKONKURS"),
        raw_ref="r", observed_at="2026-08-28")
    composite = [c for c in claims if c.qualifiers.get("composite")]
    assert len(composite) == 1
    assert composite[0].valid_from == date(2011, 2, 7)


def test_insolvency_predicate_honours_the_as_of_date():
    """It accepted `on` and scanned every claim regardless — the defect class
    `docs/determinism-audit.md` found in five other predicates."""
    claims = extract_company(
        doc([("NORMAL", "1906-02-27", "2011-02-06"),
             ("UNDER KONKURS", "2011-02-07", None)], sammensat="UNDERKONKURS"),
        raw_ref="r", observed_at="2026-08-28")
    assert insolvency_status(claims, date(2010, 2, 1)).value is V.UNKNOWABLE
    after = insolvency_status(claims, date(2025, 1, 1))
    assert after.value is V.TRUE and after.raw == "UNDERKONKURS"


def test_a_case_already_flagged_at_the_as_of_date_is_not_a_prediction():
    subject = subject_from_hit(
        doc([("NORMAL", "2010-01-01", "2020-01-01"),
             ("UNDER TVANGSOPLØSNING", "2020-01-02", None)]), "case", "blob")
    assert eligible(subject, date(2019, 1, 2))
    assert not eligible(subject, date(2020, 6, 1))
    assert not eligible(subject, date(2009, 1, 1))       # did not yet exist


def test_directorship_counts_honour_role_periods():
    """`calibration.build_directorship_index` ignores periods, which is right for
    a present-day base rate and hindsight in a backtest: it would credit a 2024
    appointment to a 2016 as-of date."""
    intervals = {"p1": [("111", date(2010, 1, 1), date(2012, 1, 1)),
                        ("222", date(2020, 1, 1), None)]}
    assert directorships_at(intervals, date(2011, 1, 1)) == {"p1": 1}
    assert directorships_at(intervals, date(2015, 1, 1)) == {}
    assert directorships_at(intervals, date(2021, 1, 1)) == {"p1": 1}


# ── 3. unmatched controls ───────────────────────────────────────────────────

def test_matching_is_deterministic_and_without_replacement():
    cases = [subject_from_hit(doc([("NORMAL", "2010-01-01", "2020-01-01"),
                                   ("UNDER KONKURS", "2020-01-02", None)], cvr=c),
                              "case", "b") for c in (11111111, 22222222)]
    controls = [subject_from_hit(doc([("NORMAL", "2010-01-01", None)], cvr=c),
                                 "control", "b") for c in (33333333, 44444444)]
    first, _ = match_pairs(cases, controls, 365, None, ("form", "age", "owners"))
    again, _ = match_pairs(cases, controls, 365, None, ("form", "age", "owners"))
    assert [(p.case.cvr, p.control.cvr) for p in first] == \
           [(p.case.cvr, p.control.cvr) for p in again]
    assert len({p.control.cvr for p in first}) == len(first)


def test_the_control_is_read_at_the_cases_own_as_of_date():
    """Otherwise a rate difference can come from the two cohorts being read in
    different years rather than from the outcome."""
    case = subject_from_hit(doc([("NORMAL", "2010-01-01", "2020-01-01"),
                                 ("UNDER KONKURS", "2020-01-02", None)]), "case", "b")
    control = subject_from_hit(doc([("NORMAL", "2010-01-01", None)], cvr=87654321),
                               "control", "b")
    pairs, _ = match_pairs([case], [control], 365, None, ())
    assert pairs[0].as_of == date(2019, 1, 2)


def test_owner_count_is_read_at_the_as_of_date_not_today():
    subject = subject_from_hit(doc([("NORMAL", "2010-01-01", None)]), "control", "b")
    subject.claims = [own("A", date(2012, 1, 1), date(2014, 1, 1)),
                      own("B", date(2018, 1, 1), None)]
    assert subject.owners_at(date(2013, 1, 1)) == 1
    assert subject.owners_at(date(2016, 1, 1)) == 0
    assert subject.owners_at(date(2019, 1, 1)) == 1


# ── counting ────────────────────────────────────────────────────────────────

def test_unknowable_is_kept_out_of_the_denominator():
    """Iteration 1 bug B3. Counting it as evaluated deflates every rate;
    counting it as FALSE reports a coverage hole as a clean check."""
    tally = Tally()
    for value in (V.TRUE, V.FALSE, V.UNKNOWN, V.UNKNOWABLE, V.UNKNOWABLE):
        tally.add(value)
    assert tally.evaluable == 3
    assert tally.rate == pytest.approx(1 / 3)


def test_no_interval_is_invented_from_a_zero_cell():
    """Iteration 1 bug B4: Laplace smoothing printed 4.56 for a predicate that
    never fired in either cohort."""
    assert katz_ci(0, 100, 5, 100) is None
    assert katz_ci(5, 100, 0, 100) is None
    lo, hi = katz_ci(20, 100, 10, 100)
    assert lo < 2.0 < hi


def test_bands_are_closed_and_capped():
    assert age_band(None) == "?" and age_band(0) == "0-2" and age_band(99) == "21+"
    assert owner_band(0) == 0 and owner_band(3) == 3 and owner_band(40) == 6


# ── the predicate the backtest produced ─────────────────────────────────────

def test_absent_audit_election_is_a_fact_about_the_subject_not_a_coverage_hole():
    """It began as an `UNKNOWABLE` row in the coverage section — where the
    largest separation in the whole test was sitting unread. `UNKNOWABLE`
    asserts the source cannot cover it; the source covers it for 99.9% of live
    Danish companies, so absence is a fact about the company."""
    absent = extract_company(doc([("NORMAL", "2010-01-01", None)]),
                             raw_ref="r", observed_at="2026-08-28")
    result = registered_audit_election_absent(absent, date(2020, 1, 1))
    assert result.value is V.TRUE
    assert "99.9%" in result.evidence

    present = extract_company(
        doc([("NORMAL", "2010-01-01", None)], attributter=[{
            "type": "REVISION_FRAVALGT", "vaerdier": [
                {"vaerdi": "true", "periode": {"gyldigFra": "2010-01-01",
                                               "gyldigTil": None}}]}]),
        raw_ref="r", observed_at="2026-08-28")
    assert registered_audit_election_absent(present, date(2020, 1, 1)).value is V.FALSE


def test_an_election_filed_after_the_as_of_date_does_not_count_as_present():
    claims = extract_company(
        doc([("NORMAL", "2010-01-01", None)], attributter=[{
            "type": "REVISION_FRAVALGT", "vaerdier": [
                {"vaerdi": "true", "periode": {"gyldigFra": "2022-01-01",
                                               "gyldigTil": None}}]}]),
        raw_ref="r", observed_at="2026-08-28")
    assert registered_audit_election_absent(claims, date(2020, 1, 1)).value is V.TRUE
    assert registered_audit_election_absent(claims, date(2023, 1, 1)).value is V.FALSE


def test_a_company_barred_from_waiving_audit_is_not_reported_as_withholding():
    """Example Bank A/S carries BØRSNOTERET and TILSYN_KATEGORI. A listed bank under
    supervision may not waive audit, so no election exists to file. Read as of
    2010-02-07 the predicate reported its absence as an established fact about
    the bank; it is a fact about Danish company law."""
    listed = extract_company(
        doc([("NORMAL", "1906-02-27", None)], attributter=[
            {"type": "BØRSNOTERET", "vaerdier": [
                {"vaerdi": "true", "periode": {"gyldigFra": "1906-02-27",
                                               "gyldigTil": None}}]}]),
        raw_ref="r", observed_at="2026-08-28")
    result = registered_audit_election_absent(listed, date(2010, 2, 7))
    assert result.value is V.UNKNOWABLE
    assert "may not be waived" in result.evidence


# ── the leak the question generator found ───────────────────────────────────

def test_a_role_with_no_function_period_does_not_apply_to_every_date():
    """`medlemsData` carries no `periode` of its own — the dates are one level
    down, on each FUNKTION value. `_period(member)` returned (None, None) for
    100% of `has_role` claims, and `claims.at()` admits an undated claim at any
    as-of date, so a 2024 appointment was visible to a 2016 screen."""
    claims = extract_company(_role_doc("2020-01-01", None), raw_ref="r",
                             observed_at="2026-08-28")
    roles = [c for c in claims if c.predicate is Predicate.HAS_ROLE]
    assert [c.valid_from for c in roles] == [date(2020, 1, 1)]
    from gleipnir.claims import at
    assert at(claims, Predicate.HAS_ROLE, date(2016, 1, 1)) == []
    assert len(at(claims, Predicate.HAS_ROLE, date(2021, 1, 1))) == 1


def test_the_membership_period_bounds_the_function_period():
    """A board seat recorded as ending 2023-04-01 whose FUNKTION row is still
    open ended in 2023. Taking only the inner period reported a departed chair
    as sitting."""
    claims = extract_company(
        _role_doc("2021-06-07", None, member_period=("2021-06-07", "2023-04-01")),
        raw_ref="r", observed_at="2026-08-28")
    role = next(c for c in claims if c.predicate is Predicate.HAS_ROLE)
    assert role.valid_from == date(2021, 6, 7)
    assert role.valid_to == date(2023, 4, 1)


def test_one_dated_role_per_function_not_one_undated_role_per_membership():
    claims = extract_company(
        _role_doc("2018-01-01", "2020-01-01", second=("FORMAND", "2020-01-02", None)),
        raw_ref="r", observed_at="2026-08-28")
    roles = sorted((c for c in claims if c.predicate is Predicate.HAS_ROLE),
                   key=lambda c: c.valid_from)
    assert [c.qualifiers["function"] for c in roles] == ["BESTYRELSESMEDLEM", "FORMAND"]
    assert roles[0].valid_to == date(2020, 1, 1)


def _role_doc(frm, to, member_period=None, second=None):
    values = [{"vaerdi": "BESTYRELSESMEDLEM",
               "periode": {"gyldigFra": frm, "gyldigTil": to}}]
    if second:
        values.append({"vaerdi": second[0],
                       "periode": {"gyldigFra": second[1], "gyldigTil": second[2]}})
    member = {"attributter": [{"type": "FUNKTION", "vaerdier": values}]}
    if member_period:
        member["periode"] = {"gyldigFra": member_period[0], "gyldigTil": member_period[1]}
    d = doc([("NORMAL", "2010-01-01", None)])
    d["_source"]["Vrvirksomhed"]["deltagerRelation"] = [{
        "deltager": {"enhedsNummer": 333, "enhedstype": "PERSON",
                     "navne": [{"navn": "Board Member"}]},
        "organisationer": [{"hovedtype": "LEDELSESORGAN",
                            "organisationsNavn": [{"navn": "Bestyrelse"}],
                            "medlemsData": [member]}]}]
    return d


def test_the_generator_refuses_a_fact_the_register_did_not_date():
    """789 of 2,126 bankrupt companies showed a liquidator in office a year
    before bankruptcy. All 862 hits carried `periode: {null, null}` — the
    liquidator was appointed BY the bankruptcy."""
    import sys
    sys.path.insert(0, "scripts")
    from questions import _in_force, _rateable
    assert not _in_force({"periode": {"gyldigFra": None, "gyldigTil": None}},
                         date(2020, 1, 1))
    assert _in_force({"periode": {"gyldigFra": "2019-01-01", "gyldigTil": None}},
                     date(2020, 1, 1))
    assert not _in_force({"periode": {"gyldigFra": "2021-01-01", "gyldigTil": None}},
                         date(2020, 1, 1))
    # A specific first-accounting-period date took 9 of the first 14 rows.
    assert not _rateable("2018-11-01")
    assert _rateable("false")
