"""Extractor tests on a synthetic document.

Synthetic rather than a recorded live payload because a real CVR document
carries named private individuals, and a test fixture is the least controlled
place personal data can end up — it gets copied into CI logs, forks and
tarballs. The structure is what matters here, and that is reproducible.
"""
from datetime import date
from decimal import Decimal

from gleipnir.claims import EpistemicTier, Predicate
from gleipnir.extract.cvr import extract_company


def _attr(type_, *values):
    return {"type": type_,
            "vaerdier": [{"vaerdi": v, "periode": {"gyldigFra": f, "gyldigTil": t}}
                         for v, f, t in values]}


DOC = {"Vrvirksomhed": {
    "cvrNummer": 99000147,
    "navne": [{"navn": "Acme International Vodka ApS",
               "periode": {"gyldigFra": "2021-06-07", "gyldigTil": None}}],
    "virksomhedsstatus": [{"status": "NORMAL",
                           "periode": {"gyldigFra": "2021-06-07", "gyldigTil": None}}],
    "virksomhedsform": [{"langBeskrivelse": "Anpartsselskab",
                         "periode": {"gyldigFra": "2021-06-07", "gyldigTil": None}}],
    "beliggenhedsadresse": [{"vejnavn": "Havnegade", "husnummerFra": 12,
                             "vejkode": 100, "postnummer": 1058,
                             "postdistrikt": "København K",
                             "kommune": {"kommuneKode": 101, "kommuneNavn": "København"},
                             "periode": {"gyldigFra": "2021-06-07", "gyldigTil": None}}],
    "hovedbranche": [{"branchekode": "110100", "branchetekst": "Destillation",
                      "periode": {"gyldigFra": "2021-06-07", "gyldigTil": None}}],
    "hjemmeside": [{"vaerdi": "acme-vodka.dk",
                    "periode": {"gyldigFra": "2021-06-07", "gyldigTil": None}}],
    "attributter": [
        _attr("TEGNINGSREGEL", ("Selskabet tegnes af direktøren", "2021-06-07", None)),
        _attr("KAPITAL", ("40000.00", "2021-06-07", None)),
        _attr("REVISION_FRAVALGT", ("true", "2021-06-07", None)),
        _attr("IRRELEVANT_ATTRIBUTE", ("x", "2021-06-07", None)),
    ],
    "deltagerRelation": [
        {   # minority equity, majority votes — the >50%-rule evasion shape
            # forretningsnoegle is the participant's own CVR number — the key
            # that makes an ownership edge traversable.
            "deltager": {"enhedsNummer": 111, "enhedstype": "VIRKSOMHED",
                         "forretningsnoegle": 99000163,
                         "navne": [{"navn": "Cyprus Holdco Ltd"}]},
            "organisationer": [{
                "hovedtype": "REGISTER",
                "organisationsNavn": [{"navn": "EJERREGISTER"}],
                "medlemsData": [{"attributter": [
                    _attr("EJERANDEL_PROCENT", ("0.49", "2022-01-01", None)),
                    _attr("EJERANDEL_STEMMERET_PROCENT", ("0.75", "2022-01-01", None)),
                ]}],
            }],
        },
        {   # listed as an owner with no percentage filed
            "deltager": {"enhedsNummer": 222, "enhedstype": "PERSON",
                         "navne": [{"navn": "Test Person"}]},
            "organisationer": [{
                "hovedtype": "REGISTER",
                "organisationsNavn": [{"navn": "REELLE EJERE"}],
                "medlemsData": [{"periode": {"gyldigFra": "2021-06-07",
                                             "gyldigTil": None},
                                 "attributter": []}],
            }],
        },
        {   # a board seat
            "deltager": {"enhedsNummer": 333, "enhedstype": "PERSON",
                         "navne": [{"navn": "Board Member"}]},
            "organisationer": [{
                "hovedtype": "LEDELSESORGAN",
                "organisationsNavn": [{"navn": "Bestyrelse"}],
                "medlemsData": [{"periode": {"gyldigFra": "2021-06-07",
                                             "gyldigTil": "2023-04-01"},
                                 "attributter": [_attr("FUNKTION",
                                                       ("FORMAND", "2021-06-07", None))]}],
            }],
        },
    ],
}}


