"""What a human read on a source — recorded as claims, not as judgement.

`architecture.md` §11 decided this, and decided it against scraping:

> LinkedIn scraping violates their terms of service. […] The specific commercial
> risk: a due diligence firm caught scraping is a due diligence firm with a
> credibility problem. […] Lower-risk paths to the same signal, roughly in order
> of preference: 1. Client- and counterparty-supplied documents. 2.
> **Analyst-entered observations** — a human looked at a public profile and
> recorded a claim. Manual, defensible, and often sufficient at diligence
> volumes. […] The architecture should make paths 1–2 excellent and treat
> [scraping] as a decision deferred indefinitely.

This module is paths 1 and 2. It is the intake for a profile, a CV, a pitch deck
or an RFP response that a person read with their own eyes, and it exists because
the richest contradiction surface in the whole design — §3's *LinkedIn says Lars
Nielsen is CFO of Acme Holding, the register does not* — was unreachable without
it.

**An attestation is not an analyst verdict.** `analyst.py` records a judgement
about an entity and may suppress a predicate within a stated scope. This records
*what a document says*, suppresses nothing, and concludes nothing. Two different
human acts, deliberately two different records.

**The tier is the document's, not the reader's.** A LinkedIn profile is
`SELF_DECLARED` whether a scraper or a person read it — the subject wrote it and
bears no consequence for it being wrong. Filing these at `ANALYST` tier would
launder a self-description into a checked fact and destroy the very comparison
they exist to feed. The analyst is the *recorder*, named in the qualifiers.

**Every statement carries a verbatim quote, and the quote is checked.** The same
rule `oracle.py` applies to a model applies to a person: a recorded statement
whose quote does not appear in the excerpt the analyst pasted is dropped. It
turns "I remember the profile saying he was CFO" into something a reader can
audit.

**We record the statements, not the profile.** §11's minimisation argument is
only available if it is true: an attestation holds the handful of statements
relevant to a screen plus their quotes, and never a copy of a person's page.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from enum import StrEnum
from typing import Any

from gleipnir.claims import Claim, EntityRef, EpistemicTier, Predicate
from gleipnir.extract.website import CHECKS_AGAINST

SOURCE_ID = "attestation"


class SourceKind(StrEnum):
    """What was read. Ordered by §11's preference, best first."""

    #: Path 1. The subject or counterparty handed it over.
    SUPPLIED_DOCUMENT = "supplied_document"
    SUPPLIED_CV = "supplied_cv"
    RFP_RESPONSE = "rfp_response"
    #: Path 2. A person looked at something public.
    PUBLIC_PROFILE = "public_profile"
    COMPANY_WEBSITE = "company_website"
    PRESS = "press"
    OTHER = "other"


class AttestationError(ValueError):
    pass


#: Only predicates the contradiction layer can actually pair. A statement with
#: no registry counterpart is an opinion about a person we have no way to check,
#: and §11 is the reason not to collect it.
PAIRABLE: frozenset[Predicate] = frozenset(CHECKS_AGAINST)


