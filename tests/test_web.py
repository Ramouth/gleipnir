"""The workbench serves the library's answers, and adds none of its own.

A UI is where the refusals get lost: a badge computed in a template, a "refresh"
button that spends quota, a coverage hole rendered as a clean page. These assert
that none of that is possible, against a store built for the test.
"""
import json
from datetime import date

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient           # noqa: E402

from gleipnir.investigation import (                # noqa: E402
    AnswerKind, QuestionContract, ReviewOutcome, ReviewPacket, ScreenMandate,
    store_review_packet,
)
from gleipnir.rawstore import RawStore              # noqa: E402
from gleipnir.web.app import create_app             # noqa: E402
from gleipnir.web.workbench import Workbench        # noqa: E402

CVR = "99000147"


def doc(name="Fixture ApS"):
    """Stored as a full Elasticsearch envelope — the shape 19 of the 329 real
    cached companies use."""
    return {"hits": {"hits": [{"_source": {"Vrvirksomhed": {
        "cvrNummer": int(CVR),
        "navne": [{"navn": name, "periode": {"gyldigFra": "2015-01-01", "gyldigTil": None}}],
        "virksomhedsstatus": [{"status": "NORMAL",
                               "periode": {"gyldigFra": "2015-01-01", "gyldigTil": None}}],
        "virksomhedMetadata": {
            "sammensatStatus": "NORMAL", "stiftelsesDato": "2015-01-01",
            "nyesteNavn": {"navn": name},
            "nyesteVirksomhedsform": {"langBeskrivelse": "Anpartsselskab"}},
        "attributter": [],
        "deltagerRelation": [],
    }}}]}}


@pytest.fixture
def store(tmp_path):
    s = RawStore(tmp_path)
    s.put(payload=json.dumps(doc()).encode(), source="cvr",
          resource_type="virksomhed", resource_id=CVR, http_status=200,
          request_params={})
    return s


@pytest.fixture
def client(store):
    return TestClient(create_app(store))


# ── the index ───────────────────────────────────────────────────────────────

def test_the_index_lists_what_is_in_the_store(client):
    r = client.get("/")
    assert r.status_code == 200
    assert CVR in r.text and "Fixture ApS" in r.text


def test_the_filter_narrows_without_ranking(client):
    assert "Fixture ApS" in client.get("/?q=fixture").text
    assert "Fixture ApS" not in client.get("/?q=zzzz").text
    assert "Nothing in the store matches" in client.get("/?q=zzzz").text


def test_the_partial_and_the_page_agree(client):
    """htmx is an enhancement: the partial it swaps in is the same markup the
    full page renders, so the page works unchanged without JavaScript."""
    assert "Fixture ApS" in client.get("/partials/companies?q=fix").text


# ── one company ─────────────────────────────────────────────────────────────

def test_a_company_page_renders_its_facts(client):
    r = client.get(f"/c/{CVR}")
    assert r.status_code == 200
    assert "Fixture ApS" in r.text
    assert "Anpartsselskab" in r.text
    # The four-valued headings, not the raw enum values.
    assert "NO CONNECTED SOURCE COVERS IT" in r.text


def test_the_colour_is_the_screens_and_not_the_templates(client, store):
    """The badge must equal `Screen.colour` exactly. A page that counted facts
    would be concluding on the reader's behalf."""
    bench = Workbench(store=store)
    colour = bench.screen(CVR, bench.sheet(CVR, None).as_of).colour
    assert f'class="badge c-{colour}"' in client.get(f"/c/{CVR}").text


def test_a_company_not_in_the_store_is_a_coverage_statement(client):
    r = client.get("/c/10000009")
    assert r.status_code == 404
    assert "not in the raw store" in r.text
    # And it must not read as a clean result.
    assert "statement about our coverage" in r.text


def test_reading_at_another_date_changes_the_sheet(client):
    early = client.get(f"/c/{CVR}?as_of=2015-02-01")
    assert "<strong>2015-02-01</strong>" in early.text
    assert "as specified" in early.text


def test_an_unparseable_date_falls_back_rather_than_500ing(client):
    assert client.get(f"/c/{CVR}?as_of=not-a-date").status_code == 200


# ── provenance ──────────────────────────────────────────────────────────────

def test_every_fact_can_be_opened(client, store):
    """A citation a reader cannot open is one they have to take on trust."""
    h = store.fetches()[0].content_hash
    assert f"/evidence/{h}" in client.get(f"/c/{CVR}").text
    r = client.get(f"/evidence/{h}")
    assert r.status_code == 200
    assert "Fixture ApS" in r.text and "virksomhed" in r.text


def test_an_unknown_hash_does_not_pretend_to_have_bytes(client):
    r = client.get("/evidence/" + "0" * 64)
    assert r.status_code == 200
    assert "No blob on disk under this hash" in r.text


# ── the analyst's write ─────────────────────────────────────────────────────

def test_recording_a_judgement_appends_and_pins_it(client, store):
    r = client.post(f"/c/{CVR}/observation", data={
        "analyst": "A. Reviewer", "verdict": "green",
        "basis": "officers hold engineering credentials consistent with the business",
        "sources": "https://example.invalid/profile",
        "suppresses": ["audit_waived"], "as_of": "2020-01-01"},
        follow_redirects=False)
    assert r.status_code == 303

    obs = Workbench(store=store).observations(CVR)
    assert len(obs) == 1
    assert obs[0].analyst == "A. Reviewer"
    assert list(obs[0].suppresses) == ["audit_waived"]
    # Pinned to what was on file, which is what makes it go stale on its own.
    assert set(obs[0].pinned) == Workbench(store=store).hashes_for(CVR)
    assert "in force" in client.get(f"/c/{CVR}").text


