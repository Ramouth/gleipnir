"""The roster is enforced, not documented.

`agents.py` exists because the same invariant was written in four docstrings and
none of them could be read by a test. These are that test. The point is not that
the current table is correct — it is that a future table cannot quietly stop
being correct, because widening a grant here fails a check rather than a review.
"""
import dataclasses

import pytest

from gleipnir.agents import (
    MODEL_FORBIDDEN, PIPELINE, ROLES, Autonomy, Capability, CapabilityError, Role,
    check_invariants, holders, render, require, role,
)


def test_the_shipped_roster_is_consistent():
    check_invariants()


@pytest.mark.parametrize("name", sorted(ROLES))
def test_every_role_states_what_it_is_for(name):
    r = ROLES[name]
    assert r.mandate and r.module and r.consumes and r.produces and r.halts_on
    assert r.capabilities, "a role that may write nothing is not a role"


@pytest.mark.parametrize("name", sorted(ROLES))
def test_a_denial_carries_its_reason(name):
    """A grant without a stated denial invites the next call site to widen it."""
    r = ROLES[name]
    for cap, why in r.forbidden:
        assert not r.may(cap), f"{name} both holds and forbids {cap}"
        assert why, f"{name} forbids {cap} without saying why"


@pytest.mark.parametrize("name", [n for n, r in ROLES.items()
                                  if r.autonomy is Autonomy.MODEL])
def test_no_model_may_conclude(name):
    """The invariant `oracle.py`, `flags.py` and `investigation.py` each state
    separately: a model proposes evidence, and does nothing else."""
    assert not (ROLES[name].capabilities & MODEL_FORBIDDEN)


def test_exactly_one_role_colours_a_company():
    assert holders(Capability.ASSIGN_COLOUR) == ("rule",)
    assert holders(Capability.RAISE_FINDING) == ("rule",)
    assert ROLES["rule"].autonomy is Autonomy.DETERMINISTIC


def test_exactly_one_role_spends_registry_quota():
    """Deciding and spending are separate: the planner derives the agenda and
    cannot act on it, so the agenda is auditable before quota is burned."""
    assert holders(Capability.SPEND_QUOTA) == ("acquirer",)
    assert not ROLES["planner"].may(Capability.SPEND_QUOTA)


def test_only_people_suppress_or_review():
    for cap in (Capability.SUPPRESS, Capability.RESOLVE_REVIEW):
        for name in holders(cap):
            assert ROLES[name].autonomy is Autonomy.HUMAN


def test_a_model_role_that_could_conclude_fails_the_check(monkeypatch):
    """The check has to catch a *new* role, not just re-assert the current one."""
    rogue = Role(
        name="summariser", autonomy=Autonomy.MODEL, module="nowhere",
        mandate="read the chain and say what it means",
        capabilities=frozenset({Capability.ASSIGN_COLOUR}),
        consumes="everything", produces="a verdict", halts_on="never")
    monkeypatch.setitem(ROLES, "summariser", rogue)
    monkeypatch.setattr("gleipnir.agents.PIPELINE", PIPELINE + ("summariser",))
    with pytest.raises(CapabilityError, match="may propose evidence and nothing else"):
        check_invariants()


def test_a_second_colouring_role_fails_the_check(monkeypatch):
    widened = dataclasses.replace(
        ROLES["extractor"],
        capabilities=ROLES["extractor"].capabilities | {Capability.ASSIGN_COLOUR})
    monkeypatch.setitem(ROLES, "extractor", widened)
    with pytest.raises(CapabilityError, match="exactly one role may assign colour"):
        check_invariants()


def test_a_capability_nobody_holds_is_a_design_hole(monkeypatch):
    stripped = dataclasses.replace(
        ROLES["acquirer"],
        capabilities=ROLES["acquirer"].capabilities - {Capability.SPEND_QUOTA})
    monkeypatch.setitem(ROLES, "acquirer", stripped)
    with pytest.raises(CapabilityError, match="capabilities nobody holds"):
        check_invariants()


def test_pipeline_and_roster_cannot_disagree(monkeypatch):
    monkeypatch.setattr("gleipnir.agents.PIPELINE", PIPELINE[:-1])
    with pytest.raises(CapabilityError, match="disagree about which roles exist"):
        check_invariants()


def test_require_guards_a_call_site():
    require("acquirer", Capability.SPEND_QUOTA)
    with pytest.raises(CapabilityError, match="may not emit_claim"):
        require("acquirer", Capability.EMIT_CLAIM)
    with pytest.raises(CapabilityError, match="no such role"):
        role("summariser")


def test_render_names_every_role_and_its_denials():
    text = render()
    for name, r in ROLES.items():
        assert name.upper() in text
        for cap, _ in r.forbidden:
            assert f"may not  {cap.value}" in text
