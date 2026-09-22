"""Build the claim graph from atomiser output and report what matched.

Usage: build_graph.py RUN_DIR [--db graph.sqlite]
Reads RUN_DIR/inputs.json (passages) and RUN_DIR/outputs/<passage id>.json.
"""
import argparse
import json
from collections import Counter
from pathlib import Path

from pydantic import ValidationError

from gleipnir.atomiser import Atom, Passage, check, trace
from gleipnir.graph import Graph
from gleipnir.support import check_support, review_request


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run', type=Path)
    parser.add_argument('--db', default=':memory:')
    parser.add_argument('--outputs', default='outputs')
    parser.add_argument('--support', action='store_true', help='run the small NLI check and write review_requests.json')
    args = parser.parse_args()
    graph = Graph(args.db)
    added = Counter()
    classifier, requests = None, []
    if args.support:
        from gleipnir.pretrained import PretrainedNLIBackend
        classifier = PretrainedNLIBackend()
    for item in json.loads((args.run / 'inputs.json').read_text()):
        passage = Passage.model_validate({k: item[k] for k in ('id', 'source_id', 'source_date', 'text')})
        path = args.run / args.outputs / f'{passage.id}.json'
        if not path.exists():
            added['missing_output'] += 1
            continue
        for i, raw in enumerate(json.loads(path.read_text()).get('atoms', [])):
            try:
                atom = Atom.model_validate(raw)
            except ValidationError:
                added['schema_error'] += 1
                continue
            verdict = check(atom, passage)
            added['in_graph' if graph.add(atom, passage, verdict, f'{passage.id}:{i}') else 'report_defect'] += 1
            support = None
            if classifier:
                try:
                    support = check_support(atom, classifier)
                except ValueError:
                    added['support_skipped'] += 1
                request = support and review_request(atom, support)
                if request:
                    requests.append({'atom': f'{passage.id}:{i}', **request})
            added[f"first_failure={trace(verdict, support)['first_failure']}"] += 1
    if classifier:
        (args.run / 'review_requests.json').write_text(json.dumps(requests, indent=1))
        print(f'review requests for the LLM: {len(requests)}')
    proposals = graph.propose_label_aliases()
    graph.db.commit()
    claims = graph.claims()
    predicates = Counter(c['predicate'] for c in claims if c['predicate'])
    print('atoms:', dict(added))
    print('label-alias proposals:', proposals)
    print('strict (no merges):   ', graph.stats())
    print('accepting same-label: ', graph.stats(accept={'same-label'}))
    print(f'predicates: {len(predicates)} distinct over {sum(predicates.values())} claims; '
          f'used more than once: {sum(1 for n in predicates.values() if n > 1)}')
    print('  most common:', predicates.most_common(8))
    for key, group in graph.claim_groups(accept={'same-label'}).items():
        if len({c['source'] for c in group}) > 1:
            pol = {c['polarity'] for c in group}
            print(f"\n{'CONTRADICTION ' if len(pol) > 1 else ''}{len(group)} claims, "
                  f"{len({c['source'] for c in group})} sources:")
            for c in group:
                print(f"  {c['source']:22} {c['polarity']:8} {c['statement'][:110]}")


if __name__ == '__main__':
    main()
