"""The research store (decision 0002): global report atoms, local projects.

Two layers, next to the raw store:

- **Global** (`research/` beside `raw/`, per user): report atoms and a
  verification cache. A report atom is "this page says these words": its id is
  hash(page sha256, the words' own span in the page, the words normalised), so
  two projects quoting the same words from the same page hold one atom, however
  they cut their passages. Checking words against content-addressed bytes is
  deterministic, so the verdict is computed once and cached, never redone. Only
  closed report atoms go global: the words stand in the bytes and the page is
  dated (its knowledge time). An undated one stays local, open (0001, rules 3, 5).
  A report atom's `holds` (world time) is null: the report is closed, its claim
  stays open until a chain states its time, and chains are local.
- **Local** (`research/projects/<name>/`): one append-only records.jsonl of typed
  records. Each carries `at` (when it was declared) and `declared_by`; a record
  grounded in a source also carries the passage its words were re-found in
  (`in`, a global passage id = hash(sha256, start, end)), its report atom and
  `known`, the source's own date: when the world could know it. An undo
  withdraws an earlier record by id; the file keeps both.

The workspace rules hold here too. The raw store, its fetch log and the
verification cache beside it are the trust boundary (like the fetch log, the
cache is not yet hash-chained). Nothing in a project is trusted: records keep
what was declared, and every view rebuilds a workspace from them and runs the
workspace's own checks, so a part the workspace would drop on use is dropped
here, with the same words. A position query keeps only evidence whose source
was dated by then; the frame is the question asked from that position, not
evidence, so it is always kept.

Reuse across projects is an explicit import (a parse): everything is
re-grounded against the bytes, atoms the project already has cost nothing,
readings arrive as "inherited: unchecked in this context" and count only once
the project confirms them for its own explanation, and the source project is
never written.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from gleipnir.atomiser import _ws
from gleipnir.rawstore import RawStore
from gleipnir.workspace import ToolError, Workspace, date_candidates, page_text, pick_date

TYPES = ('session', 'source', 'passage', 'atom', 'chain', 'question', 'explanation', 'prediction', 'reading',
         'status', 'position', 'looked_for', 'rests', 'relay', 'origin', 'original', 'accountability', 'link',
         'review', 'undo')
#: Keys a record adds to what was declared; the rest is the declared part, as written.
META = ('id', 'type', 'part', 'key', 'in', 'atom', 'source', 'known', 'at', 'declared_by', 'imported', 'inherited',
        'question', 'explanation', 'evidence', 'i')
UNHASHED = ('id', 'at', 'declared_by', 'known', 'imported', 'inherited')
PASSAGE_KEYS = ('passage', 'passage_id', 'n_in', 'case_definition_in', 'year_in')
#: What a position query filters by the source's date; the frame and the sources themselves stay.
EVIDENCE = ('atom', 'chain', 'explanation', 'reading', 'status', 'position', 'rests', 'relay', 'review')
FRAME = ('session', 'question', 'explanation', 'prediction', 'looked_for', 'undo')
UNCHECKED = 'unchecked in this context'
NAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,80}')


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def _hash(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def passage_id(sha: str, start: int, end: int) -> str:
    return 'P' + _hash([sha, start, end])[:16]


def atom_id(sha: str, start: int, end: int, report: str) -> str:
    return 'A' + _hash([sha, start, end, report])[:16]


def _record(**fields) -> dict:
    """A record with its id: a hash of what it says, not of when or by whom, so the
    same declaration made twice, or imported twice, is one record."""
    rec = {k: v for k, v in fields.items() if v is not None or k not in META}   # a declared null stays
    return {'id': 'r' + _hash({k: v for k, v in rec.items() if k not in UNHASHED})[:12], **rec}


def _lines(path: Path) -> list[dict]:
    return [json.loads(line) for line in open(path) if line.strip()] if path.exists() else []


def _append(path: Path, entries: list[dict]):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'a') as f:
        for e in entries:
            f.write(json.dumps(e, ensure_ascii=False) + '\n')


def locate(text: str, words: str, start: int = 0, end: int | None = None) -> tuple[int, int] | None:
    """Where the words stand in text[start:end], as offsets into the text, read the
    way every quote check reads (`_ws`): spacing collapsed, case folded,
    hyphenated line breaks joined. None if they do not stand there."""
    end = len(text) if end is None else min(end, len(text))
    norm, pos, i, space = [], [], start, False
    while i < end:
        if text.startswith('-\n', i):
            i += 2
            continue
        c = text[i]
        if c.isspace():
            if norm and not space:
                norm.append(' ')
                pos.append(i)
            space = True
        else:
            for f in c.casefold():
                norm.append(f)
                pos.append(i)
            space = False
        i += 1
    target = _ws(words or '')
    at = ''.join(norm).find(target) if target else -1
    return (pos[at], pos[at + len(target) - 1] + 1) if at >= 0 else None


def _known_by(date: str | None, k: str) -> bool:
    """A partial date is known by k only once all of it has passed: "2018" is not
    known by 2018-06-01. No date: never known (0001, rule 3)."""
    pad = lambda d: d + '-99-99'[len(d) - 4:] if len(d) < 10 else d
    return bool(date) and pad(date) <= pad(k)


class Store:
    """The global layer: report atoms and the verification cache, per user."""

    def __init__(self, root: Path, raw: Path):
        self.root, self.raw_dir = Path(root), Path(raw)
        self.raw = RawStore(raw)
        self.atoms = {a['id']: a for a in _lines(self.root / 'atoms.jsonl')}
        self.grounded, self.verdicts = {}, {}
        for v in _lines(self.root / 'verified.jsonl'):
            self.grounded[v['grounding']] = v
            if v.get('atom'):
                self.verdicts.setdefault(v['atom'], v)
        self.counts = {'cached': 0, 'atom_cached': 0, 'verified': 0}
        self._texts, self._fetched = {}, None

    def project(self, name: str) -> 'Project':
        if not isinstance(name, str) or not NAME.fullmatch(name):
            raise ToolError(f'a project name is letters, digits, ".", "_" or "-": {name!r}')
        return Project(self, name)

    def text(self, sha: str) -> str:
        if sha not in self._texts:
            if not self.raw.path_of(sha).exists():
                raise ToolError(f'raw bytes {sha[:10]} missing from the store at {self.raw.root.resolve()}: '
                                'pass --store with the store the project was fetched into')
            self._texts[sha] = page_text(self.raw.get(sha))
        return self._texts[sha]

    def fetched(self, sha: str, url: str) -> bool:
        """The fetch log shows these bytes fetched from this address."""
        if self._fetched is None or (sha, url) not in self._fetched:
            self._fetched = {(f.content_hash, f.resource_id) for f in self.raw.fetches() if f.http_status == 200}
        return (sha, url) in self._fetched and self.raw.path_of(sha).exists()

    def verify(self, sha: str, url: str, start: int, end: int, words: str) -> dict:
        """The verdict on "this passage of this page says these words": the atom and
        the page's date, from the bytes once, then from the cache. A lookup the
        cache answers reads no bytes; one whose atom is cached reuses its verdict."""
        key = _hash([sha, url, start, end, _ws(words)])[:24]
        if key in self.grounded:
            self.counts['cached'] += 1
            return self.grounded[key]
        span = locate(self.text(sha), words, start, end) if 0 <= start < end else None
        aid = atom_id(sha, *span, _ws(words)) if span else None
        if aid in self.verdicts:
            self.counts['atom_cached'] += 1
            said = self.verdicts[aid]['said']
        else:
            self.counts['verified'] += 1
            said = pick_date(date_candidates(self.raw.get(sha), url))[0] if span else None
        v = {'grounding': key, 'atom': aid, 'sha256': sha, 'span': list(span) if span else None,
             'report': _ws(words), 'said': said, 'at': _now()}
        _append(self.root / 'verified.jsonl', [v])
        self.grounded[key] = v
        if aid:
            self.verdicts.setdefault(aid, v)
        return v

    def promote(self, v: dict) -> bool:
        """A closed report atom goes global, once. Closed: the words stand and the
        page is dated. Returns whether it is global."""
        if not v.get('atom') or not v.get('said'):
            return False
        if v['atom'] not in self.atoms:
            a = {'id': v['atom'], 'sha256': v['sha256'], 'span': v['span'], 'report': v['report'],
                 'said': v['said'], 'holds': None}
            _append(self.root / 'atoms.jsonl', [a])
            self.atoms[a['id']] = a
        return True


class Project:
    """The local layer: one project's records, append-only."""

    def __init__(self, store: Store, name: str):
        self.store, self.name = store, name
        self.root = store.root / 'projects' / name
        self.path = self.root / 'records.jsonl'

    def records(self) -> list[dict]:
        return _lines(self.path)

    def standing(self) -> list[dict]:
        """The records not withdrawn. An undo withdraws the records before it with
        that id; the same record declared again after it stands again."""
        out = []
        for r in self.records():
            if r.get('type') == 'undo' and (r.get('note') or '').strip():
                out = [x for x in out if x.get('id') != r.get('withdraws')]
            if r.get('type') in TYPES:
                out.append(r)
        return out

    def add(self, records: list[dict]) -> list[dict]:
        """Append the records this project has never held: adding the same records
        twice adds nothing, and a record it withdrew stays withdrawn, unless it is
        declared again after an undo in the same batch."""
        have, new, withdrawn = {r.get('id') for r in self.records()}, [], set()
        for r in records:
            if r['id'] in have and r['id'] not in withdrawn:
                continue
            withdrawn.discard(r['id'])
            have.add(r['id'])
            new.append(r)
            if r['type'] == 'undo':
                withdrawn.add(r['withdraws'])
        _append(self.path, new)
        return new

    def log(self, tool: str, **detail):
        _append(self.root / 'log.jsonl', [{'at': _now(), 'tool': tool, **detail}])

    def undo(self, record: str, note: str, declared_by: str = 'llm') -> dict:
        if not note.strip():
            raise ToolError('an undo needs a --note saying why')
        if record not in {r['id'] for r in self.standing() if r['type'] != 'undo'}:
            raise ToolError(f'nothing to undo: {record} is not a standing record of {self.name}')
        rec = _record(type='undo', withdraws=record, note=note, at=_now(), declared_by=declared_by)
        _append(self.path, [rec])
        self.log('undo', record=record)
        return rec


