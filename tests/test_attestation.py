"""Analyst-entered observations — §11's path 2, and its guardrails.

`architecture.md` §11 chose this over scraping and said the architecture should
make it *excellent*. Excellent here means auditable: every statement carries the
document's own words, the quote is checked against the excerpt it came from, and
the tier stays the document's rather than the reader's.
"""
from datetime import date

import pytest

from gleipnir.attestation import (
    PAIRABLE, Attestation, AttestationError, SourceKind, Statement,
    attestations, claims_for, record,
)
from gleipnir.claims import EntityRef, EpistemicTier, Predicate
from gleipnir.contradict import Kind, pair_claims
from gleipnir.rawstore import RawStore

EXCERPT = ("Jens Hansen — CFO at Acme Holding ApS, 2019 – present. "
           "Previously Finance Director, Tallinn Logistics OU, Estonia.")


def statement(predicate=Predicate.HAS_ROLE, value="Jens Hansen",
              quote="CFO at Acme Holding ApS", **kw):
    return Statement(predicate=predicate, value=value, quote=quote, **kw)


def attestation(**kw):
    base = dict(subject="12345678", source_kind=SourceKind.PUBLIC_PROFILE,
                location="https://www.linkedin.com/in/example",
                retrieved_on=date(2026, 8, 28), analyst="A. Analyst",
                excerpt=EXCERPT, statements=(statement(),))
    base.update(kw)
    return Attestation(**base)


# ── the quote rule ──────────────────────────────────────────────────────────

def test_a_statement_needs_the_documents_own_words():
    with pytest.raises(AttestationError, match="own words"):
        attestation(statements=(statement(quote=""),))


def test_a_quote_not_in_the_excerpt_is_refused():
    """The rule `oracle.py` applies to a model applies to a person: it turns
    "I remember it saying he was CFO" into something auditable."""
    with pytest.raises(AttestationError, match="does not appear in the excerpt"):
        attestation(statements=(statement(quote="Chief Executive Officer"),))


def test_the_quote_check_tolerates_whitespace_only():
    attestation(statements=(statement(quote="CFO   at\n  Acme Holding ApS"),))


def test_an_attestation_needs_the_excerpt_its_quotes_came_from():
    with pytest.raises(AttestationError, match="excerpt"):
        attestation(excerpt="   ")


# ── attribution ─────────────────────────────────────────────────────────────

def test_an_attestation_must_name_who_made_it():
    with pytest.raises(AttestationError, match="who made it"):
        attestation(analyst="  ")


def test_an_attestation_must_say_what_was_read_and_where():
    with pytest.raises(AttestationError, match="what was read"):
        attestation(location="")


def test_an_attestation_with_no_statement_records_nothing():
    with pytest.raises(AttestationError, match="records nothing"):
        attestation(statements=())


# ── the tier ────────────────────────────────────────────────────────────────

def test_the_tier_is_the_documents_never_the_readers():
    """Filing a profile at ANALYST tier would launder a self-description into a
    checked fact and destroy the comparison it exists to feed."""
    for kind in SourceKind:
        a = attestation(source_kind=kind)
        assert a.tier is EpistemicTier.SELF_DECLARED
        assert a.tier is not EpistemicTier.ANALYST


def test_every_claim_says_the_document_asserted_it_not_the_analyst():
    for c in attestation().to_claims(raw_ref="h"):
        assert c.qualifiers["asserted_by"] == "the document"
        assert c.qualifiers["read_by"] == "A. Analyst"
        assert c.epistemic_tier is EpistemicTier.SELF_DECLARED


def test_provenance_travels_on_every_claim():
    c = attestation().to_claims(raw_ref="h")[0]
    assert c.qualifiers["location"].startswith("https://")
    assert c.qualifiers["retrieved_on"] == "2026-08-28"
    assert c.qualifiers["quote"] == "CFO at Acme Holding ApS"
    assert c.raw_ref == "h"


# ── what may be recorded at all ─────────────────────────────────────────────

def test_only_predicates_the_registry_can_check_are_admitted():
    """A statement with no registry counterpart is an unfalsifiable note about a
    named person, and §11 is the reason not to collect it."""
    with pytest.raises(AttestationError, match="no registry counterpart"):
        attestation(statements=(statement(predicate=Predicate.HAS_PURPOSE,
                                          quote="CFO at Acme Holding ApS"),))


def test_the_pairable_set_is_exactly_what_contradict_can_use():
    from gleipnir.extract.website import CHECKS_AGAINST
    assert PAIRABLE == frozenset(CHECKS_AGAINST)


def test_a_statement_must_assert_something():
    with pytest.raises(AttestationError, match="must assert"):
        attestation(statements=(statement(value=""),))


# ── the point of the whole thing ────────────────────────────────────────────

def test_an_attested_role_pairs_against_the_register():
    """§3's example, finally reachable: the profile says CFO, the register does
    not. Nothing here decides which is right."""
    registry = []
    pairs = pair_claims(attestation().to_claims(raw_ref="h"), registry,
                        as_of=date(2026, 8, 28))
    assert len(pairs) == 1
    assert pairs[0].kind is Kind.EXISTENCE_CONFLICT


def test_a_person_subject_produces_a_cv_entry():
    from gleipnir.cv import build, expansions

    person = EntityRef(kind="person", key="person:1", label="Jens Hansen")
    a = attestation(subject="person:1", statements=(
        statement(predicate=Predicate.MEMBER_OF_GROUP, value="Tallinn Logistics OU",
                  quote="Tallinn Logistics OU, Estonia",
                  detail={"country_code": "EE"}),))
    cv = build(a.to_claims(raw_ref="h", subject_ref=person), person)
    assert len(cv.entries) == 1
    assert expansions(cv)[0].source == "ee_ariregister"


# ── the store ───────────────────────────────────────────────────────────────

def test_recording_is_append_only_and_round_trips(tmp_path):
    store = RawStore(tmp_path)
    record(store, attestation())
    record(store, attestation(analyst="B. Analyst"))
    got = attestations(store, "12345678")
    assert [a.analyst for _, a in got] == ["A. Analyst", "B. Analyst"]
    assert got[0][1].statements[0].quote == "CFO at Acme Holding ApS"
    assert got[0][1].source_kind is SourceKind.PUBLIC_PROFILE


def test_claims_for_reads_every_attestation_about_a_subject(tmp_path):
    store = RawStore(tmp_path)
    record(store, attestation())
    record(store, attestation(statements=(
        statement(predicate=Predicate.HAS_LOCATION, value="Tallinn",
                  quote="Tallinn Logistics OU, Estonia"),)))
    assert len(claims_for(store, "12345678")) == 2


def test_an_attestation_about_another_subject_is_not_returned(tmp_path):
    store = RawStore(tmp_path)
    record(store, attestation(subject="99999999"))
    assert claims_for(store, "12345678") == []
