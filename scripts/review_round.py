"""One review round: small-model flags go to the LLM as questions; code checks the answers.

  review_round.py prepare RUN_DIR [--flagged 40 --controls 10]
  review_round.py score   RUN_DIR

prepare: samples flagged atoms and, as hidden controls, unflagged ones, and writes
review_batch.json (what the LLM sees: passage, atom, question; never a label or
score, never which items are controls) and review_key.json (kept from the LLM).

score: reads review_answers.json. An answer never closes anything by itself:
- "stated": the named words must be in the atom's quote (checked in code).
- "rewritten": the new atom goes through atomiser.check() and the small model.
- "withdrawn": the atom is dropped.
Controls show whether the LLM rubber-stamps or rewrites when asked at all.
"""
import argparse
import json
import random
from collections import Counter
from pathlib import Path

from pydantic import ValidationError

from gleipnir.atomiser import Atom, Passage, check
from gleipnir.support import check_support

QUESTION = ('Does the quote itself state this? If it does, name the words that state it. '
            'If it does not, rewrite the atom so that it says only what the quote says, or '
            'quote the further words it rests on.')


def load(run: Path):
    passages = {p['id']: Passage.model_validate({k: p[k] for k in ('id', 'source_id', 'source_date', 'text')})
                for p in json.loads((run / 'inputs.json').read_text())}
    atoms = {}
    for pid in passages:
        path = run / 'outputs' / f'{pid}.json'
        if path.exists():
            for i, raw in enumerate(json.loads(path.read_text()).get('atoms', [])):
                try:
                    atoms[f'{pid}:{i}'] = Atom.model_validate(raw)
                except ValidationError:
                    pass
    return passages, atoms


def prepare(run: Path, flagged_n: int, controls_n: int):
    from gleipnir.pretrained import PretrainedNLIBackend
    classifier = PretrainedNLIBackend()
    passages, atoms = load(run)
    flagged, clean = [], []
    for uid, atom in atoms.items():
        if check(atom, passages[atom.passage_id])['report']['defects']:
            continue
        try:
            s = check_support(atom, classifier)
        except ValueError:
            continue
        (flagged if s['flag'] else clean).append(uid)
    rng = random.Random(22)
    chosen = [(u, 'flagged') for u in rng.sample(flagged, min(flagged_n, len(flagged)))] + \
             [(u, 'control') for u in rng.sample(clean, min(controls_n, len(clean)))]
    rng.shuffle(chosen)
    batch, key = [], {}
    for n, (uid, kind) in enumerate(chosen):
        rid = f'r{n:03}'
        atom = atoms[uid]
        batch.append({'id': rid, 'passage': passages[atom.passage_id].text,
                      'atom': atom.model_dump(mode='json'), 'question': QUESTION})
        key[rid] = {'atom': uid, 'kind': kind}
    (run / 'review_batch.json').write_text(json.dumps(batch, indent=1, ensure_ascii=False))
    (run / 'review_key.json').write_text(json.dumps(key, indent=1))
    print(f'{len(flagged)} flagged, {len(clean)} unflagged; batch {len(batch)} '
          f'({sum(k == "flagged" for _, k in chosen)} flagged + {sum(k == "control" for _, k in chosen)} controls)')


def score(run: Path):
    from gleipnir.pretrained import PretrainedNLIBackend
    classifier = PretrainedNLIBackend()
    passages, atoms = load(run)
    key = json.loads((run / 'review_key.json').read_text())
    answers = {a['id']: a for a in json.loads((run / 'review_answers.json').read_text())}
    out = Counter()
    for rid, k in key.items():
        kind, atom = k['kind'], atoms[k['atom']]
        a = answers.get(rid)
        if not a:
            out[(kind, 'no answer')] += 1
            continue
        decision = a.get('decision')
        if decision == 'stated':
            words = ' '.join(str(a.get('words', '')).split()).casefold()
            ok = bool(words) and words in ' '.join(atom.quote.split()).casefold()
            out[(kind, 'stated, words in quote' if ok else 'stated, words NOT in quote')] += 1
        elif decision == 'rewritten':
            try:
                new = Atom.model_validate(a['atom'])
            except (ValidationError, KeyError, TypeError):
                out[(kind, 'rewritten, invalid atom')] += 1
                continue
            v = check(new, passages[atom.passage_id])
            if v['defects']:
                out[(kind, 'rewritten, fails code check')] += 1
                continue
            try:
                s = check_support(new, classifier)
            except ValueError:
                out[(kind, 'rewritten, too long for classifier')] += 1
                continue
            out[(kind, 'rewritten, passes checks' + (' but still flagged' if s['flag'] else ' and unflagged'))] += 1
        elif decision == 'withdrawn':
            out[(kind, 'withdrawn')] += 1
        else:
            out[(kind, f'unknown decision {decision!r}')] += 1
    for kind in ('flagged', 'control'):
        total = sum(v for (k, _), v in out.items() if k == kind)
        print(f'{kind} ({total}):')
        for (k, outcome), v in sorted(out.items(), key=lambda x: -x[1]):
            if k == kind:
                print(f'  {v:3}  {outcome}')
    (run / 'review_score.json').write_text(json.dumps({f'{k}|{o}': v for (k, o), v in out.items()}, indent=1))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('command', choices=['prepare', 'score'])
    ap.add_argument('run', type=Path)
    ap.add_argument('--flagged', type=int, default=40)
    ap.add_argument('--controls', type=int, default=10)
    args = ap.parse_args()
    prepare(args.run, args.flagged, args.controls) if args.command == 'prepare' else score(args.run)


if __name__ == '__main__':
    main()
