"""Source-to-atom alignment, inspired by AlignScore's context/claim interface.

No lexical-similarity fallback: without a semantic backend the atom is unchecked.
Scores express source support, never the probability that a claim is true.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Literal, Protocol, TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from gleipnir.rawstore import RawStore
    from gleipnir.research import ResearchResult

METHOD = 'source-atom-alignment/1'
CONTEXT_MARGIN = 1200
MAX_CONTEXT = 6000


class Model(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)


class AlignmentInput(Model):
    evidence_id: str
    passage_id: str
    source_ref: str = Field(pattern=r'^[0-9a-f]{64}$')
    statement: str = Field(min_length=1, max_length=3000)
    qualification: str = Field(min_length=1, max_length=3000)
    quote: str = Field(min_length=1)
    context: str = Field(min_length=1, max_length=MAX_CONTEXT)
    context_start: int = Field(ge=0)
    context_end: int = Field(gt=0)
    clipped_left: bool
    clipped_right: bool


class Judgment(Model):
    relation: Literal['supports', 'contradicts', 'insufficient']
    support_score: float = Field(ge=0, le=1, allow_inf_nan=False)
    atomic: bool | None
    truth_evaluable: bool | None
    context_sufficient: bool | None
    issues: tuple[Literal['negation', 'uncertainty', 'scope', 'attribution',
                          'time', 'quantity', 'context', 'compound', 'unsupported'], ...]
    rationale: str = Field(min_length=1, max_length=2000)
    supporting_quote: str = Field(max_length=MAX_CONTEXT)


class Backend(Protocol):
    @property
    def identity(self) -> str: ...

    def assess(self, pair: AlignmentInput) -> Judgment: ...


def request_key(pair: AlignmentInput, backend: str) -> str:
    data = {'method': METHOD, 'backend': backend, 'input': pair.model_dump()}
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


class AlignmentRecord(Model):
    key: str
    backend: str
    method: Literal['source-atom-alignment/1'] = METHOD
    input: AlignmentInput
    judgment: Judgment
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class AlignmentReport(Model):
    method: Literal['source-atom-alignment/1'] = METHOD
    backend: str
    context_margin: int = Field(default=CONTEXT_MARGIN, ge=0, le=MAX_CONTEXT)
    # A routing threshold, deliberately not described as calibrated confidence.
    threshold: float = Field(default=0.8, ge=0, le=1, allow_inf_nan=False)
    records: tuple[AlignmentRecord, ...]
    pending: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()


def pairs_for(result: ResearchResult, store: RawStore, *,
              context_margin: int = CONTEXT_MARGIN) -> tuple[AlignmentInput, ...]:
    from gleipnir.research import source_text, validate

    if not 0 <= context_margin <= MAX_CONTEXT:
        raise ValueError('context margin outside supported bounds')
    validate(result, store)
    sources = {s.id: s for s in result.sources}
    passages = {p.id: p for p in result.passages}
    texts = {s.id: source_text(store.get(s.raw_ref), s.media_type) for s in result.sources}
    pairs = []
    for atom in result.evidence:
        for pid in dict.fromkeys(atom.passage_ids):
            passage = passages[pid]
            text = texts[passage.source_id]
            # Refuse oversized quotations, never silently drop their ending.
            if len(passage.quote) > MAX_CONTEXT:
                raise ValueError(f'{pid}: quotation exceeds alignment context limit')
            margin = min(context_margin, (MAX_CONTEXT - len(passage.quote)) // 2)
            start = max(0, passage.start - margin)
            end = min(len(text), passage.end + margin)
            pairs.append(AlignmentInput(
                evidence_id=atom.id, passage_id=pid,
                source_ref=sources[passage.source_id].raw_ref,
                statement=atom.statement, qualification=atom.qualification,
                quote=passage.quote, context=text[start:end],
                context_start=start, context_end=end,
                clipped_left=start > 0, clipped_right=end < len(text)))
    return tuple(pairs)


def check_judgment(pair: AlignmentInput, judgment: Judgment) -> None:
    if judgment.supporting_quote and judgment.supporting_quote not in pair.context:
        raise ValueError('alignment rationale quotes text absent from source context')
    if judgment.relation in ('supports', 'contradicts') and not judgment.supporting_quote:
        raise ValueError('support or contradiction requires an exact source quotation')


def passes(judgment: Judgment, threshold: float) -> bool:
    return (judgment.relation == 'supports' and judgment.support_score >= threshold
            and judgment.atomic is True and judgment.truth_evaluable is True
            and judgment.context_sufficient is True and not judgment.issues)


def assess(result: ResearchResult, store: RawStore, backend: Backend, *,
           max_calls: int = 0, threshold: float = 0.8,
           context_margin: int = CONTEXT_MARGIN) -> AlignmentReport:
    """Bounded, resumable assessment. Save each completed pair immediately."""
    if max_calls < 0:
        raise ValueError('max_calls must be nonnegative')
    # Validate configuration before any model call.
    AlignmentReport(backend=backend.identity, threshold=threshold,
                    context_margin=context_margin, records=())
    records, pending, errors = [], [], []
    calls = 0
    for pair in pairs_for(result, store, context_margin=context_margin):
        key = request_key(pair, backend.identity)
        hit = store.latest('alignment', 'judgment', key)
        try:
            if hit:
                payload = store.get(hit.content_hash)
                if hashlib.sha256(payload).hexdigest() != hit.content_hash:
                    raise ValueError('cached alignment hash mismatch')
                record = AlignmentRecord.model_validate_json(payload)
                if record.key != key or record.backend != backend.identity or record.input != pair:
                    raise ValueError('cached alignment input mismatch')
            elif calls < max_calls:
                calls += 1
                judgment = Judgment.model_validate(backend.assess(pair))
                check_judgment(pair, judgment)
                record = AlignmentRecord(key=key, backend=backend.identity,
                                         input=pair, judgment=judgment)
                store.put(payload=record.model_dump_json().encode(), source='alignment',
                          resource_type='judgment', resource_id=key, http_status=200,
                          request_params={'method': METHOD, 'backend': backend.identity})
            else:
                pending.append(key)
                continue
            check_judgment(pair, record.judgment)
            records.append(record)
        except Exception as exc:
            # Failed calls consume budget; no retry storm or false support.
            # Do not persist provider error text, which may include secrets.
            pending.append(key)
            errors.append(f'{pair.evidence_id}/{pair.passage_id}: {type(exc).__name__}')
    return AlignmentReport(backend=backend.identity, threshold=threshold, context_margin=context_margin,
                           records=tuple(records), pending=tuple(pending), errors=tuple(errors))


def gate(result: ResearchResult, store: RawStore, report: AlignmentReport) -> dict:
    """Recompute input bindings; a changed atom or source invalidates its result.

    Conservative v1: every cited passage must support the whole atom. Evidence
    requiring combination across sources is routed for review, not averaged.
    """
    pairs = pairs_for(result, store, context_margin=report.context_margin)
    expected = {request_key(p, report.backend): p for p in pairs}
    records = {r.key: r for r in report.records}
    if len(records) != len(report.records) or set(records) - set(expected):
        raise ValueError('duplicate or stale alignment records')
    if set(report.pending) - set(expected):
        raise ValueError('stale pending alignment requests')
    by_atom: dict[str, list[bool]] = {a.id: [] for a in result.evidence}
    for key, pair in expected.items():
        record = records.get(key)
        if record is None:
            by_atom[pair.evidence_id].append(False)
            continue
        if record.input != pair or record.backend != report.backend:
            raise ValueError('alignment input or backend mismatch')
        saved = store.latest('alignment', 'judgment', key)
        if saved is None:
            raise ValueError('alignment has no stored assessment record')
        payload = store.get(saved.content_hash)
        if hashlib.sha256(payload).hexdigest() != saved.content_hash:
            raise ValueError('stored alignment hash mismatch')
        if AlignmentRecord.model_validate_json(payload) != record:
            raise ValueError('alignment report differs from stored assessment')
        check_judgment(pair, record.judgment)
        by_atom[pair.evidence_id].append(passes(record.judgment, report.threshold))
    eligible = [aid for aid, outcomes in by_atom.items() if outcomes and all(outcomes)]
    blocked = [aid for aid in by_atom if aid not in eligible]
    return {'eligible_atoms': eligible, 'blocked_atoms': blocked,
            'synthesis_evidence_eligible': bool(result.synthesis.evidence_ids)
            and all(aid in eligible for aid in result.synthesis.evidence_ids)
            and not report.pending and not report.errors,
            'semantic_truth_verified': False}


class AlignScoreBackend:
    """Optional upstream scorer adapter. No heavyweight dependency at import.

    A scalar score cannot certify atomicity/context, or distinguish contradiction
    from missing support. It is retained as a diagnostic; it cannot pass the
    semantic gate alone. Supply a checkpoint/version fingerprint as identity.
    """
    def __init__(self, scorer, *, identity: str):
        self.scorer = scorer
        self.identity = f'alignscore:{identity}'

    def assess(self, pair: AlignmentInput) -> Judgment:
        values = self.scorer.score(contexts=[pair.context], claims=[pair.statement])
        score = float(values[0])
        return Judgment(relation='insufficient', support_score=score,
                        atomic=None, truth_evaluable=None, context_sufficient=None,
                        issues=(), supporting_quote='', rationale=(
                            'Scalar source-support diagnostic only; requires contextual '
                            'and atomicity assessment before admission.'))
