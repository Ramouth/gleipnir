"""Pairing self-declared claims against registry claims.

Several of these encode false findings measured on a live 95-site cohort: the
comparator itself was manufacturing contradictions.
"""
from datetime import date

from gleipnir.claims import Claim, EntityRef, EpistemicTier, Predicate
from gleipnir.contradict import Kind, Status, pair_claims

AS_OF = date(2026, 8, 27)
CO = EntityRef(kind="company", key="99000147", label="Acme")


def web(pred, obj, **q):
    return Claim(subject=CO, predicate=pred, object=obj, source_id="website",
                 epistemic_tier=EpistemicTier.SELF_DECLARED, raw_ref="w",
                 observed_at=str(AS_OF), qualifiers={"checks_against": "x", **q})


def reg(pred, obj, frm=date(2020, 1, 1), to=None, **q):
    return Claim(subject=CO, predicate=pred, object=obj, source_id="cvr",
                 epistemic_tier=EpistemicTier.REGISTERED, raw_ref="r",
                 valid_from=frm, valid_to=to, qualifiers=q)


def only(pairs, pred):
    return next(p for p in pairs if p.predicate is pred)


# ── addresses: the comparator was the bug ───────────────────────────────────

def test_postcode_match_is_corroboration_not_conflict():
    """Live cohort: declared '8382 Hinnerup' against registered 'Eksempelvej 14 8382
    Hinnerup' was reported as a value conflict. String equality manufactured a
    contradiction on nearly every site carrying an address."""
    pairs = pair_claims(
        [web(Predicate.HAS_LOCATION, "8382 Hinnerup", postcode="8382")],
        [reg(Predicate.REGISTERED_AT,
             EntityRef("address", "k", "Eksempelvej 14 8382 Hinnerup"), postcode="8382")],
        as_of=AS_OF)
    assert only(pairs, Predicate.REGISTERED_AT).status is Status.CORROBORATED


def test_a_branch_matching_a_production_unit_corroborates():
    """A multi-site business lists its branches; comparing them against the
    legal seat produced seven false conflicts on one live company."""
    pairs = pair_claims(
        [web(Predicate.HAS_LOCATION, "9000 Aalborg", postcode="9000")],
        [reg(Predicate.REGISTERED_AT, EntityRef("address", "a", "seat"),
             postcode="4683", unit="registered_office"),
         reg(Predicate.REGISTERED_AT, EntityRef("address", "b", "branch"),
             postcode="9000", unit="production_unit")],
        as_of=AS_OF)
    assert only(pairs, Predicate.REGISTERED_AT).status is Status.CORROBORATED


def test_a_genuinely_unregistered_location_still_conflicts():
    pairs = pair_claims(
        [web(Predicate.HAS_LOCATION, "1050 København", postcode="1050")],
        [reg(Predicate.REGISTERED_AT, EntityRef("address", "a", "x"), postcode="8000")],
        as_of=AS_OF)
    assert only(pairs, Predicate.REGISTERED_AT).kind is Kind.VALUE_CONFLICT


# ── identifiers ─────────────────────────────────────────────────────────────

def test_displayed_cvr_matching_the_subject_is_a_deterministic_join():
    pairs = pair_claims([web(Predicate.CLAIMS_IDENTIFIER, "DK99000147")],
                        [reg(Predicate.HAS_NAME, "Acme")], as_of=AS_OF)
    p = only(pairs, Predicate.CLAIMS_IDENTIFIER)
    assert p.status is Status.CORROBORATED


def test_a_different_displayed_cvr_is_flagged_as_a_possible_group_sibling():
    """Measured live: DATAFIRMA ApS displays 99000465 while registered as
    99000473. Reported as something to expand, never as an accusation."""
    pairs = pair_claims([web(Predicate.CLAIMS_IDENTIFIER, "DK99000465")],
                        [reg(Predicate.HAS_NAME, "Acme")], as_of=AS_OF)
    p = only(pairs, Predicate.CLAIMS_IDENTIFIER)
    assert p.kind is Kind.VALUE_CONFLICT
    assert "group sibling" in p.evidence


# ── headcount ───────────────────────────────────────────────────────────────

def test_headcount_within_a_factor_of_two_corroborates():
    """CVR files employment in bands and a website rounds. Equality would flag
    every honest company."""
    pairs = pair_claims([web(Predicate.EMPLOYS, 30)],
                        [reg(Predicate.EMPLOYS, 17)], as_of=AS_OF)
    assert only(pairs, Predicate.EMPLOYS).status is Status.CORROBORATED


def test_a_large_headcount_gap_conflicts():
    pairs = pair_claims([web(Predicate.EMPLOYS, 3500)],
                        [reg(Predicate.EMPLOYS, 4)], as_of=AS_OF)
    p = only(pairs, Predicate.EMPLOYS)
    assert p.kind is Kind.VALUE_CONFLICT and "factor" in p.evidence


def test_headcount_with_nothing_filed_is_an_existence_conflict():
    pairs = pair_claims([web(Predicate.EMPLOYS, 200)], [], as_of=AS_OF)
    assert only(pairs, Predicate.EMPLOYS).kind is Kind.EXISTENCE_CONFLICT


# ── roles ───────────────────────────────────────────────────────────────────

def test_a_site_named_officer_holding_a_registered_function_corroborates():
    """Measured live on MARLOG: Nils Bakken, named COO on the site, holds a
    registered function. Positive evidence, and rare."""
    pairs = pair_claims(
        [web(Predicate.HAS_ROLE, EntityRef("person", "n", "Nils Bakken"), role="coo")],
        [reg(Predicate.HAS_ROLE, EntityRef("person", "e1", "Nils Bakken"))],
        as_of=AS_OF)
    assert only(pairs, Predicate.HAS_ROLE).status is Status.CORROBORATED


def test_an_unregistered_site_officer_is_a_candidate_never_a_conclusion():
    pairs = pair_claims(
        [web(Predicate.HAS_ROLE, EntityRef("person", "n", "Erik Halvorsen"), role="ceo")],
        [reg(Predicate.HAS_ROLE, EntityRef("person", "e1", "Someone Else"))],
        as_of=AS_OF)
    p = only(pairs, Predicate.HAS_ROLE)
    assert p.kind is Kind.EXISTENCE_CONFLICT
    assert "NOT adjudicated" in p.evidence
    assert "informal titles are common" in p.evidence


# ── staleness and scope ─────────────────────────────────────────────────────

def test_every_pair_carries_days_since_the_registry_last_changed():
    """The cheapest materiality discriminator: three weeks stale is expected,
    four years is a different story deliberately told."""
    pairs = pair_claims([web(Predicate.EMPLOYS, 200)],
                        [reg(Predicate.EMPLOYS, 5, frm=date(2016, 8, 27))],
                        as_of=AS_OF)
    assert only(pairs, Predicate.EMPLOYS).staleness_days == 3652


def test_a_claim_with_no_registry_counterpart_is_not_paired():
    """The extractor only emits what a registry can contradict; anything else
    would sit in the graph as an un-adjudicable node."""
    c = web(Predicate.HAS_WEBSITE, "example.dk")
    assert pair_claims([c], [reg(Predicate.HAS_NAME, "Acme")], as_of=AS_OF) == []


def test_expired_registry_claims_are_not_used_as_the_comparator():
    pairs = pair_claims(
        [web(Predicate.EMPLOYS, 200)],
        [reg(Predicate.EMPLOYS, 200, frm=date(2015, 1, 1), to=date(2016, 1, 1))],
        as_of=AS_OF)
    assert only(pairs, Predicate.EMPLOYS).kind is Kind.EXISTENCE_CONFLICT
