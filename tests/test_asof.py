"""As-of screening: the backtest primitive.

`poc.md` §3 requires reconstructing a structure **as it looked before exposure**,
and `architecture.md` §9 requires a report to be reproducible from a pinned
data state. Both rest on one property that has never been asserted: a screen at
a past date must not see anything that had not happened yet.

If a 2024 claim reaches a 2022 screen, every backtest built on it is worthless —
and it would fail in the flattering direction, because hindsight makes the
signals look better than they were.
"""
import os
from datetime import date

import pytest

from gleipnir.claims import Claim, EntityRef, EpistemicTier, Predicate, at, latest
from gleipnir.config import settings
from gleipnir.extract.cvr import extract_company
from gleipnir.rawstore import RawStore
from gleipnir.screen import Sources, run

CO = EntityRef(kind="company", key="99000147")


def own(holder, share, frm, to=None):
    return Claim(subject=EntityRef("company", holder, holder), predicate=Predicate.OWNS,
                 object=CO, source_id="cvr",
                 epistemic_tier=EpistemicTier.SELF_DECLARED_TO_REGISTRY, raw_ref="r",
                 valid_from=frm, valid_to=to,
                 qualifiers={"share": __import__("decimal").Decimal(share)})


#: A company in the local raw store, named by the environment so that no real
#: identifier is committed. Unset or not ingested: the corpus tests skip.
CORPUS_CVR = os.environ.get("GLEIPNIR_CORPUS_CVR", "")


def corpus():
    store = RawStore(settings.raw_store_path)
    rec = store.latest("cvr", "virksomhed", CORPUS_CVR) if CORPUS_CVR else None
    return store, rec


needs_corpus = pytest.mark.skipif(
    corpus()[1] is None,
    reason="set GLEIPNIR_CORPUS_CVR to a company in the local raw store")


# ── the window ──────────────────────────────────────────────────────────────

def test_a_claim_that_had_not_started_is_invisible():
    claims = [own("a", "0.50", date(2020, 1, 1)),
              own("b", "0.50", date(2025, 1, 1))]
    assert {c.subject.key for c in at(claims, Predicate.OWNS, date(2022, 1, 1))} == {"a"}


def test_a_claim_that_had_already_ended_is_invisible():
    claims = [own("a", "0.50", date(2020, 1, 1), date(2021, 1, 1)),
              own("b", "0.50", date(2021, 1, 2))]
    assert {c.subject.key for c in at(claims, Predicate.OWNS, date(2022, 1, 1))} == {"b"}


def test_the_boundary_days_are_inclusive():
    c = own("a", "0.50", date(2020, 1, 1), date(2020, 12, 31))
    assert at([c], Predicate.OWNS, date(2020, 1, 1))
    assert at([c], Predicate.OWNS, date(2020, 12, 31))
    assert not at([c], Predicate.OWNS, date(2021, 1, 1))


def test_latest_picks_the_version_in_force_not_the_newest_ever():
    claims = [own("a", "0.50", date(2020, 1, 1), date(2023, 1, 1)),
              own("a", "1.00", date(2023, 1, 2))]
    v = latest(claims, Predicate.OWNS, date(2022, 6, 1))
    assert v.qualifiers["share"] == __import__("decimal").Decimal("0.50")


# ── no hindsight, on the real corpus ────────────────────────────────────────

@needs_corpus
def test_a_past_screen_sees_nothing_that_had_not_happened_yet():
    """The property the whole backtest rests on. It would fail in the
    flattering direction: hindsight makes signals look better than they were."""
    store, rec = corpus()
    claims = extract_company(store.get_json(rec.content_hash), raw_ref=rec.content_hash)
    for as_of in (date(2022, 1, 1), date(2023, 12, 1), date(2024, 9, 1)):
        for pred in (Predicate.OWNS, Predicate.HAS_ROLE, Predicate.HAS_CAPITAL,
                     Predicate.REGISTERED_AT, Predicate.HAS_VOTING_RIGHTS):
            for c in at(claims, pred, as_of):
                assert c.valid_from is None or c.valid_from <= as_of, (
                    f"{pred} claim starting {c.valid_from} leaked into an "
                    f"as-of {as_of} screen")
                assert c.valid_to is None or c.valid_to >= as_of, (
                    f"{pred} claim ended {c.valid_to}, before as-of {as_of}")


@needs_corpus
def test_the_ownership_picture_actually_differs_by_date():
    """Sanity: if every date gave the same answer the test above would pass
    vacuously."""
    store, rec = corpus()
    claims = extract_company(store.get_json(rec.content_hash), raw_ref=rec.content_hash)
    seen = set()
    for as_of in (date(2022, 1, 1), date(2023, 12, 1), date(2026, 8, 27)):
        holders = frozenset(
            (c.subject.key, str(c.qualifiers.get("share")))
            for c in at(claims, Predicate.OWNS, as_of))
        seen.add(holders)
    assert len(seen) == 3, "the corpus company must have a real ownership history"


@needs_corpus
def test_a_screen_is_reproducible_at_a_pinned_date():
    """architecture.md §9: given a report, reconstruct the exact state."""
    store, _ = corpus()
    src = Sources(store=store, budget=0)
    a = run(CORPUS_CVR, src, as_of=date(2023, 12, 1))
    b = run(CORPUS_CVR, src, as_of=date(2023, 12, 1))
    assert [(c.predicate, c.value, c.statement) for c in a.chain] == \
           [(c.predicate, c.value, c.statement) for c in b.chain]
    assert a.colour is b.colour


@needs_corpus
def test_two_different_dates_give_two_different_screens():
    store, _ = corpus()
    src = Sources(store=store, budget=0)
    old = run(CORPUS_CVR, src, as_of=date(2022, 1, 1))
    new = run(CORPUS_CVR, src, as_of=date(2026, 8, 27))
    assert {c.statement for c in old.chain} != {c.statement for c in new.chain}


@needs_corpus
def test_a_screen_before_the_company_existed_finds_no_ownership():
    """Incorporated 2021-06-07. A 2019 screen must report nothing, not the
    current picture."""
    store, src = corpus()[0], None
    s = run("99000147", Sources(store=store, budget=0), as_of=date(2019, 1, 1))
    owners = [c for c in s.chain if c.predicate == "effective_owner"]
    assert owners == []


def test_a_screen_without_a_designation_list_says_so(tmp_path):
    """Returning FALSE would report 'no designation nexus' about a check that
    never ran. It is a material limit — the screen greys."""
    import json
    from gleipnir.finding import Colour
    store = RawStore(tmp_path)
    store.put(payload=json.dumps({"Vrvirksomhed": {
        "cvrNummer": 99000147, "navne": [{"navn": "X"}],
        "virksomhedsstatus": [{"status": "NORMAL", "periode": {"gyldigFra": "2020-01-01"}}],
        "virksomhedMetadata": {"sammensatStatus": "NORMAL"}}}).encode(),
        source="cvr", resource_type="virksomhed", resource_id="99000147",
        http_status=200, request_params={})
    s = run("99000147", Sources(store=store, sanctions=None, budget=0),
            as_of=date(2026, 8, 27))
    assert any("no designation list" in u for u in s.unknowable)
    assert any("no designation list" in b for b in s.blocked_on)
    assert s.colour is Colour.GREY
