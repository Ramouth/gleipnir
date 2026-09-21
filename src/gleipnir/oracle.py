"""The bounded oracle — the only place a language model touches this system.

`architecture.md` §7.1: the model is never in a many-to-many relationship with
the data. Every call takes exactly one bounded question about one span and
returns one small typed answer.

**Why a model is used here at all, and only here.** Iteration measurements
settled it: a deterministic pass over 16,424 designation statements produced
2,331 candidate entity names whose most frequent hits were `Treaty of Friendship`
(2,140×), `Bureau of Industry and Security` (1,421×) and `Secretary of State`
(1,062×). Regex cannot tell an entity from a phrase. That is the job — turning
official prose into typed references — and it is the only job.

**Four properties the design enforces, not hopes for:**

1. **One span in.** The prompt contains the span and nothing else. No other
   entity, no other verdict, no accumulated context. Many-to-many is
   structurally impossible rather than discouraged.
2. **Closed output.** A JSON schema with `additionalProperties: false` and a
   fixed relation enum. Anything outside the vocabulary cannot be returned.
3. **Every claim carries a verbatim quote, and the quote is verified in code.**
   A returned quote that does not appear in the span is dropped. The model
   cannot assert something the source does not say — that check is deterministic
   and runs after every call.
4. **Cached and replayable.** The cache key is
   `sha256(question_type ‖ inputs ‖ prompt_version)`, so a prompt change
   invalidates visibly and a report can never drift from what the current prompt
   would say.

**What the model is never asked**: whether something is suspicious, to combine
evidence, to rank findings, to resolve a contradiction, or to read an entity and
opine.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Protocol

MODEL = "claude-opus-5"
PROMPT_VERSION = "extract/2026-08-27.1"
MAX_SPAN_CHARS = 6000

#: How a named entity relates to the subject of the statement. Closed: a
#: relation outside this set cannot be returned, and adding one is a schema
#: change rather than a prompt tweak.
RELATIONS = (
    "funded_by", "funds", "employed_by", "employs", "owned_by", "owns",
    "controls", "controlled_by", "associate_of", "intermediary_for",
    "member_of", "supplied_by", "supplies", "mentioned_only",
)

EXTRACT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["entities"],
    "properties": {
        "entities": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["name", "kind", "relation", "quote"],
                "properties": {
                    "name": {"type": "string", "minLength": 2, "maxLength": 120},
                    "kind": {"type": "string",
                             "enum": ["person", "company", "organisation", "other"]},
                    "relation": {"type": "string", "enum": list(RELATIONS)},
                    "quote": {"type": "string", "minLength": 8, "maxLength": 400},
                },
            },
        }
    },
}

SYSTEM = """You transcribe named entities out of official sanctions texts into a schema.

You are not assessing anything. You do not decide whether an entity is suspicious, \
important, or culpable. You transcribe what the text names.

Rules:
- Return only named organisations, companies and natural persons that the text \
names as distinct from its subject.
- Do NOT return: treaties, statutes, regulations, government departments, courts, \
sanctions programmes, place names, nationalities, or generic phrases.
- `quote` must be copied VERBATIM from the text, character for character, and must \
contain the entity name. A quote that is not literally present will be discarded.
- `relation` describes how the named entity relates to the subject of the text. \
Use `mentioned_only` when the text names it without stating a relationship.
- If the text names no such entity, return an empty list."""


class Client(Protocol):
    """Just enough of the Anthropic SDK surface to be stubbed in tests."""

    def create(self, **kwargs: Any) -> Any: ...


@dataclass(frozen=True)
class Question:
    type: str
    inputs: dict[str, Any]
    prompt_version: str = PROMPT_VERSION

    @property
    def id(self) -> str:
        payload = json.dumps(
            {"type": self.type, "inputs": self.inputs, "pv": self.prompt_version},
            sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(payload.encode()).hexdigest()


@dataclass(frozen=True)
class Verdict:
    question_id: str
    answer: dict[str, Any]
    model: str
    created_at: str
    dropped: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"question_id": self.question_id, "answer": self.answer,
                "model": self.model, "created_at": self.created_at,
                "dropped": self.dropped}


def _normalise_ws(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def verify_quotes(span: str, entities: list[dict[str, Any]]
                  ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Keep only entities whose quote is literally present in the span.

    Whitespace is normalised on both sides — official texts wrap mid-sentence —
    but nothing else is. A quote the source does not contain is not evidence of
    anything, and this is the check that makes the model's output verifiable
    rather than trusted.
    """
    hay = _normalise_ws(span).casefold()
    kept, dropped = [], []
    for e in entities:
        q = _normalise_ws(str(e.get("quote", "")))
        name = _normalise_ws(str(e.get("name", "")))
        if not q or q.casefold() not in hay:
            dropped.append({**e, "_reason": "quote not present in span"})
        elif name.casefold() not in q.casefold():
            dropped.append({**e, "_reason": "name not inside its own quote"})
        else:
            kept.append(e)
    return kept, dropped