# ── compile: a workspace becomes a project ──────────────────────────────────
def _last(log: list[dict], tool: str, default: str) -> str:
    return next((e['at'] for e in reversed(log) if e.get('tool') == tool and e.get('at')), default)


class _Grounder:
    """Grounds declared words in passages, through the store's cache."""

    def __init__(self, store: Store, sources: dict, passages: dict):
        self.store, self.sources, self.passages = store, sources, passages   # global passage id -> (sid, start, end)
        self.local: list[dict] = []

    def __call__(self, gid: str | None, words: str | None, original=lambda sid: None) -> dict:
        """in, source, atom and known for words said in a passage, or what stands of them."""
        p = self.passages.get(gid)
        if not p:
            return {'in': gid}
        sid, start, end = p
        s = self.sources.get(sid) or {}
        out = {'in': gid, 'source': sid}
        if not isinstance(words, str) or not words.strip() or not s.get('sha256'):
            return out
        try:
            v = self.store.verify(s['sha256'], s['url'], start, end, words)
        except ToolError:
            return out
        if v.get('atom'):
            out['atom'] = v['atom']
            if not self.store.promote(v):
                self.local.append(_record(type='atom', key=v['atom'], sha256=v['sha256'], span=v['span'],
                                          report=v['report'], said=None, holds=None, at=v['at'],
                                          declared_by='compile'))
        out['known'] = original(sid) or v.get('said')
        return out

    def first(self, sid: str, words: str, original=lambda sid: None) -> dict:
        """Ground words that may stand in any passage of the source: the first that holds them."""
        for gid, (psid, _, _) in self.passages.items():
            if psid == sid and (g := self(gid, words, original)).get('atom'):
                return g
        return {'source': sid}


