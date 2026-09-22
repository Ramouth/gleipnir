"""Domain-independent research artifacts, separate from corporate screening.

Code verifies provenance and passage locations. Interpretations remain attributed
proposals: successful validation never means a scientific claim is true.
"""
from __future__ import annotations

import hashlib
from datetime import date
from typing import Literal

from lxml import html
from pydantic import BaseModel, ConfigDict, Field, field_validator

from gleipnir.rawstore import RawStore


class Record(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)


class Source(Record):
    id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    url: str = Field(min_length=1)
    raw_ref: str = Field(pattern=r'^[0-9a-f]{64}$')
    media_type: Literal['text/html', 'text/plain']
    origin_group: str = Field(min_length=1)
    published_on: date | None = None
    scope: str = Field(min_length=1)


class Question(Record):
    id: str = Field(min_length=1)
    text: str = Field(min_length=1)


class Passage(Record):
    id: str = Field(min_length=1)
    source_id: str
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    quote: str = Field(min_length=1)


class Period(Record):
    """World time: when the statement holds. An open end means "not stated"."""
    start: date | None = None
    end: date | None = None

    @field_validator('end')
    @classmethod
    def ordered(cls, end, info):
        start = info.data.get('start')
        if start and end and end < start:
            raise ValueError('period ends before it starts')
        return end


class Evidence(Record):
    id: str = Field(min_length=1)
    question_id: str
    passage_ids: tuple[str, ...] = Field(min_length=1)
    statement: str = Field(min_length=1)
    qualification: str = Field(min_length=1)
    proposed_by: str = Field(min_length=1)
    #: Atoms are eternal sentences: the time goes into the atom, never an
    #: implicit "now". None means the proposer did not bind it.
    holds: Period | None = None


class Assessment(Record):
    id: str = Field(min_length=1)
    evidence_id: str
    relation: Literal['supports', 'contradicts', 'insufficient']
    rationale: str = Field(min_length=1)
    author: str = Field(min_length=1)
    author_kind: Literal['model', 'human']
    # All entries are proposals in v1; no caller can mark one "verified truth".
    status: Literal['proposed'] = 'proposed'


class Synthesis(Record):
    text: str = Field(min_length=1)
    evidence_ids: tuple[str, ...] = ()
    author: str = Field(min_length=1)
    status: Literal['proposed'] = 'proposed'


class ResearchResult(Record):
    schema_version: Literal['gleipnir.research/1'] = 'gleipnir.research/1'
    id: str = Field(min_length=1)
    question: str = Field(min_length=1)
    as_of: date
    method: str = Field(min_length=1)
    text_method: Literal['utf8-html-text-v1'] = 'utf8-html-text-v1'
    sources: tuple[Source, ...]
    questions: tuple[Question, ...]
    passages: tuple[Passage, ...]
    evidence: tuple[Evidence, ...]
    assessments: tuple[Assessment, ...]
    synthesis: Synthesis
    unresolved: tuple[str, ...]
    limitations: tuple[str, ...]

    @field_validator('sources', 'questions', 'passages', 'evidence', 'assessments')
    @classmethod
    def unique_ids(cls, entries):
        ids = [entry.id for entry in entries]
        if len(ids) != len(set(ids)):
            raise ValueError('duplicate IDs')
        return entries


def world_time(atom: Evidence, sources: dict[str, Source],
               passages: dict[str, Passage]) -> tuple[str, date | None, date | None]:
    """How the atom's time is bound: 'stated', 'asserted' or 'open'.

    'asserted' falls back to the latest source date: the source said it held no
    later than that, which bounds it without claiming it still holds. 'open'
    means UNKNOWN at every date, never TRUE at every date.
    """
    if atom.holds and (atom.holds.start or atom.holds.end):
        return 'stated', atom.holds.start, atom.holds.end
    dates = [sources[passages[p].source_id].published_on for p in atom.passage_ids]
    dates = [d for d in dates if d]
    if dates:
        return 'asserted', None, max(dates)
    return 'open', None, None


def source_text(payload: bytes, media_type: str) -> str:
    """Versioned representation for offsets: UTF-8, DOM text, collapsed space.

    UTF-8 decoding is strict. Other encodings require a future explicit method.
    """
    text = payload.decode('utf-8')
    if media_type == 'text/html':
        document = html.fromstring(text)
        for element in document.xpath('//script|//style'):
            element.drop_tree()
        text = document.text_content()
    elif media_type != 'text/plain':
        raise ValueError(f'unsupported media type: {media_type}')
    return ' '.join(text.split())


