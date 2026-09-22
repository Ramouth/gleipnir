"""Source track records: reports read backward.

A report says something about its content and, once that content is resolved,
something about its speaker. The rules are the roadmap's source-history rules:

- **Only independent resolution counts.** A claim confirmed only by the source
  itself, or by a copy of it (same origin group), stays unresolved for that source.
- **Repetition earns nothing.** One source reporting one claim ten times counts once.
- **Extraction errors are not the source's.** Only reports whose report level
  closed (the quote verifies that the source said it) enter the record.
- **A source is judged from its own position.** Claims are eternal sentences
  with their time inside, so "X was CEO in 2024" is not refuted by a new CEO in
  2026. A resolution only applies to the dated claim that was reported.
- **A source answers for its own layer.** A gazette reporting that a CEO denied
  p committed to "the CEO denied p", not to p. Its claim_key is the inner report.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Iterable, Literal

Value = Literal['TRUE', 'FALSE', 'UNKNOWN', 'UNKNOWABLE']


@dataclass(frozen=True)
class Resolution:
    value: Value
    #: Origin groups of the sources the resolution rests on.
    by_groups: frozenset[str]
    as_of: str


@dataclass(frozen=True)
class Entry:
    source_id: str
    origin_group: str
    claim_key: str            # identity of the eternal claim that was reported
    report_closed: bool       # the quote verified that the source said it
    verb: str                 # 'denies' inverts what the source committed to
    resolution: Resolution | None


def outcome(entry: Entry) -> str:
    """confirmed, refuted or unresolved, for this source, from its own report."""
    r = entry.resolution
    if r is None or r.value in ('UNKNOWN', 'UNKNOWABLE') or not (r.by_groups - {entry.origin_group}):
        return 'unresolved'
    right = (r.value == 'TRUE') != (entry.verb == 'denies')
    return 'confirmed' if right else 'refuted'


def track_record(entries: Iterable[Entry]) -> dict[str, Counter]:
    seen, record = set(), {}
    for e in entries:
        if not e.report_closed or (e.source_id, e.claim_key) in seen:
            continue
        seen.add((e.source_id, e.claim_key))
        record.setdefault(e.source_id, Counter())[outcome(e)] += 1
    return record