def compile_workspace(store: Store, ws_dir: Path, name: str) -> dict:
    """Compile a workspace into a project plus global report atoms. Read-only on the
    workspace. Everything declared is kept as declared; words that do not stand in
    their passage get no atom, and the view drops them on use as the workspace does."""
    ws_dir = Path(ws_dir)
    if not (ws_dir / 'workspace.json').exists():
        raise ToolError(f'{ws_dir} is not a workspace: no workspace.json')
    project = store.project(name)
    ws = Workspace(ws_dir, store.raw_dir)
    read = lambda n, d: json.loads((ws_dir / n).read_text()) if (ws_dir / n).exists() else d
    meta, log = read('workspace.json', {}), _lines(ws_dir / 'log.jsonl')
    path = str(ws_dir.resolve())
    other = [r['path'] for r in project.standing() if r['type'] == 'session' and r.get('path') != path]
    if other:
        raise ToolError(f'project {name} was compiled from {other[0]}: compile {ws_dir} into its own project '
                        f'and bring it in with `import {name} PROJECT`')
    created = meta.get('created') or _now()
    before = dict(store.counts)
    sources = {s['id']: s for s in read('sources.json', []) if isinstance(s, dict) and s.get('id')}
    aliases, spans = {}, {}
    for p in read('passages.json', []):
        s = sources.get(p.get('source_id')) if isinstance(p, dict) else None
        if s and isinstance(p.get('start'), int) and isinstance(p.get('end'), int) and s.get('sha256'):
            aliases[p['id']] = passage_id(s['sha256'], p['start'], p['end'])
            spans[aliases[p['id']]] = (p['source_id'], p['start'], p['end'])
    ground = _Grounder(store, sources, spans)
    originals = {}

    def original(sid):
        if sid not in originals:
            try:
                originals[sid] = ws._original(ws._source(sid)) if sources[sid].get('original') else None
            except (ToolError, ValueError, KeyError):
                originals[sid] = None
        return originals[sid]

    def g(pid, words):
        return ground(aliases.get(pid, pid), words, original)

    def to_global(d: dict) -> dict:
        return {k: aliases.get(v, v) if k in PASSAGE_KEYS and isinstance(v, str) else v for k, v in d.items()}

    out = [_record(type='session', key=ws_dir.name, path=path, question=meta.get('question'), created=created,
                   at=created, declared_by='compile')]
    for sid, s in sources.items():
        out.append(_record(type='source', key=sid, **{k: v for k, v in s.items()
                                                       if k not in ('id', 'origin', 'accountability', 'original')},
                           at=s.get('fetched_at') or created, declared_by='fetch'))
        for kind in ('origin', 'accountability'):
            if isinstance(s.get(kind), dict):
                d = s[kind]
                out.append(_record(type=kind, source=sid, **{k: v for k, v in d.items() if k not in ('at', 'declared_by')},
                                   at=d.get('at') or created, declared_by=d.get('declared_by') or 'llm'))
        if isinstance(s.get('original'), dict):
            d = s['original']
            out.append(_record(type='original', **{k: v for k, v in d.items() if k not in ('at', 'declared_by')},
                               **ground.first(sid, d.get('words')), at=d.get('at') or created,
                               declared_by=d.get('declared_by') or 'llm'))
    for alias, gid in aliases.items():
        sid, start, end = spans[gid]
        out.append(_record(type='passage', key=gid, alias=alias, source=sid, sha256=sources[sid]['sha256'],
                           start=start, end=end, at=_last([e for e in log if e.get('passage') == alias], 'cut',
                                                            sources[sid].get('fetched_at') or created),
                           declared_by='cut'))
    # the frame
    at = _last(log, 'frame', created)
    for q in (read('frame.json', {}).get('questions') or []):
        if not isinstance(q, dict):
            continue
        out.append(_record(type='question', part='frame', key=q.get('id'),
                           **{k: v for k, v in q.items() if k not in ('id', 'explanations')}, at=at, declared_by='llm'))
        for h in q.get('explanations') or []:
            if not isinstance(h, dict):
                continue
            out.append(_record(type='explanation', part='frame', key=h.get('id'), question=q.get('id'),
                               **{k: v for k, v in h.items() if k not in ('id', 'predictions')}, at=at, declared_by='llm'))
            out += [_record(type='prediction', part='frame', key=p.get('id'), explanation=h.get('id'),
                            **{k: v for k, v in p.items() if k != 'id'}, at=at, declared_by='llm')
                    for p in h.get('predictions') or [] if isinstance(p, dict)]
    # the matrix: every grounded part is a report atom plus a local record
    at, m = _last(log, 'matrix', created), read('matrix.json', {})
    m = m if isinstance(m, dict) else {}
    for q in m.get('questions') or []:
        if isinstance(q, dict):
            out.append(_record(type='question', part='matrix', key=q.get('id'),
                               **{k: v for k, v in q.items() if k != 'id'}, at=at, declared_by='llm'))
    for h in m.get('explanations') or []:
        if not isinstance(h, dict):
            continue
        where = h.get('proposed_in') if isinstance(h.get('proposed_in'), dict) else {}
        out.append(_record(type='explanation', part='matrix', key=h.get('id'),
                           **{k: v for k, v in h.items() if k not in ('id', 'proposed_in', 'predictions')},
                           **({'proposed_in': to_global(where)} if 'proposed_in' in h else {}),
                           **g(where.get('passage'), where.get('words')), at=at, declared_by='llm'))
        out += [_record(type='prediction', part='matrix', key=p.get('id'), explanation=h.get('id'),
                        **{k: v for k, v in p.items() if k != 'id'}, at=at, declared_by='llm')
                for p in h.get('predictions') or [] if isinstance(p, dict)]
    for r in m.get('evidence') or []:
        if not isinstance(r, dict):
            continue
        rid, row = r.get('id'), g(r.get('passage'), r.get('words'))
        out.append(_record(type='rests', part='matrix', key=rid,
                           **to_global({k: v for k, v in r.items() if k not in ('id', 'status', 'positions', 'cells')}),
                           **row, at=at, declared_by='llm'))
        for kind, parts in (('status', r.get('status')), ('position', r.get('positions'))):
            for i, s in enumerate(parts or []):
                if isinstance(s, dict):
                    out.append(_record(type=kind, part='matrix', evidence=rid, i=i, **to_global(s),
                                       **g(s.get('passage'), s.get('words')), at=at, declared_by='llm'))
        for hid, c in (r.get('cells') if isinstance(r.get('cells'), dict) else {}).items():
            if isinstance(c, dict):
                c = to_global({**c, 'passage': c.get('passage') or r.get('passage')})
                cell = ground(c['passage'], c.get('words'), original) if c.get('words') else \
                    {**ground(c['passage'], None), **({'known': row['known']} if 'known' in row else {})}
                out.append(_record(type='reading', part='matrix', evidence=rid, explanation=hid, **c, **cell,
                                   at=at, declared_by='llm'))
    for e in m.get('looked_for') or []:
        if isinstance(e, dict):
            out.append(_record(type='looked_for', **e, at=at, declared_by='llm'))
    # chains (atoms.jsonl) and the declarations on them
    chains = {}
    for a in _lines(ws_dir / 'atoms.jsonl'):
        chains[a.get('uid')] = a
        out.append(_record(type='chain', **to_global(a), **g(a.get('passage_id'), a.get('quote')),
                           at=_last(log, 'add', created), declared_by='llm'))

    def chain_source(uid):
        return spans.get(aliases.get((chains.get(uid) or {}).get('passage_id')), (None,))[0]

    for fname, t, key in (('evidence.jsonl', 'rests', 'evidence'), ('relays.jsonl', 'relay', 'act')):
        for e in _lines(ws_dir / fname):
            body = {k: v for k, v in e.items() if k not in ('at', 'declared_by', 'undo')}
            if e.get('undo'):
                out += _undo(out, e, lambda r: r['type'] == t and r.get('part') == 'chain' and r.get('uid') == e.get('uid')
                             and (r.get(key) or '').strip() == (e.get(key) or '').strip() and e.get('uid') in chains)
                continue
            sid = chain_source(e.get('uid'))
            out.append(_record(type=t, part='chain', **body, **(ground.first(sid, e.get('words'), original) if sid else {}),
                               at=e.get('at') or created, declared_by=e.get('declared_by') or 'llm'))
    for e in _lines(ws_dir / 'reviews.jsonl'):
        a = chains.get(e.get('uid')) or {}
        out.append(_record(type='review', **{k: v for k, v in e.items() if k not in ('at', 'declared_by')},
                           **g(a.get('passage_id'), e.get('words')), at=e.get('at') or created,
                           declared_by=e.get('declared_by') or 'llm'))
    for e in _lines(ws_dir / 'links.jsonl'):
        if e.get('undo'):
            pair = {e.get('a'), e.get('b')}
            out += _undo(out, e, lambda r: r['type'] == 'link' and {r.get('a'), r.get('b')} == pair)
            continue
        out.append(_record(type='link', **{k: v for k, v in e.items() if k not in ('at', 'declared_by')},
                           at=e.get('at') or created, declared_by=e.get('declared_by') or 'llm'))
    for e in _lines(ws_dir / 'evidence_status.jsonl'):
        out.append(_record(type='status', part='declared', **to_global({k: v for k, v in e.items()
                                                                        if k not in ('at', 'declared_by')}),
                           **g(e.get('passage'), e.get('words')), at=e.get('at') or created,
                           declared_by=e.get('declared_by') or 'llm'))
    new = project.add(ground.local + out)
    summary = {'project': name, 'records': len({r['id'] for r in ground.local + out}), 'new_records': len(new),
               'atoms_global': len({r['atom'] for r in out if r.get('atom') in store.atoms}),
               'atoms_local': len({r['atom'] for r in out if r.get('atom') and r['atom'] not in store.atoms}),
               'not_grounded': sum(1 for r in out if r.get('in') and not r.get('atom') and _words(r)),
               'cache': {k: store.counts[k] - before[k] for k in store.counts},
               **({'not_compiled': 'explanations.jsonl (why sources differ) has no record type yet'}
                  if (ws_dir / 'explanations.jsonl').exists() else {})}
    project.log('compile', workspace=path, **{k: v for k, v in summary.items() if k != 'project'})
    return summary


