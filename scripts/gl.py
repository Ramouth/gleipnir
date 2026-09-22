"""gl: Gleipnir's research tools, for an LLM working on a research task.

  gl.py init    WS "question"
  gl.py frame   WS [FRAME.json]             (before any fetch: questions, rival explanations, their predictions,
                what would discriminate, what to look for; with a file: validate and store it; without:
                show it on one screen for the user to confirm or steer)
  gl.py fetch   WS URL [URL...] [--publisher NAME]   (several URLs: each fetched or refused on its own)
  gl.py original WS SOURCE_ID YYYY[-MM[-DD]] "exact words that state it" --note "why"
                (the date of the document a copy reproduces: an archived copy of an older report)
  gl.py read    WS SOURCE_ID [--find "exact words"] [--around 1500]
  gl.py cut     WS SOURCE_ID "exact anchor words" [--before 200] [--after 600]
  gl.py quote   WS URL "exact words" | WS QUOTES.json   (fetch if needed + find + cut, in one call;
                the file is a list of {"url", "words"})
  gl.py check   WS ATOMS.json [--support]     (a JSON list of atoms; `-` reads stdin; the support
                model is looked up in STORE/models, then raw/models here or above the package)
  gl.py add     WS ATOMS.json
  gl.py origin  WS SOURCE_ID GROUP --basis "why" [--by NAME]   (or WS FILE.json: [{"source","group","basis"}])
                (which reports copy each other: same text, same outlet)
  gl.py rests   WS EVIDENCE "exact words that attribute it" ATOM_UID... --note "why"
                (what the atoms rest on: one study, filing or announcement; atoms of one source per call;
                 declaring again adds evidence; --undo "" takes one back, with --note)
  gl.py relay   WS ACT "exact words" ATOM_UID... --note "why"
                (ACT: verifies, qualifies, disputes, distorts; repeating and endorsing are read from the atom)
  gl.py accountability WS SOURCE_ID CATEGORY --basis "why"   (or WS FILE.json: [{"source","category","basis"}])
                (CATEGORY: peer_reviewed, edited, institutional, interested_party, expert, unedited, aggregator)
  gl.py evidence WS EVIDENCE STATUS PASSAGE_ID "exact words" --note "why" [--kind KIND]
                (STATUS: retracted, corrected, reanalysed, disputed; a dispute takes --kind engages_data
                 or objection)
  gl.py matrix  WS [MATRIX.json]            (questions, competing explanations x evidence; with a file:
                validate and store it, replacing the last; without: show the matrix per question)
  gl.py compare WS "subject words" [--link-same-labels]
  gl.py explain WS "SUBJECT|RELATION" REASON PASSAGE_ID "exact quote" --note "why"
                (REASON: time, definition, speaker, copying, hedge, error, unexplained)
  gl.py pending WS                           (atoms not counted until their review is answered)
  gl.py passage WS PASSAGE_ID                (show a stored passage)
  gl.py review  WS ATOM_UID stated "exact words of the quote" --note "why" | withdrawn
  gl.py links   WS                           (entity pairs that may be the same thing)
  gl.py link    WS ENTITY_A ENTITY_B --basis "why they are the same"   (--undo --note "why" takes one back)
  gl.py status  WS
  gl.py contract                             (the atom contract to write atoms against)

Output is JSON, except `read`, `contract` and the `frame` and `matrix` screens. A
refusal exits with status 2 and says what to do instead; so does a flag the command
does not use.
"""
import argparse
import json
import sys
from pathlib import Path

from gleipnir.workspace import ToolError, Workspace


#: The flags each command uses. Any other is refused, never silently ignored.
USES = {'init': (), 'frame': (), 'fetch': ('publisher',), 'original': ('note', 'by'), 'read': ('find', 'around'),
        'cut': ('before', 'after'), 'quote': ('before', 'after'), 'check': ('support',), 'add': (), 'origin': ('basis', 'by'),
        'rests': ('note', 'by', 'undo'), 'relay': ('note', 'by', 'undo'), 'accountability': ('basis', 'by'),
        'evidence': ('note', 'by', 'kind'), 'matrix': (), 'compare': ('link_same_labels',),
        'explain': ('note', 'by'), 'pending': (), 'passage': (), 'review': ('note', 'by'), 'links': (),
        'link': ('basis', 'by', 'undo', 'note'), 'status': (), 'contract': ()}