def _claims():
    return extract_company(DOC, raw_ref="deadbeef", observed_at="2026-08-26T00:00:00Z")


def test_ownership_and_voting_are_separate_claims():
    """The divergence has to have somewhere to appear — that is the whole
    reason they are not collapsed when equal."""
    cs = _claims()
    owns = [c for c in cs if c.predicate is Predicate.OWNS and c.subject.key == "99000163"]
    votes = [c for c in cs if c.predicate is Predicate.HAS_VOTING_RIGHTS
             and c.subject.key == "99000163"]
    assert len(owns) == 1 and len(votes) == 1
    assert owns[0].qualifiers["share"] == Decimal("0.49")
    assert votes[0].qualifiers["share"] == Decimal("0.75")


def test_ownership_direction_is_owner_to_company():
    cs = _claims()
    own = next(c for c in cs if c.predicate is Predicate.OWNS and c.subject.key == "99000163")
    assert own.subject.kind == "company"
    assert own.object.key == "99000147"


def test_ownership_claims_are_self_declared_to_registry():
    """The register is authored by the company, not verified by the state —
    and that is the tier the whole contradiction model turns on."""
    cs = _claims()
    for c in cs:
        if c.predicate in (Predicate.OWNS, Predicate.HAS_VOTING_RIGHTS):
            assert c.epistemic_tier is EpistemicTier.SELF_DECLARED_TO_REGISTRY


def test_management_roles_are_registered_tier():
    cs = _claims()
    role = next(c for c in cs if c.predicate is Predicate.HAS_ROLE
                and c.object.key == "333")
    assert role.epistemic_tier is EpistemicTier.REGISTERED
    assert role.qualifiers["role"] == "Bestyrelse"
    assert role.qualifiers["function"] == "FORMAND"
    assert role.valid_to == date(2023, 4, 1)


def test_unquantified_ownership_is_not_zero():
    """Listed in the register with no percentage is an unquantified assertion,
    not a 0% holding — the difference matters when aggregating to a 50% test."""
    cs = _claims()
    own = next(c for c in cs if c.predicate is Predicate.OWNS and c.subject.key == "222")
    assert own.qualifiers["share"] is None
    assert own.valid_from == date(2021, 6, 7)


def test_share_is_kept_as_filed_fraction():
    """CVR files 0.49 for 49%, and it is kept as an exact Decimal.

    Not float: `0.05 - 0.02` is `0.030000000000000002` in binary, which made the
    bunching window 0.02 wide at five thresholds and 0.0199999999999999998 at
    two — a share exactly 2pp below a threshold hit at some and missed at
    others."""
    cs = _claims()
    own = next(c for c in cs if c.predicate is Predicate.OWNS and c.subject.key == "99000163")
    assert own.qualifiers["share"] == Decimal("0.49")
    assert isinstance(own.qualifiers["share"], Decimal)


def test_signing_rule_and_capital_extracted():
    cs = _claims()
    preds = {c.predicate for c in cs}
    assert Predicate.SIGNING_RULE in preds
    assert Predicate.HAS_CAPITAL in preds
    assert Predicate.AUDIT_WAIVED in preds


def test_unmapped_attributes_are_ignored():
    assert not any(c.object == "x" for c in _claims())


def test_every_claim_carries_provenance():
    for c in _claims():
        assert c.raw_ref == "deadbeef"
        assert c.observed_at == "2026-08-26T00:00:00Z"
        assert c.source_id == "cvr"


def test_accepts_wrapped_elasticsearch_response():
    wrapped = {"hits": {"hits": [{"_source": DOC}]}}
    assert len(extract_company(wrapped, raw_ref="x")) == len(_claims())


def test_empty_response_yields_no_claims():
    assert extract_company({"hits": {"hits": []}}, raw_ref="x") == []