def _words(r: dict) -> str | None:
    return r.get('quote') if r.get('type') == 'chain' else r.get('words')


def _undo(out: list[dict], e: dict, match) -> list[dict]:
    """An undo entry of a workspace file withdraws the records it took back. Without a note it never counted."""
    if not (e.get('note') or '').strip():
        return []
    return [_record(type='undo', withdraws=r['id'], note=e['note'], at=e.get('at'),
                    declared_by=e.get('declared_by') or 'llm') for r in out if match(r)]


# ── views: a workspace rebuilt from records, read by the workspace's own logic ──
class _Session(Workspace):
    """A workspace made of records instead of files. The workspace's checks and
    screens run on it unchanged; it writes nothing."""

    def __init__(self, files: dict, raw: Path, root: Path):
        super().__init__(root, raw)
        self.files = files

    def _json(self, name, default):
        return json.loads(json.dumps(self.files[name])) if name in self.files else default

    def _json_lines(self, name):
        return json.loads(json.dumps(self.files.get(name, [])))

    def _records(self):
        return {a['uid']: a for a in self._json_lines('atoms.jsonl') if isinstance(a, dict) and 'uid' in a}

    def _save(self, name, value):
        raise ToolError('a view is read-only: change the project by declaring records')

    def log(self, tool, **detail):
        pass


