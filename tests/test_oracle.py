"""The bounded oracle.

Every test here pins a property that keeps the model from becoming an assessor:
one span in, closed output, quotes verified against the source, cache keyed so a
prompt change invalidates visibly.
"""
import json

import pytest

from gleipnir.oracle import (
    EXTRACT_SCHEMA, MAX_SPAN_CHARS, PROMPT_VERSION, RELATIONS, Question,
    cached_extract, extract, verify_quotes,
)
from gleipnir.rawstore import RawStore

SPAN = (
    "Alexandra Doe is a social media influencer living in Examplestan. She produced "
    "content paid for by Example Media, the legal entity behind the Examplestan state "
    "outlet Example Today (ET). Alexandra Doe and her husband also received "
    "grants from the state's Presidential Foundation for Example Initiatives, "
    "through the public relations agency \"Boundless\"."
)


class Stub:
    """Returns a canned payload and records exactly what it was shown."""

    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def create(self, **kw):
        self.calls.append(kw)
        text = json.dumps(self.payload)
        return type("R", (), {"content": [type("B", (), {"type": "text", "text": text})()]})()


def ent(name, quote, relation="mentioned_only", kind="organisation"):
    return {"name": name, "kind": kind, "relation": relation, "quote": quote}


# ── one span in, nothing else ───────────────────────────────────────────────

def test_the_prompt_contains_the_span_and_nothing_else():
    """Many-to-many has to be structurally impossible, not discouraged."""
    s = Stub({"entities": []})
    extract(SPAN, s, source_ref="abc")
    kw = s.calls[0]
    assert kw["messages"] == [{"role": "user", "content": SPAN}]
    assert len(kw["messages"]) == 1


def test_an_oversized_span_is_refused_rather_than_truncated():
    """Silently truncating would make the model's answer unverifiable against
    the source it was supposedly reading."""
    s = Stub({"entities": []})
    with pytest.raises(ValueError, match="chunk it"):
        extract("x" * (MAX_SPAN_CHARS + 1), s, source_ref="abc")
    assert s.calls == []


def test_output_is_constrained_by_a_closed_schema():
    s = Stub({"entities": []})
    extract(SPAN, s, source_ref="abc")
    fmt = s.calls[0]["output_config"]["format"]
    assert fmt["type"] == "json_schema"
    item = fmt["schema"]["properties"]["entities"]["items"]
    assert item["additionalProperties"] is False
    assert set(item["properties"]["relation"]["enum"]) == set(RELATIONS)
    assert set(item["properties"]["kind"]["enum"]) == {
        "person", "company", "organisation", "other"}


# ── the quote check is what makes it verifiable ─────────────────────────────

def test_a_quote_absent_from_the_span_is_dropped():
    """The model cannot assert something the source does not say."""
    s = Stub({"entities": [
        ent("Example Media", "content paid for by Example Media"),
        ent("Rosneft", "Rosneft financed the operation"),   # not in the span
    ]})
    v = extract(SPAN, s, source_ref="abc")
    assert [e["name"] for e in v.answer["entities"]] == ["Example Media"]
    assert v.dropped[0]["name"] == "Rosneft"
    assert "not present" in v.dropped[0]["_reason"]


def test_a_quote_that_does_not_contain_its_own_entity_is_dropped():
    s = Stub({"entities": [ent("Example Energy", "living in Examplestan")]})
    v = extract(SPAN, s, source_ref="abc")
    assert v.answer["entities"] == []
    assert "name not inside" in v.dropped[0]["_reason"]


def test_line_wrapping_in_the_source_does_not_break_verification():
    """Official texts wrap mid-sentence; whitespace is normalised on both sides
    and nothing else is."""
    wrapped = "Grants came through the agency\n   \"Boundless\", registered abroad."
    kept, dropped = verify_quotes(
        wrapped, [ent("Boundless", 'through the agency "Boundless"')])
    assert len(kept) == 1 and not dropped


def test_verification_is_case_insensitive_but_not_fuzzy():
    kept, _ = verify_quotes(SPAN, [ent("EXAMPLE MEDIA", "PAID FOR BY EXAMPLE MEDIA")])
    assert len(kept) == 1
    _, dropped = verify_quotes(SPAN, [ent("Example-Media", "paid for by Example-Media")])
    assert len(dropped) == 1, "a near-miss is still a miss"


def test_unparseable_model_output_yields_nothing_rather_than_raising():
    class Broken:
        def create(self, **kw):
            return type("R", (), {"content": [
                type("B", (), {"type": "text", "text": "sorry, I cannot"})()]})()
    assert extract(SPAN, Broken(), source_ref="abc").answer["entities"] == []


# ── cache ───────────────────────────────────────────────────────────────────

def test_the_cache_key_is_content_addressed():
    a = Question("EXTRACT", {"span_sha": "x", "source_ref": "y"})
    b = Question("EXTRACT", {"source_ref": "y", "span_sha": "x"})
    assert a.id == b.id, "key ordering must not change the id"