def extract(span: str, client: Client, *, source_ref: str,
            model: str = MODEL) -> Verdict:
    """EXTRACT(span) -> typed entity references. One span, one answer.

    `source_ref` is the content hash of the document the span came from; it goes
    into the cache key so the same text from two sources is two questions.
    """
    if len(span) > MAX_SPAN_CHARS:
        raise ValueError(
            f"span is {len(span)} chars, over the {MAX_SPAN_CHARS} limit — "
            "chunk it rather than letting one call see a whole corpus")
    q = Question(type="EXTRACT",
                 inputs={"span_sha": hashlib.sha256(span.encode()).hexdigest(),
                         "source_ref": source_ref})
    resp = client.create(
        model=model,
        max_tokens=4000,
        system=SYSTEM,
        output_config={"format": {"type": "json_schema", "schema": EXTRACT_SCHEMA}},
        messages=[{"role": "user", "content": span}],
    )
    text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
    try:
        answer = json.loads(text)
    except json.JSONDecodeError:
        answer = {"entities": []}
    kept, dropped = verify_quotes(span, answer.get("entities") or [])
    return Verdict(question_id=q.id, answer={"entities": kept}, model=model,
                   created_at=datetime.now(timezone.utc).isoformat(), dropped=dropped)


def cached_extract(span: str, client: Client, store, *, source_ref: str,
                   model: str = MODEL) -> tuple[Verdict, bool]:
    """`extract`, backed by the raw store. Returns (verdict, was_cached)."""
    q = Question(type="EXTRACT",
                 inputs={"span_sha": hashlib.sha256(span.encode()).hexdigest(),
                         "source_ref": source_ref})
    hit = store.latest("oracle", "verdict", q.id)
    if hit:
        return Verdict(**store.get_json(hit.content_hash)), True
    v = extract(span, client, source_ref=source_ref, model=model)
    store.put(payload=json.dumps(v.to_dict(), sort_keys=True,
                                 ensure_ascii=False).encode(),
              source="oracle", resource_type="verdict", resource_id=q.id,
              http_status=200,
              request_params={"type": "EXTRACT", "model": model,
                              "prompt_version": PROMPT_VERSION,
                              "source_ref": source_ref})
    return v, False


def to_claims(verdict: "Verdict", *, target_key: str, target_label: str,
              authority: str, programmes: tuple[str, ...], listed_on: str | None,
              raw_ref: str, observed_at: str | None = None) -> list:
    """Verified extractions -> claims, at the tier their evidence supports.

    **These entities are named, not designated**, and the distinction is the
    whole point of the predicate. An official act wrote "Boundless" into an act
    describing how state foundation grants reached a designated person; the
    agency itself carries no listing anywhere. A company contracting with it
    would hit no sanctions screen.

    So the claim is `NAMED_IN_DESIGNATION` at `THIRD_PARTY` tier, carrying
    `propagates=False`. Legal facts propagate along defined rules — the >50%
    test *is* a propagation rule. This is not one of those: an assertion inside
    a designation statement attaches to the party named and travels zero hops.
    A nexus built through one of these edges is context for a human and may
    never colour a finding, whatever its hop count.

    The verbatim quote rides on every claim, because the finding a reader gets
    is the source's own words rather than ours.
    """
    from gleipnir.claims import Claim, EntityRef, EpistemicTier, Predicate

    out = []
    for e in verdict.answer.get("entities", []):
        kind = {"person": "person", "company": "company",
                "organisation": "company"}.get(e["kind"], "other")
        out.append(Claim(
            subject=EntityRef(kind=kind, key=f"name:{e['name'].casefold()}",
                              label=e["name"]),
            predicate=Predicate.NAMED_IN_DESIGNATION,
            object=EntityRef(kind="person", key=target_key, label=target_label),
            source_id="opensanctions",
            epistemic_tier=EpistemicTier.THIRD_PARTY,
            raw_ref=raw_ref,
            valid_from=(date.fromisoformat(listed_on) if listed_on else None),
            observed_at=observed_at,
            confidence=1.0,
            qualifiers={
                "relation": e["relation"],
                "quote": e["quote"],
                "authority": authority,
                "programmes": list(programmes),
                "extracted_by": verdict.model,
                "question_id": verdict.question_id,
                # Allegations propagate zero hops. Enforced at query time in
                # the nexus layer; stated here so it cannot be lost.
                "propagates": False,
            },
        ))
    return out
