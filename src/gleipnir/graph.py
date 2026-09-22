"""The graph layer: atoms projected into nodes and edges, in SQLite.

Natural language reaches the graph only through the atomiser contract, so every
edge has an atom, a verified quote and a report chain behind it. Nothing here
reads text.

- **Nodes:** sources, entities (global or local ids), reports and claims.
- **Edges:** report -by-> speaker, report -content-> report|claim,
  claim -subject-> entity, claim -object-> entity, and a traversal edge
  subject -rel:<predicate>-> object carrying the claim id.
- **Aliases:** proposals that a local id (rigid inside one source) denotes the
  same thing as a global id. They are stored with their basis and only used when
  that basis is accepted, so every merge is visible and reversible.

Same claim, two sources: claims are grouped at query time by their resolved
key (subject, predicate, object or value, stated time). Polarity is not in the
key, so a group holding both polarities is a contradiction. A group reported by
two independent sources is the unit the ledger resolves.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections import defaultdict
from typing import Iterable

from gleipnir.atomiser import LOCAL, Atom, Claim, Passage, Report

SCHEMA = """
CREATE TABLE IF NOT EXISTS nodes (id TEXT PRIMARY KEY, kind TEXT NOT NULL, label TEXT,
                                  scope TEXT, props TEXT);
CREATE TABLE IF NOT EXISTS edges (src TEXT NOT NULL, dst TEXT NOT NULL, kind TEXT NOT NULL,
                                  atom TEXT NOT NULL, props TEXT);
CREATE TABLE IF NOT EXISTS aliases (local_id TEXT NOT NULL, global_id TEXT NOT NULL,
                                    basis TEXT NOT NULL, evidence TEXT,
                                    PRIMARY KEY (local_id, global_id));
