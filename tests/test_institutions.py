"""Designations and assessments, and the wall between them.

A government act with a citation may ground a finding. A researcher's published
risk rating may not — however well sourced, and however alarming. Collapsing the
two is how a think tank's judgement becomes a legal conclusion about a named
person, so the separation is asserted over the whole table.
"""
from datetime import date

import pytest

from gleipnir.finding import Colour, Finding, RedGround
from gleipnir.institutions import (
    ASSESSMENTS, AUTHORITIES, Assessment, assessment_for, coverage,
    designation_for, status,
)


class FakeTarget:
    def __init__(self, name, programs=("US-BIS-EL",), countries=("cn",)):
        self.name = name
        self.programs = programs
        self.countries = countries
        self.datasets = ("US Entity List",)
        self.last_change = "2026-08-18T15:55:52"


class FakeIndex:
    def __init__(self, targets):
        self._by_name = {t.name.casefold(): t for t in targets}

    def by_name(self, name):
        hit = self._by_name.get((name or "").casefold())
        return [hit] if hit else []


INDEX = FakeIndex([FakeTarget("Beihang University",
                              programs=("US-BIS-EL", "US-MCCAIN-1286"))])


# ── the wall ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("name", sorted(ASSESSMENTS))
def test_no_assessment_may_ever_colour(name):
    """Asserted over the whole table rather than trusted at the call sites."""
    assert ASSESSMENTS[name].may_colour is False


def test_an_assessment_alone_cannot_ground_a_finding():
    s = status("Northwestern Polytechnical University", index=None)
    assert s.assessment is not None
    assert s.designation is None
    assert not s.may_ground_a_finding


def test_a_designation_can_ground_a_finding():
    s = status("Beihang University", INDEX)
    assert s.may_ground_a_finding
    assert s.designation.ground is RedGround.DESIGNATION


def test_an_assessment_carries_no_ground_attribute_at_all():
    """There is no code path from an assessment to a RedGround."""
    a = assessment_for("beihang university")
    assert not hasattr(a, "ground")


def test_a_designation_still_needs_an_authority_and_citation_to_go_red():
    """`finding.py` is the backstop: a designation is a ground, not a finding."""
    d = designation_for("Beihang University", INDEX)
    authority, citation = d.authorities()[0]
    Finding(colour=Colour.RED, ground=d.ground, statement="listed",
            authority=authority, citation=citation)
    with pytest.raises(ValueError, match="authority and a citation"):
        Finding(colour=Colour.RED, ground=d.ground, statement="listed")


# ── designations come from the store, not from source code ──────────────────

def test_nothing_is_hardcoded_without_a_list():
    """A designation list frozen into source is wrong within the quarter."""
    assert designation_for("Beihang University", None) is None


def test_an_unlisted_institution_returns_nothing():
    assert designation_for("University of Copenhagen", INDEX) is None


def test_the_programmes_carry_their_authority_and_citation():
    d = designation_for("Beihang University", INDEX)
    assert d.programmes == ("US-BIS-EL", "US-MCCAIN-1286")
    assert d.authorities()[0] == AUTHORITIES["US-BIS-EL"]
    assert "15 CFR" in d.authorities()[0][1]


def test_an_uncited_programme_is_not_given_an_authority_it_lacks():
    idx = FakeIndex([FakeTarget("Somewhere", programs=("XX-UNKNOWN",))])
    authority, citation = designation_for("Somewhere", idx).authorities()[0]
    assert authority == "programme XX-UNKNOWN" and citation == ""


def test_a_match_is_a_candidate_not_an_adjudication():
    assert "unadjudicated_name_match" in designation_for("Beihang University",
                                                         INDEX).line()


# ── coverage is stated, never implied ───────────────────────────────────────

def test_a_miss_does_not_read_as_a_clean_result():
    lines = status("Some Unlisted Polytechnic", INDEX).lines()
    assert "Not a clean result" in " ".join(lines)
    assert "partial" in " ".join(lines)


def test_the_assessment_table_says_how_partial_it_is():
    text = coverage()
    assert str(len(ASSESSMENTS)) in text
    assert "covers many more" in text


def test_every_assessment_is_dated_and_cited():
    for a in ASSESSMENTS.values():
        assert a.authority and a.citation
        assert isinstance(a.retrieved_on, date)


def test_the_two_records_are_never_merged():
    s = status("Beihang University", INDEX)
    assert s.designation is not None and s.assessment is not None
    assert len(s.lines()) == 2
    assert "never a colour" in s.lines()[1]