def _declared(r: dict, drop=()) -> dict:
    return {k: v for k, v in r.items() if k not in META and k not in drop}


def _counting(recs: list[dict]) -> list[dict]:
    """The records that count: an inherited reading only once it is confirmed here."""
    confirmed = {r.get('record') for r in recs if r['type'] == 'review' and r.get('decision') == 'confirmed'}
    return [r for r in recs if not (r.get('inherited') and r['id'] not in confirmed)]


def _files(recs: list[dict], as_of: str | None = None) -> dict:
    """The workspace files these records stand for, passages under their workspace ids."""
    by = lambda t, part=None: [r for r in recs if r['type'] == t and (part is None or r.get('part') == part)]
    names = {}
    for p in by('passage'):
        names.setdefault(p.get('alias'), set()).add(p['key'])
    view = {p['key']: p['alias'] if len(names[p.get('alias')]) == 1 else p['key'] for p in by('passage')}
    local = lambda d: {k: view.get(v, v) if k in PASSAGE_KEYS and isinstance(v, str) else v for k, v in d.items()}
    session = next(iter(by('session')), {})
    files = {'workspace.json': {'question': session.get('question'),     # a position query speaks from k
                                'created': as_of + '-01-01'[:10 - len(as_of)] if as_of else session.get('created')}}
    sources = {}
    for s in by('source'):
        sources[s['key']] = {'id': s['key'], **_declared(s), 'origin': None}
    for kind in ('origin', 'accountability', 'original'):
        for d in by(kind):
            if d.get('source') in sources:
                sources[d['source']][kind] = {**_declared(d), 'declared_by': d.get('declared_by'), 'at': d.get('at')}
    files['sources.json'] = list(sources.values())
    files['passages.json'] = [{'id': view[p['key']], 'source_id': p.get('source'), 'start': p.get('start'),
                               'end': p.get('end')} for p in by('passage')]
    preds = lambda part, hid: [{'id': p['key'], **_declared(p)} for p in by('prediction', part)
                               if p.get('explanation') == hid]
    if frame := [{'id': q['key'], **_declared(q),
                  'explanations': [{'id': h['key'], **_declared(h), 'predictions': preds('frame', h['key'])}
                                   for h in by('explanation', 'frame') if h.get('question') == q['key']]}
                 for q in by('question', 'frame')]:
        files['frame.json'] = {'questions': frame}
    rows = []
    for r in by('rests', 'matrix'):
        row = {'id': r['key'], **local(_declared(r)), 'status': [], 'positions': [], 'cells': {}}
        for kind, key in (('status', 'status'), ('position', 'positions')):
            row[key] = [local(_declared(s)) for s in sorted(by(kind, 'matrix'), key=lambda s: s.get('i', 0))
                        if s.get('evidence') == r['key']]
        row['cells'] = {c['explanation']: local(_declared(c)) for c in by('reading') if c.get('evidence') == r['key']}
        rows.append(row)
    explanations = []
    for h in by('explanation', 'matrix'):
        e = {'id': h['key'], **_declared(h, ('proposed_in',))}
        if 'proposed_in' in h:
            e['proposed_in'] = local(h['proposed_in'])
        if p := preds('matrix', h['key']):
            e['predictions'] = p
        explanations.append(e)
    present = {r['id'] for r in rows}
    looked = [_declared(e) for e in by('looked_for') if e.get('row') is None or e.get('row') in present or not as_of]
    if by('question', 'matrix') or explanations or rows or looked:
        files['matrix.json'] = {'questions': [{'id': q['key'], **_declared(q)} for q in by('question', 'matrix')],
                                'explanations': explanations, 'evidence': rows, 'looked_for': looked}
    files['atoms.jsonl'] = [{'uid': c.get('uid'), **local(_declared(c, ('uid',)))} for c in by('chain')]
    for name, t in (('evidence.jsonl', 'rests'), ('relays.jsonl', 'relay')):
        files[name] = [{**_declared(e), **({'evidence': e.get('evidence')} if t == 'rests' else {}), 'declared_by': e.get('declared_by'), 'at': e.get('at')} for e in by(t, 'chain')]
    files['reviews.jsonl'] = [{**_declared(e), 'declared_by': e.get('declared_by'), 'at': e.get('at')}
                              for e in by('review') if 'uid' in e]
    files['links.jsonl'] = [{**_declared(e), 'declared_by': e.get('declared_by'), 'at': e.get('at')} for e in by('link')]
    files['evidence_status.jsonl'] = [{'evidence': e.get('evidence'), **local(_declared(e)),
                                       'declared_by': e.get('declared_by'), 'at': e.get('at')}
                                      for e in by('status', 'declared')]
    return files


