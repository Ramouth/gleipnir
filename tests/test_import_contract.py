"""Did the fact actually get imported?

Unit tests over synthetic fixtures cannot answer that: a fixture only contains
what its author already thought to parse, so a parser that silently drops a
field passes every one of them. Six such failures reached live data in this
project before any test caught one.

These tests invert it. The corpus test walks every raw document actually held
and fails on any field that carried data, produced no claim, and has no written
reason for being skipped. On first run it surfaced twelve — including
`BØRSNOTERET` (publicly listed), `GENOPTAGELSE_TVANGSOPLØSNING` (reinstated
after compulsory dissolution) and `OFFENTLIG_EJERBOG` (publishes its shareholder
register), all sitting in the data unread.
"""
from collections import Counter
from datetime import date
from decimal import Decimal

import pytest

from gleipnir.claims import Predicate, at
from gleipnir.config import settings
from gleipnir.extract.contract import (
    CVR_ATTRIBUTES, CVR_REQUIRED, IGNORED, IGNORED_ATTRIBUTES, audit_company,
    silent_drops, unwrap,
)
from gleipnir.extract.cvr import extract_company
from gleipnir.rawstore import RawStore

AS_OF = date(2026, 8, 27)


def corpus():
    """Every distinct company document in the raw store."""
    store = RawStore(settings.raw_store_path)
    seen, out = set(), []
    for f in store.fetches():
        if f.source != "cvr" or f.resource_type != "virksomhed":
            continue
        if f.resource_id in seen:
            continue
        seen.add(f.resource_id)
        out.append((f.resource_id, store.get_json(f.content_hash), f.content_hash))
    return out


CORPUS = corpus()
needs_corpus = pytest.mark.skipif(
    len(CORPUS) < 5, reason="no ingested corpus in this checkout")


# ── the contract is coherent ────────────────────────────────────────────────

def test_no_field_is_both_required_and_ignored():
    assert not (set(CVR_REQUIRED) & set(IGNORED))
    assert not (set(CVR_ATTRIBUTES) & set(IGNORED_ATTRIBUTES))


def test_every_declared_skip_states_a_reason():
    """A decision not to import something is reviewable only if it is written
    down. A decision implicit in what the parser happens to touch is not."""
    for table in (IGNORED, IGNORED_ATTRIBUTES):
        for field, reason in table.items():
            assert reason.strip(), f"{field} is skipped with no reason given"
            assert len(reason) > 15, f"{field}: {reason!r} is not a reason"


def test_required_fields_name_a_real_predicate():
    for field, pred in {**CVR_REQUIRED, **CVR_ATTRIBUTES}.items():
        assert isinstance(pred, Predicate), field


# ── the corpus test: this is the one that catches drift ─────────────────────

@needs_corpus
def test_no_silent_drops_anywhere_in_the_ingested_corpus():
    """Fails when CVR starts carrying a field nobody has triaged.

    A new field is not automatically a bug — but it must be either imported or
    explicitly declined, and this makes that a decision rather than an omission.
    """
    offenders = {}
    for cvr, doc, h in CORPUS:
        claims = extract_company(doc, raw_ref=h, observed_at=str(AS_OF))
        if not claims:
            continue
        for c in silent_drops(audit_company(doc, claims)):
            offenders.setdefault(c.path, []).append(cvr)
    assert not offenders, (
        "fields carrying data that produced no claim and no declared reason:\n"
        + "\n".join(f"  {p}  ({len(v)} companies, e.g. {v[0]})"
                    for p, v in sorted(offenders.items())))


@needs_corpus
def test_every_document_yields_the_facts_that_identify_it():
    """A company with no name or no status is an extraction failure, not a
    quiet company."""
    for cvr, doc, h in CORPUS:
        claims = extract_company(doc, raw_ref=h, observed_at=str(AS_OF))
        if not claims:
            continue
        preds = {c.predicate for c in claims}
        assert Predicate.HAS_NAME in preds, cvr
        assert Predicate.HAS_STATUS in preds, cvr
        composite = [c for c in claims if c.predicate is Predicate.HAS_STATUS
                     and c.qualifiers.get("composite")]
        assert composite, f"{cvr} has no sammensatStatus — the field that says bankrupt"


# ── conservation: the source's rows and the graph's claims must agree ───────

