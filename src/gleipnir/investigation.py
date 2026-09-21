"""Bounded investigation infrastructure.

This module is the boundary between an investigation *idea* and knowledge in
the claim graph.  A language model may propose an evidence-backed answer to an
approved question; it can neither alter a claim nor create a finding.  Only a
source-specific extractor may turn a verified proposal into a :class:`Claim`.

Potentially sensitive passages use a separate, human-only review packet.  The
packet deliberately records no assessment of a person's religion, politics,
beliefs, or intent.  It is a pointer to an attributable passage that a trained
reviewer may classify as in or out of scope.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from enum import StrEnum
from typing import Any

from gleipnir.predicates.core import V


class AnswerKind(StrEnum):
    BOOLEAN = "boolean"
    DATE = "date"
    COUNT = "count"
    ENTITY_MATCH = "entity_match"


class ReviewOutcome(StrEnum):
    """Human outcomes.  None is an assertion about a person's beliefs."""

    PENDING = "pending"
    IRRELEVANT = "irrelevant"
    IDENTITY_UNRESOLVED = "identity_unresolved"
    OFFICIAL_SECURITY_FACT = "official_security_fact"
    ESCALATE_LEGAL = "escalate_legal"


class InvestigationError(ValueError):
    pass


def _normalise(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().casefold()


@dataclass(frozen=True)
class ScreenMandate:
    """The bounded authority under which a screen may run.

    ``permitted_sources`` and ``permitted_subject_kinds`` are deny-by-default.
    A consent record is intentionally an opaque hash/reference: the raw consent
    artefact belongs in the immutable raw store, not in every question run.
    """

    id: str
    subject_key: str
    purpose: str
    consent_ref: str
    granted_on: date
    expires_on: date
    permitted_sources: tuple[str, ...]
    permitted_subject_kinds: tuple[str, ...]
    withdrawn_on: date | None = None

    def active_on(self, on: date) -> bool:
        return (self.withdrawn_on is None and self.granted_on <= on <= self.expires_on)

    def permits(self, *, source: str, subject_kind: str, on: date) -> bool:
        return (self.active_on(on) and source in self.permitted_sources
                and subject_kind in self.permitted_subject_kinds)


@dataclass(frozen=True)
class QuestionContract:
    """A versioned, data-answerable question approved by policy.

    The contract must name both the source and fields which can answer it.
    This prevents a model from turning a plausible narrative question into an
    unbounded web search.
    """

    id: str
    version: str
    question: str
    trigger_predicates: tuple[str, ...]
    source: str
    subject_kind: str
    required_fields: tuple[str, ...]
    answer_kind: AnswerKind
    decision_effect: str
    coverage_rule: str
    requires_human_review: bool = False

    def validate(self) -> None:
        if not all((self.id, self.version, self.question, self.source,
                    self.subject_kind, self.decision_effect, self.coverage_rule)):
            raise InvestigationError("question contract has a required blank field")
        if not self.trigger_predicates:
            raise InvestigationError("question contract must name a trigger predicate")
        if not self.required_fields:
            raise InvestigationError("question contract must name answer fields")


@dataclass(frozen=True)
class EvidenceProposal:
    """An additive LLM/extractor proposal, not an admitted graph claim."""

    question_id: str
    question_version: str
    raw_ref: str
    source: str
    quote: str
    proposed_fields: dict[str, Any]
    model: str | None = None
    prompt_version: str | None = None

    def verify_against(self, source_text: str) -> None:
        if not self.quote or _normalise(self.quote) not in _normalise(source_text):
            raise InvestigationError("proposal quote is not present in its source")


@dataclass(frozen=True)
class QuestionRun:
    """One immutable attempt to answer a contract for one subject."""

    mandate_id: str
    contract_id: str
    contract_version: str
    subject_key: str
    subject_kind: str
    as_of: date
    trigger_claim_refs: tuple[str, ...]
    answer: V
    evidence_refs: tuple[str, ...] = ()
    proposals: tuple[EvidenceProposal, ...] = ()
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    @classmethod
    def create(cls, *, mandate: ScreenMandate, contract: QuestionContract,
               subject_key: str, as_of: date, trigger_claim_refs: tuple[str, ...],
               answer: V, evidence_refs: tuple[str, ...] = (),
               proposals: tuple[EvidenceProposal, ...] = ()) -> "QuestionRun":
        contract.validate()
        if not mandate.permits(source=contract.source, subject_kind=contract.subject_kind,
                               on=as_of):
            raise InvestigationError("mandate does not permit this question contract")
        if not trigger_claim_refs:
            raise InvestigationError("question run requires the claims that triggered it")
        if answer in (V.TRUE, V.FALSE) and not (evidence_refs or proposals):
            raise InvestigationError("a resolved answer requires evidence")
        for proposal in proposals:
            if (proposal.question_id, proposal.question_version, proposal.source) != (
                    contract.id, contract.version, contract.source):
                raise InvestigationError("proposal is not for this approved question")
        return cls(mandate_id=mandate.id, contract_id=contract.id,
                   contract_version=contract.version, subject_key=subject_key,
                   subject_kind=contract.subject_kind, as_of=as_of,
                   trigger_claim_refs=trigger_claim_refs, answer=answer,
                   evidence_refs=evidence_refs, proposals=proposals)


@dataclass(frozen=True)
class ReviewPacket:
    """Restricted pointer to a passage requiring human interpretation.

    No field stores a political or religious label, a sentiment score, or a
    model conclusion.  ``outcome`` can be set only by ``review`` below.
    """

    mandate_id: str
    contract_id: str
    contract_version: str
    subject_key: str
    raw_ref: str
    source: str
    quote: str
    context: str
    reason: str
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    outcome: ReviewOutcome = ReviewOutcome.PENDING
    reviewer: str | None = None
    reviewed_at: str | None = None
    rationale: str | None = None

    @classmethod
    def create(cls, *, mandate: ScreenMandate, contract: QuestionContract,
               subject_key: str, as_of: date, raw_ref: str, quote: str,
               context: str, reason: str) -> "ReviewPacket":
        contract.validate()
        if not contract.requires_human_review:
            raise InvestigationError("only a human-review contract can create a review packet")
        if not mandate.permits(source=contract.source, subject_kind=contract.subject_kind,
                               on=as_of):
            raise InvestigationError("mandate does not permit this review packet")
        if not quote or _normalise(quote) not in _normalise(context):
            raise InvestigationError("review quote is not present in bounded context")
        if not reason:
            raise InvestigationError("review packet requires a retrieval reason")
        return cls(mandate_id=mandate.id, contract_id=contract.id,
                   contract_version=contract.version, subject_key=subject_key,
                   raw_ref=raw_ref, source=contract.source, quote=quote,
                   context=context, reason=reason)

    def review(self, *, outcome: ReviewOutcome, reviewer: str,
               rationale: str) -> "ReviewPacket":
        if self.outcome is not ReviewOutcome.PENDING:
            raise InvestigationError("review packet is immutable once reviewed")
        if outcome is ReviewOutcome.PENDING or not reviewer or not rationale:
            raise InvestigationError("a completed review needs outcome, reviewer, and rationale")
        return ReviewPacket(**{**asdict(self), "outcome": outcome,
                               "reviewer": reviewer,
                               "reviewed_at": datetime.now(timezone.utc).isoformat(),
                               "rationale": rationale})


def store_review_packet(store, packet: ReviewPacket) -> str:
    """Append a review packet; never overwrite the pending packet or review."""
    payload = json.dumps(asdict(packet), ensure_ascii=False, sort_keys=True,
                         default=str).encode()
    rec = store.put(payload=payload, source="review", resource_type="review_packet",
                    resource_id=packet.subject_key, http_status=200,
                    request_params={"mandate_id": packet.mandate_id,
                                    "contract_id": packet.contract_id,
                                    "contract_version": packet.contract_version,
                                    "outcome": packet.outcome})
    return rec.content_hash
