"""Analyst observations — the only path by which human judgement enters the graph.

`architecture.md` §11 lists analyst-entered observations as path 2 of 5 for
self-declared sources: "a human looked at a public profile and recorded a claim.
Manual, defensible, and often sufficient at diligence volumes."

The XR-Turbo case argues it is not a fallback but load-bearing. The evidence that
settled that screen — *the officers have engineering credentials consistent with
the business* — came from a human looking at public profiles. No automated source
in the stack can produce it: OpenAlex returns a Norwegian mathematics-education
researcher for a common Danish name, industrial engineers do not publish, and patent
inventor search cannot disambiguate a common name. It is also *positive*
evidence, and every automated predicate can only ever reach "nothing fired".

**A green flag must never silently hide anything.** Three rules, all enforced
here:

1. **Attributable and dated.** Who judged, when, on what basis, from which URLs.
2. **Reversible.** Append-only, like every other claim; a withdrawal is a new
   record, not a deletion.
3. **Pinned to a data state, and stale when that state moves.** The judgement was
   made against a specific set of facts. If ownership, control, or officers
   change afterwards, the flag no longer covers the entity — it covers the
   entity as it was. This is what the bitemporal store is for, and it is the
   difference between a suppression and a blind spot.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from typing import Any

from gleipnir.claims import Claim, EntityRef, EpistemicTier, Predicate

SOURCE_ID = "analyst"

#: Records that are judgements rather than evidence. A flag pins itself to the
#: documents it was made against, and these are not among them.
JUDGEMENT_SOURCES = frozenset({SOURCE_ID, "review"})


class StaleFlag(RuntimeError):
    """The pinned facts moved after the judgement was recorded."""


@dataclass(frozen=True)
class Observation:
    """One human judgement about one entity."""

    subject: str                  # CVR number
    verdict: str                  # 'green' | 'amber' | 'withdraw'
    basis: str                    # what was observed, in the analyst's words
    analyst: str                  # who
    observed_at: str              # ISO timestamp, UTC
    as_of: str                    # the data state the judgement was made against
    sources: tuple[str, ...] = ()
    #: Content hashes of the raw documents the judgement was made against. A
    #: change to any of them makes the flag stale.
    pinned: tuple[str, ...] = ()
    #: Predicates this judgement is allowed to suppress. A green flag on the
    #: officers does NOT suppress a future sanctions hit, and scoping it here is
    #: what stops one honest judgement becoming a general blindfold.
    suppresses: tuple[str, ...] = ()
    note: dict[str, Any] = field(default_factory=dict)

    def to_claim(self) -> Claim:
        return Claim(
            subject=EntityRef(kind="company", key=self.subject),
            predicate=Predicate.ANALYST_VERDICT,
            object=self.verdict,
            source_id=SOURCE_ID,
            epistemic_tier=EpistemicTier.ANALYST,
            raw_ref=";".join(self.pinned),
            valid_from=date.fromisoformat(self.as_of),
            observed_at=self.observed_at,
            qualifiers={"basis": self.basis, "analyst": self.analyst,
                        "sources": list(self.sources),
                        "suppresses": list(self.suppresses), **self.note},
        )


def record(store, obs: Observation) -> str:
    """Append an observation to the raw store. Returns its content hash."""
    rec = store.put(
        payload=json.dumps(asdict(obs), ensure_ascii=False, sort_keys=True).encode(),
        source=SOURCE_ID, resource_type="observation", resource_id=obs.subject,
        http_status=200, request_params={"verdict": obs.verdict,
                                         "analyst": obs.analyst},
    )
    return rec.content_hash


def observations(store, subject: str) -> list[Observation]:
    """Every observation about one entity, oldest first. Append-only, so a
    withdrawal appears after the judgement it withdraws."""
    out = []
    for f in store.fetches():
        if f.source == SOURCE_ID and f.resource_id == subject:
            out.append(Observation(**store.get_json(f.content_hash)))
    return sorted(out, key=lambda o: o.observed_at)


def current_hashes(store, subject: str) -> set[str]:
    """The documents currently in force about one entity — latest per resource.

    **Not every hash ever fetched.** That was what all three call sites computed,
    and because the raw store is append-only an old blob's hash never leaves
    that set: `moved` was always empty, no flag could ever go stale, and the
    mechanism this module describes as "the difference between a suppression and
    a blind spot" was inert. A judgement pinned to a document that has since
    been superseded has to stop covering the entity, which means the comparison
    set is the *latest* payload per resource and nothing else.

    Keyed on (source, resource_type) so a refetch of the company document
    supersedes the previous company document, and a newly-observed DNS record
    does not silently supersede it.

    Judgements are excluded. An analyst observation and a review packet are
    filed against the same `resource_id` as the evidence, but they are what the
    flag *is*, not what it was made against — including them would have a
    judgement pin itself, and would make recording a second observation move the
    state the first one was pinned to.
    """
    latest: dict[tuple[str, str], tuple[str, str]] = {}
    for f in store.fetches():
        if f.resource_id != subject or f.http_status != 200:
            continue
        if f.source in JUDGEMENT_SOURCES:
            continue
        key = (f.source, f.resource_type)
        prev = latest.get(key)
        if prev is None or f.fetched_at > prev[0]:
            latest[key] = (f.fetched_at, f.content_hash)
    return {h for _, h in latest.values()}


def active_flag(store, subject: str, current_hashes: set[str]) -> tuple[Observation | None, str]:
    """The flag in force, and why it is or is not being honoured.

    Returns (observation, status) where status is one of `active`, `stale`,
    `withdrawn`, `none`. A stale flag is reported, never applied — the analyst
    judged a set of facts, and those facts have since changed.
    """
    obs = observations(store, subject)
    if not obs:
        return None, "none"
    latest = obs[-1]
    if latest.verdict == "withdraw":
        return latest, "withdrawn"
    moved = [h for h in latest.pinned if h not in current_hashes]
    if moved:
        return latest, "stale"
    return latest, "active"


def apply_flag(results, flag: Observation | None, status: str):
    """Drop suppressed predicates from a result set, visibly.

    Never applied when the flag is stale or withdrawn, and never beyond the
    predicates the analyst explicitly scoped.
    """
    if flag is None or status != "active":
        return results, []
    scoped = set(flag.suppresses)
    kept = [r for r in results if r.predicate not in scoped]
    dropped = [r for r in results if r.predicate in scoped]
    return kept, dropped