@needs_corpus
def test_every_filed_ownership_percentage_becomes_an_ownership_claim():
    """Counting is the check. A parser that reads the first membership and
    stops passes any test that only asserts 'some ownership was found'."""
    for cvr, doc, h in CORPUS:
        src = unwrap(doc)
        expected = 0
        for rel in src.get("deltagerRelation") or []:
            for org in rel.get("organisationer") or []:
                names = {(n.get("navn") or "").upper().replace(" ", "")
                         for n in (org.get("organisationsNavn") or [])}
                if not (names & {"EJERREGISTER", "REELLEEJERE"}):
                    continue
                for md in org.get("medlemsData") or []:
                    for a in md.get("attributter") or []:
                        if a.get("type") == "EJERANDEL_PROCENT":
                            expected += len(a.get("vaerdier") or [])
        claims = extract_company(doc, raw_ref=h, observed_at=str(AS_OF))
        got = sum(1 for c in claims if c.predicate is Predicate.OWNS
                  and c.qualifiers.get("share") is not None)
        assert got == expected, f"{cvr}: {expected} filed percentages, {got} claims"


@needs_corpus
def test_every_management_membership_becomes_a_role_claim():
    for cvr, doc, h in CORPUS:
        src = unwrap(doc)
        expected = sum(
            len(org.get("medlemsData") or [])
            for rel in (src.get("deltagerRelation") or [])
            for org in (rel.get("organisationer") or [])
            if org.get("hovedtype") == "LEDELSESORGAN")
        claims = extract_company(doc, raw_ref=h, observed_at=str(AS_OF))
        got = sum(1 for c in claims if c.predicate is Predicate.HAS_ROLE
                  and c.qualifiers.get("hovedtype") == "LEDELSESORGAN")
        # At least one claim per membership, not exactly one. A membership whose
        # FUNKTION changed — board member, then chair — is two dated roles, and
        # collapsing it to one undated role is what made 100% of `has_role`
        # claims invisible to the as-of filter, so a 2024 appointment was
        # visible to a 2016 screen. The contract defended here is that no
        # membership is silently dropped.
        assert got >= expected, f"{cvr}: {expected} memberships, {got} role claims"


@needs_corpus
def test_history_is_kept_not_collapsed_to_the_current_value():
    """CVR carries every version. Collapsing to 'newest' would throw away the
    timing signal the whole detector rests on."""
    multi = 0
    for cvr, doc, h in CORPUS:
        src = unwrap(doc)
        versions = len(src.get("beliggenhedsadresse") or [])
        if versions < 2:
            continue
        multi += 1
        claims = extract_company(doc, raw_ref=h, observed_at=str(AS_OF))
        got = sum(1 for c in claims if c.predicate is Predicate.REGISTERED_AT
                  and c.qualifiers.get("unit") == "registered_office")
        assert got == versions, f"{cvr}: {versions} address versions, {got} claims"
    assert multi >= 3, "corpus has too few multi-version companies to prove this"


@needs_corpus
def test_shares_are_decimal_all_the_way_through_ingestion():
    """A float share reintroduces the threshold-window defect that made one
    bunching window a different width from another."""
    for cvr, doc, h in CORPUS:
        for c in extract_company(doc, raw_ref=h, observed_at=str(AS_OF)):
            s = c.qualifiers.get("share")
            if s is not None:
                assert isinstance(s, Decimal), f"{cvr}: share is {type(s).__name__}"


@needs_corpus
def test_every_claim_can_be_traced_back_to_the_bytes_it_came_from():
    for cvr, doc, h in CORPUS:
        for c in extract_company(doc, raw_ref=h, observed_at=str(AS_OF)):
            assert c.raw_ref == h, f"{cvr}: claim not traceable to its payload"
            assert c.source_id == "cvr"


# ── the recovered fields stay recovered ─────────────────────────────────────

