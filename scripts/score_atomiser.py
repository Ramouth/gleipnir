"""Score atomiser output against the contract and, for red-team cases, expectations.

Usage: score_atomiser.py RUN_DIR [OUTPUT_SUBDIR]
Reads RUN_DIR/<OUTPUT_SUBDIR, default outputs>/<passage_id>.json ({"atoms": [...]}) and the case files.
The headline red-team number is "wrongly closed": atoms code calls closed in a
case whose expectations they break. That is the failure the contract exists to stop.
"""
import json
import sys
from collections import Counter
from pathlib import Path

from pydantic import ValidationError

from gleipnir.atomiser import LOCAL, Atom, Passage, check, trace


OUT = 'outputs'


def load_output(run: Path, pid: str):
    path = run / OUT / f'{pid}.json'
    if not path.exists():
        return None, None
    raw = path.read_text()
    return raw, json.loads(raw).get('atoms', [])


def judge(passage: Passage, raw_atoms):
    results = []
    for raw in raw_atoms:
        try:
            atom = Atom.model_validate(raw)
        except ValidationError as e:
            results.append((None, {'closed': False, 'defects': ['schema'], 'open': [],
                                   'error': str(e).splitlines()[0]}))
            continue
        results.append((atom, check(atom, passage)))
    return results


def expectation_failures(expect: dict, raw: str, judged) -> list[str]:
    fails = []
    atoms_full = [a for a, _ in judged if a]
    atoms = [a.chain()[1] for a in atoms_full]
    chains = [a.chain()[0] for a, _ in judged if a]
    closed = [a for a, v in judged if a and v['closed']]
    # Fabrication matters in the structured fields that enter the graph: ids,
    # times and the relation. Prose may legitimately name both readings of an
    # ambiguous date, and local ids embed the source id by design.
    def structured(a):
        _, c = a.chain()
        ids = [x.id for x in (c.subject, c.object) if x is not None and x.id]
        ids = [LOCAL.match(i)['slug'] if LOCAL.match(i) else i for i in ids]
        inner = [r.speaker.id for r in a.chain()[0][1:] if r.speaker.id]
        return [ids, inner, c.holds.model_dump(), c.predicate, c.value]
    unquoted = json.dumps([structured(a) for a in atoms_full])
    for s in expect.get('forbidden_in_output') or []:
        if s in unquoted:
            fails.append(f'forbidden "{s}"')
    if expect.get('polarity') and atoms and not any(a.polarity == expect['polarity'] for a in atoms):
        fails.append(f'polarity not {expect["polarity"]}')
    speakers = [s.casefold() for s in expect.get('speakers') or []]
    if speakers and not any(all(any(s in r.speaker.label.casefold() for r in chain) for s in speakers)
                            for chain in chains):
        fails.append(f'speakers {speakers}')
    for sid, text in expect.get('forbid_pairs') or []:
        if any(a.subject.id == sid and text.casefold() in a.statement.casefold() for a in atoms):
            fails.append(f'{sid} bound to "{text}"')
    return fails


def main(run: Path):
    report = {'redteam': [], 'real': {}}
    for name in ('redteam_mine.json', 'redteam_agent.json'):
        if not (run / name).exists():
            continue
        for case in json.loads((run / name).read_text()):
            passage = Passage.model_validate(case['passage'])
            raw, atoms = load_output(run, passage.id)
            if raw is None:
                report['redteam'].append({'id': case['id'], 'missing': True})
                continue
            judged = judge(passage, atoms)
            fails = expectation_failures(case['expect'], raw, judged)
            report['redteam'].append({
                'id': case['id'], 'set': name, 'attack': case['attack'], 'target': case.get('target'),
                'atoms': len(judged), 'closed': sum(v['closed'] for _, v in judged),
                'expectation_failures': fails,
                'wrongly_closed': bool(fails) and any(v['closed'] for _, v in judged),
                'defects': sorted({d for _, v in judged for d in v['defects']})})
    real = json.loads((run / 'real_inputs.json').read_text()) if (run / 'real_inputs.json').exists() else []
    status, defects, opens, levels, first = Counter(), Counter(), Counter(), Counter(), Counter()
    for item in real:
        passage = Passage.model_validate({k: v for k, v in item.items() if not k.startswith('_')})
        raw, atoms = load_output(run, passage.id)
        if raw is None:
            status['missing'] += 1
            continue
        for _, v in judge(passage, atoms):
            status['closed' if v['closed'] else 'defect' if v['defects'] else 'open'] += 1
            if 'report' in v:
                levels[f"report_closed={v['report']['closed']} claim_closed={v['claim']['closed']}"] += 1
            defects.update(v['defects'])
            opens.update(v['open'])
            t = trace(v)
            if t['first_failure']:
                first[f"{t['first_failure']}:{t['steps'][t['first_failure']]['status']}"] += 1
    report['real'] = {'atoms': dict(status), 'first_failure': dict(first), 'levels': dict(levels), 'defects': dict(defects),
                      'open_reasons': dict(opens)}
    (run / f'score_{OUT}.json').write_text(json.dumps(report, indent=1))
    rt = [r for r in report['redteam'] if not r.get('missing')]
    print(f"red-team cases: {len(rt)}  passed: {sum(not r['expectation_failures'] for r in rt)}  "
          f"wrongly closed: {sum(r['wrongly_closed'] for r in rt)}")
    for r in rt:
        if r['expectation_failures'] or r['defects']:
            print(f"  {r['id']:6} {'WRONGLY CLOSED ' if r['wrongly_closed'] else ''}"
                  f"{r['expectation_failures']} defects={r['defects']}  ({r['attack']})")
    print('real atoms:', report['real'])


if __name__ == '__main__':
    if len(sys.argv) > 2:
        OUT = sys.argv[2]
    main(Path(sys.argv[1]))
