"""The rule layer, and the one property it exists to keep.

The predicates are fodder. `finding.py` already refuses to let a fact become a
colour; what was missing was the table saying what WOULD colour it, and where
the model's remit begins and ends.
"""
import pytest

from gleipnir.finding import AMBER_QUALIFIERS, Colour
from gleipnir.flags import (RULES, UNGOVERNED, Disposition, agenda, apply,
                            rule_for)
from gleipnir.predicates.core import Result, Tier, V
from gleipnir.predicates.cvr_only import ALL_CVR_ONLY


def res(predicate, value=V.TRUE, raw=None, evidence=""):
    return Result(predicate, value, Tier.C, raw=raw, evidence=evidence)


def test_no_rule_may_raise_red():
    """Six iterations produced not one predicate that constitutes direct
    involvement. A red rule here would be exactly the drift `RedGround` is
    closed against."""
    assert [r.predicate for r in RULES if r.colour is Colour.RED] == []


def test_every_raising_rule_carries_a_ground_and_a_qualifier():
    for rule in RULES:
        if rule.disposition is not Disposition.RAISE:
            continue
        assert rule.ground is not None, rule.predicate
        assert rule.colour is not None, rule.predicate
        if rule.colour is Colour.AMBER:
            assert rule.qualifier in AMBER_QUALIFIERS, rule.predicate


def test_every_escalation_names_a_source_a_question_and_what_would_change():
    """All three, or the row is not a task — it is a shrug with a source
    attached, and the model cannot act on it."""
    for rule in RULES:
        if rule.disposition is not Disposition.ESCALATE:
            continue
        assert rule.source and rule.question and rule.would_change, rule.predicate


def test_every_rated_fact_says_why_it_may_never_colour():
    for rule in RULES:
        if rule.disposition is Disposition.FACT:
            assert rule.holds_as, rule.predicate


def test_the_table_has_one_row_per_predicate_and_value():
    keys = [(r.predicate, r.on) for r in RULES]
    assert len(keys) == len(set(keys))


def test_a_chain_stopped_by_structure_and_one_stopped_by_our_budget_differ():
    """Same predicate, opposite statements. Conflating them reports a spending
    decision as a coverage limit."""
    by_structure = rule_for(res("chain_terminates_unresolvable", V.TRUE))
    by_budget = rule_for(res("chain_terminates_unresolvable", V.UNKNOWN))
    assert by_structure.source == "gleif"
    assert by_budget.source == "cvr"
    assert by_structure.question != by_budget.question


def test_false_and_unknowable_never_reach_the_table():
    """A predicate that came back clean, or that could not be computed, is not
    a trigger. Routing UNKNOWABLE here would colour a coverage hole."""
    governed = apply([res("majority_owner_unresolvable", V.FALSE),
                      res("majority_owner_unresolvable", V.UNKNOWABLE)])
    assert all(not rows for rows in governed.values())


def test_a_predicate_the_table_does_not_cover_is_surfaced_not_dropped():
    governed = apply([res("some_new_predicate", V.TRUE)])
    assert [r.predicate for _, r in governed[UNGOVERNED]] == ["some_new_predicate"]


def test_every_shipped_predicate_has_a_rule_for_the_values_it_can_return():
    """A boolean with no row is a gap in the table. This is the test that fails
    when a predicate is added and the rule layer is not."""
    covered = {r.predicate for r in RULES}
    missing = sorted({p.__name__ for p in ALL_CVR_ONLY} - covered - {
        # function names that differ from the predicate name they emit
        "subthreshold_aggregation", "ownership_residual", "signing_rule_changes",
        "same_day_capital_and_ownership_change", "ownership_events",
        "insolvency_status", "registered_audit_election_absent",
        "unusual_ownership_percentage", "voting_exceeds_equity",
        "majority_owner_unresolvable", "audit_waived",
    })
    assert missing == []


def test_the_agenda_is_the_whole_of_the_models_remit():
    """The model answers these questions. It does not decide what they mean —
    the rule does, and the rule is in this file."""
    tasks = agenda([res("majority_owner_unresolvable", V.TRUE),
                    res("registered_audit_election_absent", V.TRUE),
                    res("ownership_register_events", V.TRUE)])
    assert tasks == [
        "gleif: does this holder have an active LEI, and a Level 2 parent?",
        "regnskaber: has this company ever filed an annual report?",
    ]


def test_a_designation_name_match_raises_amber_never_red():
    """It is a NAME match against a pinned list. Amber with
    `unadjudicated_name_match` is the strongest thing it can be."""
    rule = rule_for(res("designated_holder_candidate", V.TRUE))
    assert rule.colour is Colour.AMBER
    assert rule.ground.value == "designation_or_watchlist"
    assert rule.qualifier == "unadjudicated_name_match"


def test_apply_is_deterministic():
    rows = [res("ownership_register_events"), res("majority_owner_unresolvable"),
            res("audit_waived"), res("signing_rule_changed")]
    first = {d: [r.predicate for _, r in v] for d, v in apply(rows).items()}
    second = {d: [r.predicate for _, r in v] for d, v in apply(rows[::-1]).items()}
    assert first == second