@needs_corpus
def test_the_fields_the_contract_recovered_are_still_imported():
    """BØRSNOTERET, GENOPTAGELSE_TVANGSOPLØSNING and OFFENTLIG_EJERBOG were all
    present in the data and read by nothing until the corpus audit found them."""
    found = set()
    for cvr, doc, h in CORPUS:
        for c in extract_company(doc, raw_ref=h, observed_at=str(AS_OF)):
            if c.predicate in (Predicate.IS_LISTED,
                               Predicate.REINSTATED_AFTER_DISSOLUTION,
                               Predicate.PUBLISHES_SHAREHOLDER_REGISTER,
                               Predicate.SUPERVISORY_CATEGORY):
                found.add(c.predicate)
    assert Predicate.IS_LISTED in found
    assert Predicate.REINSTATED_AFTER_DISSOLUTION in found
    assert Predicate.PUBLISHES_SHAREHOLDER_REGISTER in found


# ── the audit itself behaves ────────────────────────────────────────────────

def test_a_field_with_data_and_no_claim_and_no_reason_is_a_silent_drop():
    doc = {"Vrvirksomhed": {"cvrNummer": 99000147,
                            "navne": [{"navn": "X"}],
                            "somethingBrandNew": [{"a": 1}]}}
    cov = audit_company(doc, extract_company(doc, raw_ref="r"))
    drops = {c.path for c in silent_drops(cov)}
    assert "somethingBrandNew" in drops


def test_an_empty_field_is_not_a_drop():
    """An absent field is not an unimported one."""
    doc = {"Vrvirksomhed": {"cvrNummer": 99000147, "navne": [{"navn": "X"}],
                            "somethingBrandNew": []}}
    assert silent_drops(audit_company(doc, extract_company(doc, raw_ref="r"))) == []


def test_a_required_field_carrying_data_but_producing_nothing_fails():
    doc = {"Vrvirksomhed": {"cvrNummer": 99000147, "navne": [{"navn": "X"}]}}
    claims = [c for c in extract_company(doc, raw_ref="r")
              if c.predicate is not Predicate.HAS_NAME]
    cov = {c.path: c for c in audit_company(doc, claims)}
    assert cov["navne"].present and not cov["navne"].imported


@needs_corpus
def test_undated_claims_stay_within_their_measured_share():
    """The guard that stops a fourth hindsight leak.

    Three separate leaks had one cause and none was found by review:
    `sammensatStatus` filed with no period; `medlemsData` whose dates sit one
    level down on each FUNKTION value, leaving 100% of `has_role` claims
    undated so a 2024 board appointment was visible to a 2016 screen; and a
    liquidator appointed BY a bankruptcy showing as in office a year before it.

    `claims.at()` admits an undated claim at every as-of date. That is right for
    a present-day screen — an undated board seat is still a board seat — and is
    hindsight in a backtest, which is why `at(..., require_period=True)` exists.

    **The ceiling is not zero, because the register genuinely files some facts
    without dates.** Measured over 329 full documents on 2026-08-28. A rate that
    climbs means an extractor stopped reading a period it used to read, which is
    exactly how `has_role` reached 100% without anyone noticing.
    """
    from gleipnir.claims import _undated_by_nature

    # predicate -> measured undated share, 2026-08-28. Two are undated by
    # nature: a company is founded once, and a headcount is filed for a year
    # rather than holding from a date.
    CEILING = {
        "has_role": 0.05,             # measured 3.00%
        "audited_by": 0.05,           # measured 2.45%
        "has_status": 0.02,           # measured 0.71%
        "audit_waived": 0.02,         # measured 0.49%
        "supervisory_category": 0.30,  # measured 14.29%, n=7
    }
    total, undated = Counter(), Counter()
    for cvr, doc, h in CORPUS:
        for c in extract_company(doc, raw_ref=h, observed_at=str(AS_OF)):
            total[c.predicate] += 1
            if c.valid_from is None:
                undated[c.predicate] += 1

    allowed = _undated_by_nature()
    over, unlisted = [], []
    for predicate, count in sorted(undated.items()):
        if predicate in allowed:
            continue
        share = count / total[predicate]
        ceiling = CEILING.get(predicate.value)
        if ceiling is None:
            unlisted.append(f"{predicate.value} ({share:.1%}, {count}/{total[predicate]})")
        elif share > ceiling:
            over.append(f"{predicate.value} {share:.1%} > {ceiling:.0%}")

    assert not unlisted, (
        "a predicate started arriving undated, so it is visible at every as-of "
        "date: " + ", ".join(unlisted) + " — date it in the extractor, or add it "
        "here with the measured share, or to claims._undated_by_nature()")
    assert not over, "undated share rose above its measured ceiling: " + ", ".join(over)