def as_of(recs: list[dict], k: str, date_of) -> list[dict]:
    """The position query: the records known as of knowledge time k. Evidence
    counts only if its source was dated by k (an undated source never is); an
    explanation not yet proposed by k is left out, with its readings and a
    row's readings, statuses and positions go with the row."""
    keep = [r for r in recs if r['type'] not in EVIDENCE or (r['type'] == 'explanation' and not r.get('source'))
            or (r.get('source') and _known_by(date_of(r['source']), k))]
    rows = {r['key'] for r in keep if r['type'] == 'rests' and r.get('part') == 'matrix'}
    hyps = {r['key'] for r in keep if r['type'] == 'explanation'}
    return [r for r in keep if not (r['type'] in ('reading', 'status', 'position') and r.get('part') != 'declared'
                                    and r.get('evidence') not in rows)
            and not (r['type'] == 'reading' and r.get('explanation') not in hyps)]


def _session(store: Store, project: Project, recs: list[dict], as_of_k: str | None = None) -> _Session:
    return _Session(_files(recs, as_of_k), store.raw_dir, project.root)


def records_as_of(store: Store, name: str, k: str | None = None) -> list[dict]:
    """A project's standing records that count, as of knowledge time k (all, without k)."""
    project = store.project(name)
    recs = _counting(project.standing())
    if not recs:
        raise ToolError(f'no project {name}: compile a workspace into it or import into it first')
    if k is None:
        return recs
    if not Workspace._dated(k):
        raise ToolError('--as-of is a date: YYYY, YYYY-MM or YYYY-MM-DD')
    s, dates = _session(store, project, recs), {}

    def date_of(sid):
        if sid not in dates:
            try:
                src = s._source(sid)
                dates[sid] = s._original(src) or s._date(src)[0]
            except (ToolError, ValueError, OSError):
                dates[sid] = None
        return dates[sid]
    return as_of(recs, k, date_of)


