"""The CV projection, and the rule that licenses an expansion.

The expansion rule is the point: a foreign entry in someone's record is what
buys the right to query that jurisdiction's register. So these test the trigger
as carefully as the assembly — a country invented from a company name would
spend budget on a fetch nobody asked for, and a country dropped would leave a
foreign employer unexamined.
"""
from datetime import date

import pytest

from gleipnir.claims import Claim, EntityRef, EpistemicTier, Predicate
from gleipnir.cv import (
    HOME, REGISTER_FOR, SOURCE_JURISDICTION, build, expansions, render,
)

PERSON = EntityRef(kind="person", key="person:4000164459", label="A Person")


def claim(predicate=Predicate.HAS_ROLE, subject=None, obj=None, source_id="cvr",
          tier=EpistemicTier.REGISTERED, frm=None, to=None, **quals):
    return Claim(
        subject=subject or EntityRef(kind="company", key="company:1", label="Dansk ApS"),
        predicate=predicate,
        object=obj if obj is not None else PERSON,
        source_id=source_id, epistemic_tier=tier, raw_ref="hash",
        valid_from=frm, valid_to=to, qualifiers=dict(quals))


# ── assembly ────────────────────────────────────────────────────────────────

def test_a_role_filed_company_to_person_lands_in_the_persons_cv():
    """CVR files `company --has_role--> person`, so a CV is the reverse index."""
    cv = build([claim(frm=date(2014, 1, 1), role="Bestyrelse")], PERSON)
    assert len(cv.entries) == 1
    assert cv.entries[0].entity.label == "Dansk ApS"
    assert cv.entries[0].capacity == "Bestyrelse"


def test_an_affiliation_stated_person_to_institution_also_lands():
    """The sources disagree about which way a position points; a CV that read
    one direction would be empty for half the graph."""
    cv = build([claim(predicate=Predicate.HAS_AFFILIATION, subject=PERSON,
                      obj=EntityRef(kind="company", key="ror:x", label="Some University"),
                      source_id="openalex", tier=EpistemicTier.THIRD_PARTY,
                      country_code="CN")], PERSON)
    assert cv.entries[0].entity.label == "Some University"
    assert cv.entries[0].country == "CN"


def test_claims_about_other_people_are_not_in_this_cv():
    other = EntityRef(kind="person", key="person:999", label="Someone Else")
    assert build([claim(obj=other)], PERSON).entries == ()


def test_a_predicate_that_is_not_a_position_is_ignored():
    assert build([claim(predicate=Predicate.HAS_CAPITAL)], PERSON).entries == ()


def test_every_entry_keeps_its_tier_and_evidence():
    """A registered directorship and a role read off a profile are both entries
    and are never the same kind of statement."""
    cv = build([claim(),
                claim(source_id="attestation", tier=EpistemicTier.SELF_DECLARED)],
               PERSON)
    assert {e.tier for e in cv.entries} == {EpistemicTier.REGISTERED,
                                            EpistemicTier.SELF_DECLARED}
    assert all(e.evidence_ref == "hash" for e in cv.entries)


def test_entries_are_ordered_by_date():
    cv = build([claim(frm=date(2020, 1, 1), obj=PERSON),
                claim(frm=date(2010, 1, 1), obj=PERSON)], PERSON)
    assert [e.valid_from for e in cv.entries] == [date(2010, 1, 1), date(2020, 1, 1)]


# ── country: read, never inferred ───────────────────────────────────────────

def test_a_national_register_states_its_own_jurisdiction():
    """An entity in CVR is Danish because that is what CVR is. Reading the
    country off the source is the source's coverage, not an inference."""
    cv = build([claim(source_id="cvr")], PERSON)
    assert cv.entries[0].country == "DK"
    assert SOURCE_JURISDICTION["ee_ariregister"] == "EE"


def test_a_per_record_country_beats_the_source_default():
    cv = build([claim(source_id="cvr", country_code="EE")], PERSON)
    assert cv.entries[0].country == "EE"


def test_a_country_is_never_guessed_from_a_name():
    """A Danish ApS with a foreign-sounding name must not become a foreign
    entry — a foreign entry costs a fetch."""
    cv = build([claim(source_id="attestation", tier=EpistemicTier.SELF_DECLARED,
                      obj=PERSON,
                      subject=EntityRef(kind="company", key="c:2",
                                        label="Tallinn Logistics OU"))], PERSON)
    assert cv.entries[0].country == ""
    assert not cv.entries[0].country_known


def test_an_unplaced_entry_is_neither_foreign_nor_domestic():
    cv = build([claim(source_id="attestation", tier=EpistemicTier.SELF_DECLARED)],
               PERSON)
    assert cv.unknown_country() == list(cv.entries)
    assert cv.foreign() == []
    assert expansions(cv) == []


# ── the expansion rule ──────────────────────────────────────────────────────