def test_a_prompt_change_invalidates_the_cache_visibly():
    a = Question("EXTRACT", {"span_sha": "x", "source_ref": "y"})
    b = Question("EXTRACT", {"span_sha": "x", "source_ref": "y"},
                 prompt_version="extract/9999-99-99.9")
    assert a.id != b.id


def test_the_same_text_from_two_sources_is_two_questions():
    a = Question("EXTRACT", {"span_sha": "x", "source_ref": "doc1"})
    b = Question("EXTRACT", {"span_sha": "x", "source_ref": "doc2"})
    assert a.id != b.id


def test_a_second_call_is_served_from_the_store(tmp_path):
    store = RawStore(tmp_path)
    s = Stub({"entities": [ent("Example Media", "content paid for by Example Media")]})
    v1, cached1 = cached_extract(SPAN, s, store, source_ref="abc")
    v2, cached2 = cached_extract(SPAN, s, store, source_ref="abc")
    assert (cached1, cached2) == (False, True)
    assert len(s.calls) == 1, "the second call must not reach the model"
    assert v1.answer == v2.answer and v1.question_id == v2.question_id


def test_a_verdict_records_what_was_asked_and_by_which_model(tmp_path):
    store = RawStore(tmp_path)
    s = Stub({"entities": []})
    cached_extract(SPAN, s, store, source_ref="abc")
    rec = store.fetches()[-1]
    assert rec.source == "oracle" and rec.resource_type == "verdict"
    assert rec.request_params["prompt_version"] == PROMPT_VERSION
    assert rec.request_params["type"] == "EXTRACT"


# ── what it must never be asked ─────────────────────────────────────────────

def test_the_system_prompt_forbids_assessment():
    from gleipnir.oracle import SYSTEM
    low = SYSTEM.lower()
    assert "not assessing" in low
    assert "do not decide whether an entity is suspicious" in low
    # And it names the categories that produced the deterministic pass's noise.
    for junk in ("treaties", "statutes", "sanctions programmes", "nationalities"):
        assert junk in low


# ── claims: named is not designated ─────────────────────────────────────────

def test_extracted_entities_become_claims_that_cannot_propagate():
    """An official act named 'Boundless' in an official act; the agency carries no
    listing. A company contracting with it hits no sanctions screen, and a
    nexus built through this edge may never colour a finding."""
    from gleipnir.claims import EpistemicTier, Predicate
    from gleipnir.oracle import Verdict, to_claims

    v = Verdict(question_id="q1", model="claude-opus-5", created_at="2026-08-27T00:00:00Z",
                answer={"entities": [ent("Boundless", 'the agency "Boundless"',
                                         relation="intermediary_for")]})
    c = to_claims(v, target_key="Q133403322", target_label="Alexandra Doe",
                  authority="EU Council Official Journal", programmes=("EU-RUSDA",),
                  listed_on="2026-06-15", raw_ref="blob1")[0]
    assert c.predicate is Predicate.NAMED_IN_DESIGNATION
    assert c.epistemic_tier is EpistemicTier.THIRD_PARTY
    assert c.qualifiers["propagates"] is False
    assert c.qualifiers["relation"] == "intermediary_for"
    assert c.qualifiers["quote"] == 'the agency "Boundless"'
    assert c.qualifiers["authority"] == "EU Council Official Journal"
    assert c.object.key == "Q133403322"


def test_a_claim_carries_the_verbatim_quote_and_its_provenance():
    from gleipnir.oracle import Verdict, to_claims
    v = Verdict(question_id="q9", model="claude-opus-5", created_at="t",
                answer={"entities": [ent("Example Media", "paid for by Example Media",
                                         relation="funds")]})
    c = to_claims(v, target_key="Q1", target_label="X", authority="EU",
                  programmes=("EU-RUSDA",), listed_on="2026-06-15",
                  raw_ref="blob2", observed_at="2026-08-27")[0]
    assert c.raw_ref == "blob2" and c.observed_at == "2026-08-27"
    assert c.qualifiers["question_id"] == "q9"
    assert c.qualifiers["extracted_by"] == "claude-opus-5"
    assert str(c.valid_from) == "2026-06-15"


def test_organisations_and_companies_share_the_company_node_kind():
    from gleipnir.oracle import Verdict, to_claims
    v = Verdict(question_id="q", model="m", created_at="t", answer={"entities": [
        ent("A Ltd", "quote naming A Ltd", kind="company"),
        ent("B Foundation", "quote naming B Foundation", kind="organisation"),
        ent("C Person", "quote naming C Person", kind="person"),
    ]})
    kinds = [c.subject.kind for c in to_claims(
        v, target_key="Q", target_label="X", authority="EU", programmes=(),
        listed_on=None, raw_ref="r")]
    assert kinds == ["company", "company", "person"]
