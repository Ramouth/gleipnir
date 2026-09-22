"""Evaluate a configured semantic assessor on synthetic quotation-integrity cases.

No model accuracy is inferred from stubs. Zero budget replays available cache
entries and reports unassessed cases separately, never as correct rejections.
"""
import argparse
import hashlib
import json
import time
from datetime import date
from pathlib import Path

from gleipnir.alignment import assess, gate
from gleipnir.oracle import AlignmentOracle
from gleipnir.rawstore import RawStore
from gleipnir.research import ResearchResult


def make_case(case, store):
    context = case['context']
    content_key = hashlib.sha256(context.encode()).hexdigest()
    url = f'fixture://quotation-integrity/{content_key}'
    rec = store.latest('alignment_fixture', 'text', url)
    if rec is None:
        rec = store.put(payload=context.encode(), source='alignment_fixture', resource_type='text',
                        resource_id=url, http_status=200, request_params={'synthetic': True})
    quote = case.get('quote', context)
    start = context.index(quote)
    return ResearchResult.model_validate(dict(
        id=case['id'], question='Does the contextualized source support this atom?',
        as_of=date.today(), method='Synthetic development evaluation; expected labels withheld from assessor.',
        sources=[dict(id='s', title='Synthetic source', url=url, raw_ref=rec.content_hash,
                      media_type='text/plain', origin_group='synthetic', scope='development case')],
        questions=[dict(id='q', text='Does the source support this atom?')],
        passages=[dict(id='p', source_id='s', start=start, end=start+len(quote), quote=quote)],
        evidence=[dict(id='a', question_id='q', passage_ids=['p'], statement=case['statement'],
                       qualification='Preserve the conditions and uncertainty expressed in the source.',
                       proposed_by='development fixture')],
        assessments=[], synthesis=dict(text=case['statement'], evidence_ids=['a'], author='fixture'),
        unresolved=[], limitations=['Synthetic fixture, not scientific evidence.']
    ))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backend', choices=['pretrained', 'llm'], default='pretrained')
    parser.add_argument('--model', help='Explicit model ID for the LLM backend')
    parser.add_argument('--model-directory', type=Path, default=Path('raw/models/nli-MiniLM2-L6-H768'))
    parser.add_argument('--max-calls', type=int, default=0)
    parser.add_argument('--context-margin', type=int, help='Context characters per side; 600 pretrained, 1200 LLM')
    parser.add_argument('--threshold', type=float, default=0.8)
    parser.add_argument('--cases', type=Path, default=Path(__file__).resolve().parents[1] / 'tests/fixtures/alignment_cases.json')
    parser.add_argument('--store', type=Path, default=Path('raw/alignment-evaluation'))
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.max_calls < 0:
        parser.error('max-calls must be nonnegative')
    if args.backend == 'llm' and not args.model:
        parser.error('--model is required with --backend llm')
    if args.backend == 'pretrained' and args.model:
        parser.error('--model applies only to --backend llm')
    dataset = json.loads(args.cases.read_text())
    store = RawStore(args.store)
    client = None
    if args.backend == 'llm':
        if args.max_calls:
            import anthropic
            client = anthropic.Anthropic(max_retries=0, timeout=45.0)
        oracle = AlignmentOracle(client.messages if client else None, model=args.model)
    else:
        from gleipnir.pretrained import PretrainedNLIBackend
        oracle = PretrainedNLIBackend(args.model_directory)

    class CountedBackend:
        identity = oracle.identity
        calls = 0
        inference_seconds = 0.0

        def assess(self, pair):
            self.calls += 1
            started = time.perf_counter()
            try:
                return oracle.assess(pair)
            finally:
                self.inference_seconds += time.perf_counter() - started

    backend = CountedBackend()
    rows = []
    try:
        for case in dataset['cases']:
            result = make_case(case, store)
            report = assess(result, store, backend,
                            max_calls=max(0, args.max_calls-backend.calls), threshold=args.threshold,
                            context_margin=(args.context_margin if args.context_margin is not None
                                            else 600 if args.backend == 'pretrained' else 1200))
            completed = not report.pending and not report.errors
            gate_eligible = gate(result, store, report)['synthesis_evidence_eligible'] if completed else None
            semantics_assessed = completed and bool(report.records) and all(
                r.judgment.atomic is not None and r.judgment.truth_evaluable is not None
                and r.judgment.context_sufficient is not None for r in report.records)
            eligible = gate_eligible if semantics_assessed else None
            support = (all(r.judgment.relation == 'supports' and
                           r.judgment.support_score >= args.threshold for r in report.records)
                       if completed and report.records else None)
            rows.append(dict(id=case['id'], category=case['category'],
                             expected_support=case.get('expected_support'), support=support,
                             expected_eligible=case['expected_eligible'], eligible=eligible,
                             gate_eligible=gate_eligible, source_pair_assessed=completed,
                             assessment=report.model_dump(mode='json')))
    finally:
        if client:
            client.close()
    bad = [r for r in rows if not r['expected_eligible'] and r['eligible'] is not None]
    good = [r for r in rows if r['expected_eligible'] and r['eligible'] is not None]
    false_accepts = sum(r['eligible'] for r in bad)
    false_rejects = sum(not r['eligible'] for r in good)
    metrics = dict(total=len(rows), assessed=len(bad)+len(good), unassessed=len(rows)-len(bad)-len(good),
                   calls=backend.calls, false_accepts=false_accepts, false_rejects=false_rejects,
                   false_accept_rate=false_accepts/len(bad) if bad else None,
                   false_reject_rate=false_rejects/len(good) if good else None)
    metrics['source_pairs_assessed'] = sum(r['source_pair_assessed'] for r in rows)
    metrics['inference_seconds_including_load'] = backend.inference_seconds
    support_bad = [r for r in rows if r['expected_support'] is False and r['support'] is not None]
    support_good = [r for r in rows if r['expected_support'] is True and r['support'] is not None]
    support_metrics = dict(
        assessed=len(support_bad)+len(support_good),
        false_accepts=sum(r['support'] for r in support_bad),
        false_rejects=sum(not r['support'] for r in support_good),
        negative_cases=len(support_bad), positive_cases=len(support_good),
        excluded_or_unassessed=len(rows)-len(support_bad)-len(support_good))
    output = dict(dataset_version=dataset['version'], annotation=dataset['annotation'],
                  support_metrics=support_metrics,
                  backend=backend.identity, threshold=args.threshold, metrics=metrics, cases=rows)
    args.output.write_text(json.dumps(output, indent=2)+'\n')
    print(json.dumps({'gate_metrics': metrics, 'support_metrics': support_metrics}, indent=2))
    return 1 if args.max_calls and not metrics['source_pairs_assessed'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