def validate(result: ResearchResult, store: RawStore) -> dict:
    """Verify every edge and source version offline; do not infer entailment."""
    texts = {}
    fetches = store.fetches()
    for source in result.sources:
        payload = store.get(source.raw_ref)
        if hashlib.sha256(payload).hexdigest() != source.raw_ref:
            raise ValueError(f'{source.id}: source hash mismatch')
        if not any(f.content_hash == source.raw_ref and f.resource_id == source.url
                   and f.http_status == 200 and f.byte_len == len(payload) for f in fetches):
            raise ValueError(f'{source.id}: no matching successful source fetch')
        texts[source.id] = source_text(payload, source.media_type)
    questions = {q.id for q in result.questions}
    passages = {p.id: p for p in result.passages}
    evidence = {e.id: e for e in result.evidence}
    for passage in result.passages:
        if passage.source_id not in texts:
            raise ValueError(f'{passage.id}: missing source')
        if not (passage.start < passage.end <= len(texts[passage.source_id])):
            raise ValueError(f'{passage.id}: invalid passage bounds')
        if texts[passage.source_id][passage.start:passage.end] != passage.quote:
            raise ValueError(f'{passage.id}: passage does not match stored source')
    for atom in result.evidence:
        if atom.question_id not in questions:
            raise ValueError(f'{atom.id}: missing question')
        if any(pid not in passages for pid in atom.passage_ids):
            raise ValueError(f'{atom.id}: missing passage')
    for assessment in result.assessments:
        if assessment.evidence_id not in evidence:
            raise ValueError(f'{assessment.id}: missing evidence')
    if any(eid not in evidence for eid in result.synthesis.evidence_ids):
        raise ValueError('synthesis: missing evidence')
    sources = {s.id: s for s in result.sources}
    binding = {a.id: world_time(a, sources, passages)[0] for a in result.evidence}
    return {'sources_verified': len(texts), 'passages_verified': len(passages),
            'evidence_proposals': len(evidence), 'semantic_truth_verified': False,
            'time_stated': sum(b == 'stated' for b in binding.values()),
            'time_asserted': sum(b == 'asserted' for b in binding.values()),
            'time_open': sorted(k for k, b in binding.items() if b == 'open')}


def save(result: ResearchResult, store: RawStore) -> str:
    """Append a validated snapshot; prior versions remain content-addressed."""
    validate(result, store)
    payload = result.model_dump_json(indent=2).encode('utf-8')
    return store.put(payload=payload, source='research', resource_type='research_result',
                     resource_id=result.id, http_status=200,
                     request_params={'schema_version': result.schema_version}).content_hash


def render(result: ResearchResult) -> str:
    """Reviewable export of attributed proposals, not a scientific validator."""
    sources = {s.id: s for s in result.sources}
    passages = {p.id: p for p in result.passages}
    lines = [f'# {result.question}', '', f'Research date: {result.as_of}', '',
             'DRAFT — source fidelity has not been certified by this renderer.', '',
             result.synthesis.text, '',
             f'Synthesis: proposed by {result.synthesis.author}; evidence: '
             + ', '.join(result.synthesis.evidence_ids), '', '## Evidence', '']
    for atom in result.evidence:
        refs = list(dict.fromkeys(passages[p].source_id for p in atom.passage_ids))
        links = ', '.join(f'[{sid}: {sources[sid].title}]({sources[sid].url})' for sid in refs)
        kind, start, end = world_time(atom, sources, passages)
        when = {'stated': f'{start or "…"} → {end or "…"}',
                'asserted': f'as asserted by {end}; not known to hold after',
                'open': 'OPEN — no world time; unknown at every date'}[kind]
        lines.extend([f'- **{atom.id}:** {atom.statement} {links}',
                      f'  Qualification: {atom.qualification}', f'  Holds: {when}'])
    lines.extend(['', '## Proposed evidence assessments', ''])
    for assessment in result.assessments:
        lines.append(f'- **{assessment.evidence_id}: {assessment.relation}** — '
                     f'{assessment.rationale} ({assessment.author_kind}: {assessment.author}; '
                     f'{assessment.status})')
    lines.extend(['', '## Unresolved', ''] + [f'- {x}' for x in result.unresolved])
    lines.extend(['', '## Method and limitations', '', result.method, ''])
    lines.extend(f'- {x}' for x in result.limitations)
    lines.extend(['', 'Passage and hash verification checks provenance only. '
                  'Evidence interpretations and synthesis remain attributed proposals.', ''])
    return '\n'.join(lines)