def _normalise(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip().casefold()


@dataclass(frozen=True)
class Statement:
    """One thing the document says, with the words it says it in."""

    predicate: Predicate
    #: What the document asserts — a company name, a title, a headcount.
    value: str
    quote: str
    valid_from: date | None = None
    valid_to: date | None = None
    #: Free qualifiers the predicate needs: `role` for HAS_ROLE, and so on.
    detail: dict[str, Any] = field(default_factory=dict)

    def validate(self, excerpt: str) -> None:
        if self.predicate not in PAIRABLE:
            raise AttestationError(
                f"{self.predicate} has no registry counterpart to pair against; "
                f"pairable predicates are {sorted(p.value for p in PAIRABLE)}")
        if not self.value:
            raise AttestationError("a statement must assert something")
        if not self.quote:
            raise AttestationError(
                "a statement needs the document's own words — an unquoted "
                "reading is not auditable")
        if _normalise(self.quote) not in _normalise(excerpt):
            raise AttestationError(
                "the quote does not appear in the excerpt it was taken from")


@dataclass(frozen=True)
class Attestation:
    """One human reading of one document, at one moment."""

    subject: str                 # CVR number or person key the reading is about
    source_kind: SourceKind
    #: Where it was read. A URL for a public profile; a document reference for
    #: something supplied. One of the two is required.
    location: str
    retrieved_on: date
    analyst: str
    #: The passage the statements were taken from. Bounded on purpose: enough to
    #: verify every quote, and no more.
    excerpt: str
    statements: tuple[Statement, ...]
    recorded_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat())
    #: Content hash, where the document itself was lawfully stored (path 1).
    document_ref: str = ""

    def __post_init__(self) -> None:
        if not (self.analyst and self.analyst.strip()):
            raise AttestationError("an attestation must name who made it")
        if not self.location.strip():
            raise AttestationError(
                "an attestation must say what was read and where")
        if not self.statements:
            raise AttestationError("an attestation with no statement records nothing")
        if not self.excerpt.strip():
            raise AttestationError(
                "an attestation needs the excerpt its quotes came from")
        for statement in self.statements:
            statement.validate(self.excerpt)

    @property
    def tier(self) -> EpistemicTier:
        """The document's tier, never the reader's.

        A supplied document and a public profile are both the subject's own
        account of themselves. Nobody bears legal consequence for either being
        wrong, which is exactly what `SELF_DECLARED` means.
        """
        return EpistemicTier.SELF_DECLARED

    def to_claims(self, *, raw_ref: str, subject_ref: EntityRef | None = None,
                  observed_at: str | None = None) -> list[Claim]:
        """The statements, as claims that `contradict.pair_claims` can pair."""
        subject = subject_ref or EntityRef(kind="company", key=self.subject)
        return [
            Claim(
                subject=subject,
                predicate=s.predicate,
                object=EntityRef(kind="other", key=f"value:{s.value.casefold()}",
                                 label=s.value),
                source_id=SOURCE_ID,
                epistemic_tier=self.tier,
                raw_ref=raw_ref,
                valid_from=s.valid_from,
                valid_to=s.valid_to,
                observed_at=observed_at or self.recorded_at,
                qualifiers={
                    "quote": s.quote,
                    "read_by": self.analyst,
                    "source_kind": str(self.source_kind),
                    "location": self.location,
                    "retrieved_on": str(self.retrieved_on),
                    "checks_against": str(CHECKS_AGAINST.get(s.predicate, "")),
                    # The reader is not the asserter. Stated on every claim so
                    # a report can never present a profile as a checked fact.
                    "asserted_by": "the document",
                    **s.detail,
                },
            )
            for s in self.statements
        ]


def record(store, attestation: Attestation) -> str:
    """Append one attestation to the raw store. Returns its content hash."""
    payload = json.dumps(asdict(attestation), ensure_ascii=False, sort_keys=True,
                         default=str).encode()
    rec = store.put(payload=payload, source=SOURCE_ID,
                    resource_type="attestation", resource_id=attestation.subject,
                    http_status=200,
                    request_params={"analyst": attestation.analyst,
                                    "source_kind": str(attestation.source_kind),
                                    "location": attestation.location})
    return rec.content_hash


def attestations(store, subject: str) -> list[tuple[str, Attestation]]:
    """Every attestation about one subject, oldest first, with its hash."""
    out: list[tuple[str, Attestation]] = []
    for f in store.fetches():
        if f.source != SOURCE_ID or f.resource_id != subject:
            continue
        try:
            out.append((f.content_hash, _from_json(store.get_json(f.content_hash))))
        except (AttestationError, TypeError, ValueError, KeyError):
            continue
    return sorted(out, key=lambda p: p[1].recorded_at)


def claims_for(store, subject: str, *,
               subject_ref: EntityRef | None = None) -> list[Claim]:
    """Every attested claim about one subject, ready to pair against the registry."""
    return [c for h, a in attestations(store, subject)
            for c in a.to_claims(raw_ref=h, subject_ref=subject_ref)]


def _from_json(payload: dict[str, Any]) -> Attestation:
    data = dict(payload)
    data["source_kind"] = SourceKind(data["source_kind"])
    data["retrieved_on"] = date.fromisoformat(str(data["retrieved_on"])[:10])
    data["statements"] = tuple(
        Statement(predicate=Predicate(s["predicate"]), value=s["value"],
                  quote=s["quote"],
                  valid_from=date.fromisoformat(s["valid_from"][:10])
                  if s.get("valid_from") else None,
                  valid_to=date.fromisoformat(s["valid_to"][:10])
                  if s.get("valid_to") else None,
                  detail=s.get("detail") or {})
        for s in data.get("statements") or [])
    return Attestation(**data)