def matrix_view(store: Store, name: str, k: str | None = None) -> dict:
    """The matrix computed from a project's records, as `matrix WS` shows it."""
    project = store.project(name)
    return _session(store, project, records_as_of(store, name, k), k).matrix_show()


# ── import: a parse from project A into project B ──────────────────────────
#: Never imported: a project's framing is its own, and its confirmations and undos are about its own records.
NOT_IMPORTED = ('session', 'question', 'explanation', 'prediction', 'looked_for', 'undo')
ONE_PER_SOURCE = ('origin', 'accountability', 'original')


def _claims(recs: list[dict]) -> dict:
    """explanation id -> its claim, as the project frames it."""
    return {h['key']: h['claim'] for h in recs if h['type'] == 'explanation' and h.get('claim')}


def import_project(store: Store, b: str, a: str, only: list[str] | None = None, declared_by: str = 'llm') -> dict:
    """Import A's evidence into B. Everything is re-grounded against the bytes;
    atoms B already has cost nothing; readings arrive unchecked in B's context
    and count only once B confirms them; A is never written."""
    if a == b:
        raise ToolError('import from another project: a project already holds its own records')
    A, B = store.project(a), store.project(b)
    theirs = A.standing()
    if not theirs:
        raise ToolError(f'no project {a}: nothing to import')
    mine, before, at = B.standing(), dict(store.counts), _now()
    claims = _claims(theirs)
    recs = [r for r in theirs if r['type'] not in NOT_IMPORTED
            and not (r['type'] == 'review' and 'record' in r)]
    if only:
        rows = set(only)
        if missing := rows - {r.get('key') for r in recs if r['type'] == 'rests' and r.get('part') == 'matrix'} \
                - {r.get('evidence') for r in recs}:
            raise ToolError(f'{a} has no evidence {", ".join(sorted(missing))}: `view {a} matrix` lists its rows')
        chosen = [r for r in recs if (r['type'] == 'rests' and r.get('key') in rows) or r.get('evidence') in rows]
        chosen += [r for r in recs if r['type'] == 'status' and r.get('part') == 'declared' and r.get('evidence') in rows]
        uids = {r.get('uid') for r in chosen if r.get('part') == 'chain'}
        chosen += [r for r in recs if r['type'] in ('chain', 'relay', 'review') and r.get('uid') in uids]
        sids = {r.get('source') for r in chosen} | {r.get('source') for r in recs if r['type'] == 'chain'
                                                    and r.get('uid') in uids}
        atoms = {r.get('atom') for r in chosen}
        chosen += [r for r in recs if r['type'] in ('source', 'passage', 'atom', 'link') + ONE_PER_SOURCE and (
            r.get('key') in sids or r.get('source') in sids or r.get('key') in atoms)]
        ids = {r['id'] for r in chosen}
        recs = [r for r in recs if r['id'] in ids]
    have, held = {r['id'] for r in mine}, {r.get('atom') for r in mine if r.get('atom')}
    rows_b = {r['key']: r['id'] for r in mine if r['type'] == 'rests' and r.get('part') == 'matrix'}
    cells_b = {(r.get('evidence'), r.get('explanation')) for r in mine if r['type'] == 'reading'}
    per_source_b = {(r['type'], r.get('source')): r['id'] for r in mine if r['type'] in ONE_PER_SOURCE}
    sources = {r['key']: r for r in theirs if r['type'] == 'source'}
    spans = {r['key']: (r.get('source'), r.get('start'), r.get('end')) for r in theirs if r['type'] == 'passage'}
    out, refused, counts = [], [], {'already_in_b': 0, 'atoms_already_in_b': 0}
    for r in recs:
        if r['id'] in have:
            counts['already_in_b'] += 1
            continue
        why = None
        if r['type'] == 'rests' and r.get('part') == 'matrix' and r['key'] in rows_b:
            why = f'{b} has its own row {r["key"]}: compare the two and declare one in {b}'
        elif r['type'] == 'reading' and (r.get('evidence'), r.get('explanation')) in cells_b:
            why = f'{b} already reads {r.get("evidence")} for {r.get("explanation")}'
        elif r['type'] in ONE_PER_SOURCE and (r['type'], r.get('source')) in per_source_b:
            why = f'{b} has declared its own {r["type"]} for {r.get("source")}'
        elif r['type'] == 'source' and not store.fetched(r.get('sha256'), r.get('url')):
            why = f'{r["key"]}: its bytes and url do not match a fetch record in the store'
        elif r['type'] == 'passage':
            s = sources.get(r.get('source')) or {}
            if r['key'] != passage_id(s.get('sha256'), r.get('start'), r.get('end')) or \
                    not store.fetched(s.get('sha256'), s.get('url')):
                why = f'{r["key"]}: the passage does not match its source\'s bytes'
        elif r.get('atom') and r['atom'] in held:
            counts['atoms_already_in_b'] += 1                     # costs nothing
        elif r.get('atom'):
            sid, start, end = spans.get(r.get('in'), (None, None, None))
            s = sources.get(sid) or {}
            try:
                v = store.verify(s.get('sha256'), s.get('url'), start, end, _words(r)) \
                    if s and store.fetched(s.get('sha256'), s.get('url')) else {}
            except (ToolError, TypeError):
                v = {}
            if v.get('atom') != r['atom']:
                why = f'{r["id"]}: its words no longer stand in its passage of the bytes'
            elif not store.promote(v) and not any(x['type'] == 'atom' and x.get('key') == r['atom'] for x in out + mine):
                out += [x for x in theirs if x['type'] == 'atom' and x.get('key') == r['atom']][:1]   # stays open, local
            held.add(r['atom'])
        if why:
            refused.append({'record': r['id'], 'type': r['type'], 'why': why})
            continue
        rec = {**r, 'imported': {'from': a, 'at': at, 'by': declared_by}}
        if r['type'] == 'reading':
            rec['inherited'] = {'status': UNCHECKED, 'from': a, 'claim': claims.get(r.get('explanation'))}
            cells_b.add((r.get('evidence'), r.get('explanation')))
        if r['type'] == 'rests' and r.get('part') == 'matrix':
            rows_b[r['key']] = r['id']
        out.append(rec)
        have.add(r['id'])
    new = B.add(out)
    summary = {'project': b, 'from': a, **({'only': only} if only else {}), 'imported': len(new),
               'readings_unchecked': sum(1 for r in new if r.get('inherited')), **counts,
               'cache': {k: store.counts[k] - before[k] for k in store.counts}, 'refused': refused,
               **({'next': f'an inherited reading counts only once confirmed for {b}\'s own explanation: '
                           f'`confirm {b} RECORD --note "why it holds here"`, or re-read the row'}
                  if any(r.get('inherited') for r in new) else {})}
    B.log('import', **{k: v for k, v in summary.items() if k != 'project'})
    return summary


def confirm(store: Store, name: str, record: str, note: str, declared_by: str = 'llm') -> dict:
    """Confirm an inherited reading in this project's context: the same explanation
    (same id and claim here as where it was read) and a note on why the scope holds.
    A small model's flag is never a confirmation."""
    project = store.project(name)
    recs = project.standing()
    r = next((x for x in recs if x['id'] == record), None)
    if not r or not r.get('inherited'):
        raise ToolError(f'{record} is not an inherited reading of {name}')
    if not note.strip():
        raise ToolError('a confirmation needs a --note: why the reading holds for this question\'s scope')
    hid, claim = r.get('explanation'), _claims(recs).get(r.get('explanation'))
    if claim is None:
        raise ToolError(f'{name} has no explanation {hid}: frame it, or re-read the row for {name}\'s own')
    if claim != r['inherited'].get('claim'):
        raise ToolError(f'{name}\'s {hid} is not the explanation this reading was made for '
                        f'({r["inherited"].get("claim")!r}, here {claim!r}): re-read the row for {name}\'s {hid}')
    rec = _record(type='review', record=record, decision='confirmed', note=note, at=_now(), declared_by=declared_by)
    project.add([rec])
    project.log('confirm', record=record)
    return rec
