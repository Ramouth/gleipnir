"""gl: Gleipnir's research tools, for an LLM working on a research task.

  gl.py init    WS "question"
  gl.py fetch   WS URL [--publisher NAME]
  gl.py read    WS SOURCE_ID [--find "exact words"] [--around 1500]
  gl.py cut     WS SOURCE_ID "exact anchor words" [--before 200] [--after 600]
  gl.py check   WS ATOMS.json [--support]     (a JSON list of atoms; `-` reads stdin)
  gl.py add     WS ATOMS.json
  gl.py origin  WS SOURCE_ID GROUP --basis "why" [--by NAME]
  gl.py compare WS "subject words" [--link-same-labels]
  gl.py explain WS "SUBJECT|RELATION" REASON PASSAGE_ID "exact quote" --note "why"
                (REASON: time, definition, speaker, copying, hedge, error, unexplained)
  gl.py review  WS ATOM_UID stated "exact words of the quote" --note "why" | withdrawn
  gl.py links   WS                           (entity pairs that may be the same thing)
  gl.py link    WS ENTITY_A ENTITY_B --basis "why they are the same"
  gl.py status  WS
  gl.py contract                             (the atom contract to write atoms against)

Output is JSON, except `read` and `contract`. A refusal exits with status 2 and
says what to do instead.
"""
import argparse
import json
import sys
from pathlib import Path

from gleipnir.workspace import ToolError, Workspace


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('command')
    ap.add_argument('args', nargs='*')
    ap.add_argument('--publisher'); ap.add_argument('--find'); ap.add_argument('--around', type=int, default=1500)
    ap.add_argument('--before', type=int, default=200); ap.add_argument('--after', type=int, default=600)
    ap.add_argument('--support', action='store_true'); ap.add_argument('--basis', default='')
    ap.add_argument('--by', default='llm'); ap.add_argument('--link-same-labels', action='store_true'); ap.add_argument('--note', default=''); ap.add_argument('--store', type=Path, default=Path('raw'))
    a = ap.parse_args()
    if a.command == 'contract':
        from gleipnir.atomiser import PROMPT
        print(PROMPT)
        return
    if not a.args:
        ap.error('a workspace directory is required')
    ws = Workspace(Path(a.args[0]), a.store)
    rest = a.args[1:]

    def atoms_arg():
        raw = sys.stdin.read() if rest[0] == '-' else Path(rest[0]).read_text()
        data = json.loads(raw)
        return data['atoms'] if isinstance(data, dict) else data

    try:
        if a.command == 'init':
            out = ws.init(rest[0])
        elif a.command == 'fetch':
            out = ws.fetch(rest[0], a.publisher)
        elif a.command == 'read':
            print(ws.read(rest[0], a.find, a.around)); return
        elif a.command == 'cut':
            out = ws.cut(rest[0], rest[1], a.before, a.after)
            print(json.dumps({k: v for k, v in out.items() if k != 'text'}, ensure_ascii=False)); print(out['text']); return
        elif a.command == 'check':
            classifier = None
            if a.support:
                from gleipnir.pretrained import PretrainedNLIBackend
                classifier = PretrainedNLIBackend()
            out = ws.check(atoms_arg(), classifier)
        elif a.command == 'add':
            out = ws.add(atoms_arg())
        elif a.command == 'origin':
            out = ws.origin(rest[0], rest[1], a.basis, a.by)
        elif a.command == 'compare':
            out = ws.compare(rest[0], a.link_same_labels)
        elif a.command == 'explain':
            out = ws.explain(rest[0], rest[1], rest[2], rest[3] if len(rest) > 3 else '', a.note, a.by)
        elif a.command == 'review':
            out = ws.review(rest[0], rest[1], rest[2] if len(rest) > 2 else '', a.note, a.by)
        elif a.command == 'links':
            out = ws.link_candidates()
        elif a.command == 'link':
            out = ws.link(rest[0], rest[1], a.basis, a.by)
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