def test_company_participant_without_a_cvr_is_kept_not_dropped():
    """A company participant with no usable forretningsnoegle is still a real
    ownership edge. Marked unresolvable rather than discarded — an absent edge
    would understate a chain, which fails closed in the wrong direction."""
    doc = {"Vrvirksomhed": {"cvrNummer": 99000147, "navne": [{"navn": "X"}],
        "deltagerRelation": [{
            "deltager": {"enhedsNummer": 999, "enhedstype": "VIRKSOMHED",
                         "forretningsnoegle": None, "navne": [{"navn": "Foreign Ltd"}]},
            "organisationer": [{"hovedtype": "REGISTER",
                "organisationsNavn": [{"navn": "EJERREGISTER"}],
                "medlemsData": [{"attributter": [
                    _attr("EJERANDEL_PROCENT", ("0.60", "2022-01-01", None))]}]}]}]}}
    c = next(c for c in extract_company(doc, raw_ref="r")
             if c.predicate is Predicate.OWNS)
    assert c.subject.key == "enh:999"
    assert c.subject.label == "Foreign Ltd"


def test_company_participant_with_bad_checksum_falls_back():
    """A forretningsnoegle failing mod-11 is a parsing artefact, not a CVR
    number, and must not become a node other companies could join to."""
    doc = {"Vrvirksomhed": {"cvrNummer": 99000147, "navne": [{"navn": "X"}],
        "deltagerRelation": [{
            "deltager": {"enhedsNummer": 888, "enhedstype": "VIRKSOMHED",
                         "forretningsnoegle": 12345678, "navne": [{"navn": "Bad Ltd"}]},
            "organisationer": [{"hovedtype": "REGISTER",
                "organisationsNavn": [{"navn": "EJERREGISTER"}],
                "medlemsData": [{"attributter": [
                    _attr("EJERANDEL_PROCENT", ("0.60", "2022-01-01", None))]}]}]}]}}
    c = next(c for c in extract_company(doc, raw_ref="r")
             if c.predicate is Predicate.OWNS)
    assert c.subject.key == "enh:888"


def test_a_foreign_director_is_a_natural_person():
    """Danish law requires members of a management body to be natural persons.
    CVR files foreign ones as ANDEN_DELTAGER because they have no Danish unit
    number — one observed subsidiary's three directors were all filed that way, and
    treating them as non-persons made nominee_density blind to every foreign
    director."""
    doc = {"Vrvirksomhed": {"cvrNummer": 99000147, "navne": [{"navn": "X"}],
        "deltagerRelation": [{
            "deltager": {"enhedsNummer": 777, "enhedstype": "ANDEN_DELTAGER",
                         "navne": [{"navn": "Anders Lindqvist"}]},
            "organisationer": [{"hovedtype": "LEDELSESORGAN",
                "organisationsNavn": [{"navn": "Direktion"}],
                "medlemsData": [{"attributter": [
                    _attr("FUNKTION", ("DIREKTØR", "2020-01-01", None))]}]}]}]}}
    c = next(c for c in extract_company(doc, raw_ref="r")
             if c.predicate is Predicate.HAS_ROLE)
    assert c.object.kind == "person"
    assert c.object.key.startswith("fp:")
    assert c.object.label == "Anders Lindqvist"


def test_a_foreign_company_in_an_ownership_register_stays_a_company():
    doc = {"Vrvirksomhed": {"cvrNummer": 99000147, "navne": [{"navn": "X"}],
        "deltagerRelation": [{
            "deltager": {"enhedsNummer": 888, "enhedstype": "ANDEN_DELTAGER",
                         "navne": [{"navn": "Bidco 7 (Luxembourg) Acquisition S.à.r.l."}]},
            "organisationer": [{"hovedtype": "REGISTER",
                "organisationsNavn": [{"navn": "EJERREGISTER"}],
                "medlemsData": [{"attributter": [
                    _attr("EJERANDEL_PROCENT", ("1.0", "2020-01-01", None))]}]}]}]}}
    c = next(c for c in extract_company(doc, raw_ref="r")
             if c.predicate is Predicate.OWNS)
    assert c.subject.kind == "company" and c.subject.key.startswith("fc:")


def test_an_unclassifiable_other_party_says_so_rather_than_guessing():
    from gleipnir.extract.cvr import classify_other_party
    assert classify_other_party("Boet efter Jens Hansen", set()) == "other_party"
    assert classify_other_party("Northfield Capital AB", set()) == "company"
    assert classify_other_party("Jane Doe", {"LEDELSESORGAN"}) == "person"
    assert classify_other_party("Jane Doe", {"REGISTER"}) == "other_party"