DEFAULTS = {'around': 1500, 'before': 200, 'after': 600, 'support': False, 'undo': False, 'basis': '', 'by': 'llm',
            'link_same_labels': False, 'note': ''}


def unused_flags(a) -> str | None:
    """Why the command refuses a flag it was given, or None."""
    given = {k for k, v in vars(a).items() if v is not None and k not in ('command', 'args', 'store')}
    extra = sorted(given - set(USES.get(a.command, given)))
    flag = lambda x: '--' + x.replace('_', '-')
    return (f'{a.command} does not take {", ".join(map(flag, extra))}; it takes '
            f'{", ".join(map(flag, USES[a.command])) or "no flags"} (and --store)') if extra else None


def default_store() -> Path:
    """The raw store: ./raw if there is one, else the nearest raw/ above this
    script (a git worktree inside the repository shares the repository's store)."""
    if Path('raw').is_dir():
        return Path('raw')
    for d in Path(__file__).resolve().parents:
        if (d / 'raw').is_dir():
            return d / 'raw'
    return Path('raw')


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('command')
    ap.add_argument('args', nargs='*')
    ap.add_argument('--publisher'); ap.add_argument('--find'); ap.add_argument('--around', type=int)
    ap.add_argument('--before', type=int); ap.add_argument('--after', type=int)
    ap.add_argument('--support', action='store_true', default=None)
    ap.add_argument('--undo', action='store_true', default=None)
    ap.add_argument('--basis'); ap.add_argument('--by'); ap.add_argument('--kind'); ap.add_argument('--note')
    ap.add_argument('--link-same-labels', action='store_true', default=None)
    ap.add_argument('--store', type=Path, default=None)
    a = ap.parse_args()
    if why := unused_flags(a):
        print(json.dumps({'refused': why}), file=sys.stderr)
        sys.exit(2)
    for k, v in DEFAULTS.items():
        if getattr(a, k) is None:
            setattr(a, k, v)
    if a.command == 'contract':
        from gleipnir.atomiser import PROMPT
        print(PROMPT)
        return
    if not a.args:
        ap.error('a workspace directory is required')
    ws = Workspace(Path(a.args[0]), a.store or default_store())
    rest = a.args[1:]

    def atoms_arg():
        raw = sys.stdin.read() if rest[0] == '-' else Path(rest[0]).read_text()
        data = json.loads(raw)
        return data['atoms'] if isinstance(data, dict) else data

    try:
        if a.command == 'init':
            out = ws.init(rest[0])
        elif a.command == 'frame':
            if not rest:
                print('\n'.join(ws.frame_show()['screen'])); return
            out = ws.frame(json.loads(sys.stdin.read() if rest[0] == '-' else Path(rest[0]).read_text()))
        elif a.command == 'fetch':
            if len(rest) == 1:
                out = ws.fetch(rest[0], a.publisher)
            else:  # several URLs in one call: each fetched or refused on its own
                out = []
                for url in rest:
                    try:
                        out.append(ws.fetch(url, a.publisher))
                    except ToolError as e:
                        out.append({'url': url, 'refused': str(e)})
        elif a.command == 'original':
            out = ws.original(rest[0], rest[1], rest[2], a.note, a.by)
        elif a.command == 'read':
            print(ws.read(rest[0], a.find, a.around)); return
        elif a.command == 'quote':
            if len(rest) == 1 and rest[0].endswith('.json'):
                out = []  # a file of {"url", "words"}: each quoted or refused on its own
                for e in json.loads(Path(rest[0]).read_text()):
                    try:
                        q = ws.quote(e.get('url', ''), e.get('words', ''), a.before, a.after)
                        out.append({k: v for k, v in q.items() if k != 'text'})
                    except ToolError as err:
                        out.append({'url': e.get('url'), 'refused': str(err)})
            else:
                out = ws.quote(rest[0], rest[1], a.before, a.after)
                print(json.dumps({k: v for k, v in out.items() if k != 'text'}, ensure_ascii=False)); print(out['text']); return
        elif a.command == 'cut':
            out = ws.cut(rest[0], rest[1], a.before, a.after)
            print(json.dumps({k: v for k, v in out.items() if k != 'text'}, ensure_ascii=False)); print(out['text']); return
        elif a.command == 'check':
            classifier = None
            if a.support:
                from gleipnir.pretrained import PretrainedNLIBackend, find_directory
                classifier = PretrainedNLIBackend(find_directory(a.store))
            out = ws.check(atoms_arg(), classifier)
        elif a.command == 'add':
            out = ws.add(atoms_arg())
        elif a.command == 'origin' and len(rest) == 1 and rest[0].endswith('.json'):
            out = []  # a file of {"source", "group", "basis"} entries, each declared or refused on its own
            for e in json.loads(Path(rest[0]).read_text()):
                try:
                    out.append(ws.origin(e.get('source', ''), e.get('group', ''), e.get('basis', ''), a.by))
                except ToolError as err:
                    out.append({'source': e.get('source'), 'refused': str(err)})
        elif a.command == 'accountability' and len(rest) == 1 and rest[0].endswith('.json'):
            out = []  # a file of {"source", "category", "basis"} entries, each declared or refused on its own
            for e in json.loads(Path(rest[0]).read_text()):
                try:
                    out.append(ws.accountability(e.get('source', ''), e.get('category', ''), e.get('basis', ''), a.by))
                except ToolError as err:
                    out.append({'source': e.get('source'), 'refused': str(err)})
        elif a.command == 'origin':
            out = ws.origin(rest[0], rest[1], a.basis, a.by)
        elif a.command == 'rests':
            out = ws.rests(rest[2:], rest[0], rest[1], a.note, a.by, a.undo)
        elif a.command == 'relay':
            out = ws.relay(rest[2:], rest[0], rest[1], a.note, a.by, a.undo)
        elif a.command == 'accountability':
            out = ws.accountability(rest[0], rest[1], a.basis, a.by)
        elif a.command == 'evidence':
            out = ws.evidence_status(rest[0], rest[1], rest[2], rest[3], a.note, a.by, a.kind)
        elif a.command == 'matrix':
            if not rest:
                out = ws.matrix_show()                  # one screen: the grid, then one line per item
                print('\n'.join(out.pop('grid')))
                for q in out.pop('questions'):
                    print(f"\n== {q.pop('id')}: {q.pop('text')}")
                    for e in q.pop('explanations'):
                        print(json.dumps(e, ensure_ascii=False))
                    for k, v in q.items():
                        print(f'  {k}: {json.dumps(v, ensure_ascii=False)}')
                print()
                for k, v in out.items():
                    print(f'{k}: {json.dumps(v, ensure_ascii=False)}')
                return
            else:
                out = ws.matrix(json.loads(sys.stdin.read() if rest[0] == '-' else Path(rest[0]).read_text()))
        elif a.command == 'compare':
            out = ws.compare(rest[0], a.link_same_labels)
        elif a.command == 'explain':
            out = ws.explain(rest[0], rest[1], rest[2], rest[3] if len(rest) > 3 else '', a.note, a.by)
        elif a.command == 'review':
            out = ws.review(rest[0], rest[1], rest[2] if len(rest) > 2 else '', a.note, a.by)
        elif a.command == 'pending':
            out = ws.pending()
        elif a.command == 'passage':
            p = ws.passage(rest[0])
            print(json.dumps({'passage': rest[0], 'source': p.source_id, 'source_date': p.source_date,
                              'original_date': ws._original(ws._source(p.source_id))},
                             ensure_ascii=False)); print(ws._wrap(p.text)); return
        elif a.command == 'links':
            out = ws.link_candidates()
        elif a.command == 'link':
            out = ws.link(rest[0], rest[1], a.basis, a.by, a.undo, a.note)
        elif a.command == 'status':
            out = ws.status()
        else:
            ap.error(f'unknown command {a.command}')
    except ToolError as e:
        print(json.dumps({'refused': str(e)}), file=sys.stderr)
        sys.exit(2)
    except (IndexError, FileNotFoundError, json.JSONDecodeError) as e:
        print(json.dumps({'refused': f'bad arguments: {e}'}), file=sys.stderr)
        sys.exit(2)
    print(json.dumps(out, indent=1, ensure_ascii=False))


if __name__ == '__main__':
    main()
