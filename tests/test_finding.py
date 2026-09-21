"""The output model: a chain of comparable facts, and a short list of findings.

Gleipnir parses sources into operational, comparable knowledge and lets the
client conclude. These tests pin the places where that could silently stop being
true.
"""
from datetime import date

import pytest

from gleipnir.finding import (
    AMBER_QUALIFIERS, ChainFact, Colour, Comparator, Finding, RedGround, Screen,
)


def red(**kw):
    base = dict(colour=Colour.RED, ground=RedGround.DESIGNATION,
                statement="Sole owner appears on the EU Consolidated List",
                authority="EU Council Official Journal", citation="(UE) 2026/1356",
                recorded_on=date(2026, 6, 15))
    base.update(kw)
    return Finding(**base)


# ── red is narrow, and needs a ground ───────────────────────────────────────

def test_structure_alone_is_never_red():
    """Chain termination in a transit jurisdiction is the strongest structural
    signal there is. It is still not involvement."""
    with pytest.raises(ValueError, match="structure alone is never red"):
        Finding(Colour.RED, None, "chain terminates in a transit jurisdiction")


def test_an_uncitable_red_is_refused():
    with pytest.raises(ValueError, match="uncitable red is an accusation"):
        red(citation="")
    with pytest.raises(ValueError, match="uncitable red is an accusation"):
        red(authority="")


def test_red_attaches_to_the_entity_itself_not_to_a_distant_party():
    """Involvement recorded against someone two hops away is that party's
    finding. Legal facts propagate by defined rules; allegations do not
    propagate at all."""
    with pytest.raises(ValueError, match="red attaches to the entity itself"):
        red(subject_hops=2)
    assert red(subject_hops=0).colour is Colour.RED


def test_the_grounds_are_a_closed_set():
    assert set(RedGround) == {
        RedGround.DESIGNATION, RedGround.ADJUDICATED_FRAUD,
        RedGround.COUNTERPARTY_LITIGATION, RedGround.PUBLIC_SUPPORT_FOR_AGGRESSOR}


def test_amber_must_say_why_it_is_not_adjudicated():
    with pytest.raises(ValueError, match="amber needs a qualifier"):
        Finding(Colour.AMBER, RedGround.ADJUDICATED_FRAUD, "reported in media")
    f = Finding(Colour.AMBER, RedGround.ADJUDICATED_FRAUD, "reported in media",
                qualifier="reported")
    assert f.qualifier in AMBER_QUALIFIERS


def test_a_name_match_is_amber_not_red():
    """Corporate nodes match by name, and no verdict has resolved it."""
    f = Finding(Colour.AMBER, RedGround.DESIGNATION,
                "Holder name matches a designated entity",
                qualifier="unadjudicated_name_match")
    assert f.colour is Colour.AMBER


# ── chain facts are uncoloured, and carry their comparator ──────────────────

def test_a_chain_fact_has_no_colour_field():
    assert not hasattr(ChainFact(predicate="p", statement="s"), "colour")


def test_a_fact_without_a_comparator_says_so_rather_than_implying_rarity():
    f = ChainFact(predicate="mail_jurisdiction", statement="mail resolves to RU")
    assert "no measured comparator" in f.comparator.line()


def test_a_measured_fact_arrives_with_both_populations():
    """The fact only becomes information alongside its denominators."""
    f = ChainFact(
        predicate="mail_jurisdiction", value="RU",
        statement="Mail infrastructure resolves to RU",
        comparator=Comparator(ordinary=0.0, ordinary_n=237, contrast=0.635,
                              contrast_n=701, contrast_label="designated entities"))
    line = f.comparator.line()
    assert "0.0% of ordinary Danish companies" in line
    assert "63.5% of designated entities" in line
    assert "n=237" in line and "n=701" in line


def test_a_conditioned_rate_names_its_confounder():
    """An unconditioned rate measures whatever structural variable drives it —
    sub-threshold aggregation is 0.0% at one owner and 91.4% at six."""
    c = Comparator(ordinary=0.914, ordinary_n=510, conditioned_on="6+ owners")
    assert "conditioned on 6+ owners" in c.line()


# ── the screen's colour ─────────────────────────────────────────────────────

def test_a_screen_with_only_chain_facts_is_green():
    """Eleven unusual structural facts and no recorded involvement is not red.
    Volume of structure must never become colour."""
    s = Screen(cvr="99000147", name="Acme ApS", as_of=date(2026, 8, 27),
               chain=[ChainFact(predicate=f"p{i}", statement="s") for i in range(11)])
    assert s.colour is Colour.GREEN


def test_one_red_finding_colours_the_screen():
    s = Screen(cvr="1", name="X", as_of=date(2026, 8, 27), findings=[red()])
    assert s.colour is Colour.RED


def test_amber_does_not_become_red_by_accumulation():
    ambers = [Finding(Colour.AMBER, RedGround.DESIGNATION, f"m{i}",
                      qualifier="reported") for i in range(9)]
    s = Screen(cvr="1", name="X", as_of=date(2026, 8, 27), findings=ambers)
    assert s.colour is Colour.AMBER


def test_an_unanswerable_screen_is_grey_not_green():
    """The most common dishonesty in this field is rendering 'could not check'
    as clean — but only a limit that blocked a core question counts."""
    s = Screen(cvr="1", name="X", as_of=date(2026, 8, 27),
               unknowable=["ownership register not filed"],
               blocked_on=["ownership register not filed"])
    assert s.colour is Colour.GREY


def test_grey_yields_to_a_real_finding():
    s = Screen(cvr="1", name="X", as_of=date(2026, 8, 27), findings=[red()],
               unknowable=["accounts not filed"], blocked_on=["accounts not filed"])
    assert s.colour is Colour.RED


def test_a_peripheral_limit_does_not_grey_the_screen():
    """A company that never filed a signing rule is not unscreenable. Colouring
    every missing field grey makes an ordinary company look unanswerable."""
    s = Screen(cvr="1", name="X", as_of=date(2026, 8, 27),
               unknowable=["signing_rule_changed: TEGNINGSREGEL not filed"])
    assert s.colour is Colour.GREEN


def test_a_material_limit_greys_the_screen():
    """Not being able to check designations at all is a different statement."""
    s = Screen(cvr="1", name="X", as_of=date(2026, 8, 27),
               unknowable=["designated_in_chain: no designation list loaded"],
               blocked_on=["designated_in_chain: no designation list loaded"])
    assert s.colour is Colour.GREY