def test_an_unattributed_judgement_is_not_recorded(client, store):
    client.post(f"/c/{CVR}/observation",
                data={"analyst": "  ", "verdict": "green", "basis": "trust me"},
                follow_redirects=False)
    assert Workbench(store=store).observations(CVR) == []


def test_an_invented_verdict_is_not_recorded(client, store):
    client.post(f"/c/{CVR}/observation",
                data={"analyst": "A", "verdict": "cleared", "basis": "b"},
                follow_redirects=False)
    assert Workbench(store=store).observations(CVR) == []


def test_a_withdrawal_suppresses_nothing(client, store):
    client.post(f"/c/{CVR}/observation", data={
        "analyst": "A", "verdict": "withdraw", "basis": "retracted",
        "suppresses": ["audit_waived"]}, follow_redirects=False)
    obs = Workbench(store=store).observations(CVR)
    assert obs[-1].verdict == "withdraw" and list(obs[-1].suppresses) == []


def test_a_stale_flag_is_reported_and_not_applied(client, store):
    client.post(f"/c/{CVR}/observation", data={
        "analyst": "A", "verdict": "green", "basis": "checked",
        "suppresses": ["audit_waived"]}, follow_redirects=False)
    # The documents the judgement was pinned to move.
    store.put(payload=json.dumps(doc("Fixture ApS renamed")).encode(), source="cvr",
              resource_type="virksomhed", resource_id=CVR, http_status=200,
              request_params={})
    page = client.get(f"/c/{CVR}").text
    assert "stale" in page and "reported and not" in page


# ── the reviewer's write ────────────────────────────────────────────────────

def packet(store):
    mandate = ScreenMandate(
        id="M1", subject_key=CVR, purpose="screen", consent_ref="ref",
        granted_on=date(2020, 1, 1), expires_on=date(2030, 1, 1),
        permitted_sources=("media",), permitted_subject_kinds=("company",))
    contract = QuestionContract(
        id="Q1", version="1", question="does the passage name the subject?",
        trigger_predicates=("designated_holder_candidate",), source="media",
        subject_kind="company", required_fields=("name",),
        answer_kind=AnswerKind.ENTITY_MATCH, decision_effect="none",
        coverage_rule="one source", requires_human_review=True)
    p = ReviewPacket.create(
        mandate=mandate, contract=contract, subject_key=CVR, as_of=date(2021, 1, 1),
        raw_ref="deadbeef", quote="the company was named",
        context="In the report, the company was named among the parties.",
        reason="an approved question retrieved this passage")
    return store_review_packet(store, p)


def test_the_queue_shows_a_pending_packet(client, store):
    packet(store)
    r = client.get("/queue")
    assert "the company was named" in r.text
    assert "an approved question retrieved this passage" in r.text


def test_reviewing_appends_an_outcome_and_never_edits(client, store):
    h = packet(store)
    r = client.post(f"/queue/{h}", data={
        "outcome": ReviewOutcome.IRRELEVANT.value, "reviewer": "B. Reviewer",
        "rationale": "different company of the same name"}, follow_redirects=False)
    assert r.status_code == 303

    bench = Workbench(store=store)
    outcomes = [p.outcome for _, p in bench.review_packets()]
    # Both records survive: the pending packet and the review of it.
    assert ReviewOutcome.PENDING in outcomes
    assert ReviewOutcome.IRRELEVANT in outcomes
    assert "different company of the same name" in client.get("/queue").text


def test_the_queue_offers_only_the_four_lawful_outcomes(client, store):
    packet(store)
    page = client.get("/queue").text
    for outcome in ReviewOutcome:
        if outcome is ReviewOutcome.PENDING:
            assert f'value="{outcome.value}"' not in page
        else:
            assert f'value="{outcome.value}"' in page


# ── the roster, and the budget ──────────────────────────────────────────────

def test_the_roster_page_states_who_may_colour(client):
    r = client.get("/agents")
    assert r.status_code == 200
    assert "Who can put a colour on a company?" in r.text
    assert "oracle" in r.text and "may not emit_claim" in r.text


def test_the_workbench_holds_no_registry_budget(client, store):
    """A page a click can drive must not be able to burn a rate-limited
    system-to-system agreement."""
    assert Workbench(store=store).budget == 0
    assert client.get("/health").json()["budget"] == 0


def test_no_route_fetches_anything(store, monkeypatch):
    """Belt and braces: the screen is handed a client of None, so there is no
    object a route could spend through even if the budget moved."""
    import gleipnir.web.workbench as wb

    seen = {}
    real = wb.run_screen

    def spy(cvr, sources, **kw):
        seen["client"], seen["budget"] = sources.cvr_client, sources.budget
        return real(cvr, sources, **kw)

    monkeypatch.setattr(wb, "run_screen", spy)
    TestClient(create_app(store)).get(f"/c/{CVR}")
    assert seen == {"client": None, "budget": 0}


def test_the_store_moving_drops_the_caches(store):
    bench = Workbench(store=store)
    assert len(bench.companies()) == 1
    store.put(payload=json.dumps(doc("Second ApS")).encode(), source="cvr",
              resource_type="virksomhed", resource_id="10000009", http_status=200,
              request_params={})
    assert len(bench.companies()) == 2