CREATE INDEX IF NOT EXISTS edges_src ON edges(src);
CREATE INDEX IF NOT EXISTS edges_dst ON edges(dst);
"""


def slug(label: str) -> str:
    return re.sub(r'[^a-z0-9]+', '-', label.casefold()).strip('-')[:60] or 'unnamed'


def _hash(*parts) -> str:
    return hashlib.sha256(json.dumps(parts, sort_keys=True).encode()).hexdigest()[:16]


class Graph:
    def __init__(self, path: str = ':memory:'):
        self.db = sqlite3.connect(path)
        self.db.executescript(SCHEMA)

    # ── writing ──────────────────────────────────────────────────────────────
    def _node(self, id_: str, kind: str, label: str | None, scope: str, props=None):
        self.db.execute('INSERT OR IGNORE INTO nodes VALUES (?,?,?,?,?)',
                        (id_, kind, label, scope, json.dumps(props) if props else None))

    def _edge(self, src: str, dst: str, kind: str, atom: str, props=None):
        self.db.execute('INSERT INTO edges VALUES (?,?,?,?,?)',
                        (src, dst, kind, atom, json.dumps(props) if props else None))

    def _entity(self, ident: str | None, label: str, source_id: str) -> str:
        """The node for a subject, object or speaker. Unidentified speakers and
        subjects get a local id from their label, scoped to this source."""
        node = ident or f'local:{source_id}#{slug(label)}'
        self._node(node, 'entity', label, 'local' if LOCAL.match(node) or not ident else 'global')
        return node

    def add(self, atom: Atom, passage: Passage, verdict: dict, uid: str) -> bool:
        """Project one checked atom. Atoms whose report level failed are left
        out: the atomiser misread the source, so the source said nothing here."""
        if verdict['report']['defects']:
            return False
        self._node(passage.source_id, 'source', passage.source_id, 'global',
                   {'date': passage.source_date})
        node: Report | Claim = atom.report
        parent, depth = None, 0
        while isinstance(node, Report):
            rid = f'report:{uid}:{depth}'
            self._node(rid, 'report', node.verb, 'global', {'quote': atom.quote})
            speaker = self._entity(node.speaker.id, node.speaker.label, passage.source_id)
            self._edge(rid, speaker, 'by', uid)
            if parent:
                self._edge(parent, rid, 'content', uid)
            parent, node, depth = rid, node.content, depth + 1
        claim = node
        cid = f'claim:{uid}'
        self._node(cid, 'claim', claim.statement, verdict['claim'].get('scope', 'global'), {
            'predicate': claim.predicate, 'value': claim.value, 'polarity': claim.polarity,
            'hedge': claim.hedge, 'modality': claim.modality, 'holds': claim.holds.model_dump(),
            'closed': verdict['claim']['closed'], 'open': verdict['claim']['open'],
            'source': passage.source_id})
        self._edge(parent, cid, 'content', uid)
        subject = self._entity(claim.subject.id, claim.subject.label, passage.source_id)
        self._edge(cid, subject, 'subject', uid)
        if claim.object is not None:
            obj = self._entity(claim.object.id, claim.object.label, passage.source_id)
            self._edge(cid, obj, 'object', uid)
            if claim.predicate:
                self._edge(subject, obj, f'rel:{claim.predicate}', uid, {'claim': cid})
        return True

    def propose_alias(self, local_id: str, global_id: str, basis: str, evidence: str = ''):
        self.db.execute('INSERT OR IGNORE INTO aliases VALUES (?,?,?,?)',
                        (local_id, global_id, basis, evidence))

    def propose_label_aliases(self) -> int:
        """Propose `term:<slug>` for local entities sharing a slug across two or
        more sources. A name is not rigid, so this is a proposal with basis
        'same-label', used only when the caller accepts that basis."""
        rows = self.db.execute("SELECT id FROM nodes WHERE kind='entity' AND id LIKE 'local:%'").fetchall()
        by_slug = defaultdict(set)
        for (ident,) in rows:
            m = LOCAL.match(ident)
            if m:
                by_slug[m['slug']].add((m['source'], ident))
        n = 0
        for s, members in by_slug.items():
            if len({src for src, _ in members}) > 1:
                for _, ident in members:
                    self.propose_alias(ident, f'term:{s}', 'same-label')
                    n += 1
        return n

    # ── reading ──────────────────────────────────────────────────────────────
    def resolver(self, accept: Iterable[str] = ()):
        accept = set(accept)
        table = {}
        for local, glob, basis in self.db.execute('SELECT local_id, global_id, basis FROM aliases'):
            if basis in accept:
                table[local] = glob
        return lambda ident: table.get(ident, ident)

    def claims(self) -> list[dict]:
        out = []
        for cid, statement, scope, props in self.db.execute(
                "SELECT id, label, scope, props FROM nodes WHERE kind='claim'"):
            p = json.loads(props)
            ends = dict(self.db.execute(
                "SELECT kind, dst FROM edges WHERE src=? AND kind IN ('subject','object')", (cid,)))
            out.append({'id': cid, 'statement': statement, 'scope': scope,
                        'subject': ends.get('subject'), 'object': ends.get('object'), **p})
        return out

    def claim_groups(self, accept: Iterable[str] = ()) -> dict[str, list[dict]]:
        """Claims grouped by resolved key. Only claims with a relation can be
        matched; the rest form singleton groups keyed by their own id."""
        resolve = self.resolver(accept)
        groups = defaultdict(list)
        for c in self.claims():
            if c['predicate'] and (c['object'] or c['value']):
                # Only stated world time identifies a claim; an assertion date is
                # a bound, so the same finding in a 2024 and a 2025 paper matches.
                # Polarity is left out: affirmed and negated land in one group,
                # and that group is a contradiction.
                stated = c['holds'].get('basis') == 'stated'
                key = _hash(resolve(c['subject']), c['predicate'],
                            resolve(c['object']) if c['object'] else slug(c['value']),
                            c['holds'].get('start') if stated else None,
                            c['holds'].get('end') if stated else None)
            else:
                key = c['id']
            groups[key].append(c)
        return dict(groups)

    def neighbours(self, entity: str) -> list[tuple[str, str, str]]:
        return self.db.execute(
            "SELECT src, kind, dst FROM edges WHERE (src=? OR dst=?) AND kind LIKE 'rel:%'",
            (entity, entity)).fetchall()

    def stats(self, accept: Iterable[str] = ()) -> dict:
        kinds = dict(self.db.execute('SELECT kind, count(*) FROM nodes GROUP BY kind'))
        groups = self.claim_groups(accept)
        multi = [g for g in groups.values() if len({c['source'] for c in g}) > 1]
        contradictions = [g for g in multi if len({c['polarity'] for c in g}) > 1]
        # A weaker level than the same claim: two sources making claims with the
        # same subject and relation. In research text this is where sources talk
        # about the same thing and can be compared, even when objects differ.
        resolve = self.resolver(accept)
        topics = defaultdict(set)
        for c in self.claims():
            if c['predicate']:
                topics[(resolve(c['subject']), c['predicate'])].add(c['source'])
        return {'nodes': kinds, 'edges': self.db.execute('SELECT count(*) FROM edges').fetchone()[0],
                'relation_edges': self.db.execute("SELECT count(*) FROM edges WHERE kind LIKE 'rel:%'").fetchone()[0],
                'claim_groups': len(groups), 'claims_in_two_or_more_sources': len(multi),
                'contradictions': len(contradictions),
                'subject_relation_in_two_or_more_sources': sum(len(v) > 1 for v in topics.values())}