def foreign_cv(country="EE", label="Tallinn Logistics OU", **kw):
    return build([claim(predicate=Predicate.MEMBER_OF_GROUP, subject=PERSON,
                        obj=EntityRef(kind="company", key=f"c:{label}", label=label),
                        source_id="attestation", tier=EpistemicTier.SELF_DECLARED,
                        country_code=country, **kw)], PERSON)


def test_a_foreign_entry_names_the_register_that_could_resolve_it():
    todo = expansions(foreign_cv())
    assert len(todo) == 1
    assert todo[0].country == "EE"
    assert todo[0].source == REGISTER_FOR["EE"] == "ee_ariregister"
    assert todo[0].resolvable


def test_the_derivation_is_printed_not_just_the_action():
    """`plan.py`'s discipline: an agenda item says which fact wanted it."""
    because = expansions(foreign_cv())[0].because
    assert "A Person" in because and "Tallinn Logistics OU" in because
    assert "EE" in because


def test_a_home_entry_generates_no_expansion():
    assert expansions(foreign_cv(country=HOME)) == []


def test_a_country_with_no_connected_register_is_a_coverage_statement():
    """Reported, not silently dropped: we know it is foreign and we have
    nowhere to look."""
    todo = expansions(foreign_cv(country="TR"))
    assert len(todo) == 1 and not todo[0].resolvable
    assert "no connected register covers TR" in todo[0].because


def test_one_entity_across_several_years_is_one_lookup():
    claims = [claim(predicate=Predicate.MEMBER_OF_GROUP, subject=PERSON,
                    obj=EntityRef(kind="company", key="c:1", label="Same OU"),
                    source_id="attestation", tier=EpistemicTier.SELF_DECLARED,
                    frm=date(y, 1, 1), country_code="EE")
              for y in (2015, 2016, 2017)]
    cv = build(claims, PERSON)
    assert len(cv.entries) == 3
    assert len(expansions(cv)) == 1


def test_an_already_resolved_entity_is_not_queued_again():
    cv = foreign_cv()
    key = cv.entries[0].entity.key
    assert expansions(cv, already_resolved=[key]) == []


def test_a_foreign_entry_is_an_agenda_item_and_not_a_colour():
    """Working for an Estonian company is ordinary. The expansion buys a
    lookup and asserts nothing."""
    e = expansions(foreign_cv())[0]
    assert not hasattr(e, "colour") and not hasattr(e, "risk")
    assert set(vars(e)) == {"entry", "country", "source", "because"}


# ── rendering ───────────────────────────────────────────────────────────────

def test_an_empty_cv_reads_as_coverage_not_as_clean():
    text = render(build([], PERSON, consulted=("cvr",)))
    assert "no position on file" in text
    assert "not a clean result" in text


def test_the_rendering_shows_the_source_on_every_line():
    text = render(foreign_cv())
    assert "attestation/self_declared" in text
    assert "EXPANDS OUTSIDE DK" in text


# ── the goal that turns an expansion into an agenda item ────────────────────

def test_a_foreign_cv_entry_becomes_a_derived_need():
    """`plan.py`'s discipline end to end: a fact somebody filed generates the
    fetch, and the agenda says which fact wanted it."""
    from gleipnir.goals import GOALS
    from gleipnir.plan import State, agenda

    person = EntityRef(kind="person", key="person:1", label="A Person")
    registry = [claim(obj=person, frm=date(2014, 1, 1), role="Direktion")]
    attested = [claim(predicate=Predicate.MEMBER_OF_GROUP, subject=person,
                      obj=EntityRef(kind="company", key="c:1",
                                    label="Tallinn Logistics OU"),
                      source_id="attestation", tier=EpistemicTier.SELF_DECLARED,
                      country_code="EE")]
    state = State(root="12345678", as_of=date(2026, 1, 1))
    state.mark("registry_claims", "12345678", registry)
    state.mark("website", "12345678", "https://example.invalid")
    state.mark("attested_claims", person.key, attested)

    needs = [n for n in agenda(GOALS, state) if n.key == "foreign_entity"]
    assert len(needs) == 1
    assert needs[0].source == "ee_ariregister"
    assert needs[0].subject == "Tallinn Logistics OU"
    assert "A Person" in needs[0].because


def test_a_country_with_no_register_is_abandoned_rather_than_queued_forever():
    from gleipnir.goals import GOALS
    from gleipnir.plan import State, agenda

    person = EntityRef(kind="person", key="person:1", label="A Person")
    registry = [claim(obj=person, frm=date(2014, 1, 1))]
    attested = [claim(predicate=Predicate.MEMBER_OF_GROUP, subject=person,
                      obj=EntityRef(kind="company", key="c:2", label="Istanbul AS"),
                      source_id="attestation", tier=EpistemicTier.SELF_DECLARED,
                      country_code="TR")]
    state = State(root="12345678", as_of=date(2026, 1, 1))
    state.mark("registry_claims", "12345678", registry)
    state.mark("website", "12345678", "https://example.invalid")
    state.mark("attested_claims", person.key, attested)

    assert [n for n in agenda(GOALS, state) if n.key == "foreign_entity"] == []
    assert state.dead("foreign_entity", "Istanbul AS")
