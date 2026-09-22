"""Orchestration/gate tests use stubs, not evidence of semantic model accuracy."""
import json
from types import SimpleNamespace

import pytest

from gleipnir.alignment import (
    AlignScoreBackend, Judgment, assess, gate, pairs_for, request_key,
)
from gleipnir.oracle import AlignmentOracle
from gleipnir.rawstore import RawStore
from gleipnir.research import ResearchResult


@pytest.fixture
def case(tmp_path):
    context = ('The authors tested one energy range. No significant excess was observed. '
               'Other ranges remain untested.')
    store = RawStore(tmp_path / 'raw')
    rec = store.put(payload=context.encode(), source='test', resource_type='text',
                    resource_id='https://example.org/study', http_status=200, request_params={})
    quote = 'No significant excess was observed.'
    start = context.index(quote)
    result = ResearchResult.model_validate(dict(
        id='test', question='Was an excess observed?', as_of='2026-09-21', method='test',
        sources=[dict(id='s', title='Study', url='https://example.org/study',
                      raw_ref=rec.content_hash, media_type='text/plain', origin_group='team', scope='study')],
        questions=[dict(id='q', text='Was an excess observed?')],
        passages=[dict(id='p', source_id='s', start=start, end=start+len(quote), quote=quote)],
        evidence=[dict(id='a', question_id='q', passage_ids=['p'],
                       statement='The authors report no significant excess in the tested energy range.',
                       qualification='Other energy ranges were not tested.', proposed_by='generator')],
        assessments=[], synthesis=dict(text='No excess in the tested range.', evidence_ids=['a'], author='generator'),
        unresolved=[], limitations=[]))
    return result, store


def judgment(**changes):
    data = dict(relation='supports', support_score=0.95, atomic=True,
                truth_evaluable=True, context_sufficient=True, issues=(),
                rationale='The source reports this for the tested range.',
                supporting_quote='No significant excess was observed.')
    return Judgment(**(data | changes))


class Stub:
    identity = 'stub/version-1'

    def __init__(self, answer=None):
        self.answer = answer or judgment()
        self.calls = []

    def assess(self, pair):
        self.calls.append(pair)
        return self.answer


def test_context_is_retrieved_not_supplied_by_generator(case):
    result, store = case
    backend = Stub()
    report = assess(result, store, backend, max_calls=1)
    pair = backend.calls[0]
    assert pair.context.startswith('The authors tested one energy range.')
    assert pair.context.endswith('Other ranges remain untested.')
    assert 'generator' not in pair.model_dump_json()
    assert gate(result, store, report)['eligible_atoms'] == ['a']
    assert gate(result, store, report)['semantic_truth_verified'] is False


@pytest.mark.parametrize('changes', [
    dict(relation='contradicts', issues=('negation',)),
    dict(relation='insufficient', issues=('uncertainty',)),
    dict(issues=('scope',)), dict(issues=('attribution',)), dict(issues=('time',)),
    dict(issues=('quantity',)), dict(context_sufficient=False),
    dict(atomic=False), dict(truth_evaluable=False), dict(support_score=0.3),
    dict(atomic=None),
])
def test_high_score_never_overrides_failed_semantics(case, changes):
    result, store = case
    report = assess(result, store, Stub(judgment(**changes)), max_calls=1)
    check = gate(result, store, report)
    assert check['blocked_atoms'] == ['a']
    assert check['synthesis_evidence_eligible'] is False


def test_missing_budget_blocks_and_completed_work_replays(case):
    result, store = case
    backend = Stub()
    report = assess(result, store, backend)
    assert not backend.calls and report.pending
    assert not gate(result, store, report)['synthesis_evidence_eligible']
    assess(result, store, backend, max_calls=1)
    replay = assess(result, store, backend)
    assert len(backend.calls) == 1
    assert not replay.pending
    assert gate(result, store, replay)['synthesis_evidence_eligible']


def test_cache_binds_model_and_candidate_and_qualifications(case):
    result, store = case
    backend = Stub()
    old = assess(result, store, backend, max_calls=1)
    data = result.model_dump()
    data['evidence'][0]['statement'] = 'An excess was discovered.'
    changed = ResearchResult.model_validate(data)
    with pytest.raises(ValueError, match='stale'):
        gate(changed, store, old)
    assert assess(changed, store, backend).pending
    pair = pairs_for(result, store)[0]
    assert request_key(pair, 'model-1') != request_key(pair, 'model-2')
    changed_pair = pair.model_copy(update={'qualification': 'All ranges.'})
    assert request_key(pair, 'model-1') != request_key(changed_pair, 'model-1')


def test_fabricated_judge_quote_is_not_cached_or_admitted(case):
    result, store = case
    report = assess(result, store, Stub(judgment(supporting_quote='Fabricated quote')), max_calls=1)
    assert report.errors and report.pending and not report.records
    assert len(store.fetches()) == 1


def test_invalid_backend_output_and_failed_calls_remain_pending(case):
    result, store = case
    backend = Stub({'relation': 'supports'})
    report = assess(result, store, backend, max_calls=1)
    assert report.errors and report.pending
    assert len(backend.calls) == 1
    assert not gate(result, store, report)['synthesis_evidence_eligible']


def test_report_cannot_change_stored_judgment(case):
    result, store = case
    report = assess(result, store, Stub(judgment(relation='contradicts', issues=('negation',))), max_calls=1)
    data = report.model_dump()
    data['records'][0]['judgment'] = judgment().model_dump()
    forged = type(report).model_validate(data)
    with pytest.raises(ValueError, match='differs from stored'):
        gate(result, store, forged)


def test_alignscore_scalar_is_diagnostic_not_semantic_certification(case):
    result, store = case
    class Scorer:
        def score(self, *, contexts, claims):
            assert 'Other ranges remain untested.' in contexts[0]
            assert claims == [result.evidence[0].statement]
            return [0.99]
    backend = AlignScoreBackend(Scorer(), identity='checkpoint-sha/test')
    report = assess(result, store, backend, max_calls=1)
    assert report.records[0].judgment.support_score == 0.99
    assert not gate(result, store, report)['synthesis_evidence_eligible']


def test_oracle_sees_only_source_and_atom_in_closed_schema(case):
    result, store = case
    calls = []
    class Client:
        def create(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(content=[SimpleNamespace(type='text', text=judgment().model_dump_json())])
    report = assess(result, store, AlignmentOracle(Client(), model='test-model'), max_calls=1)
    payload = json.loads(calls[0]['messages'][0]['content'])
    assert 'context' in payload and 'statement' in payload
    assert 'rationale' not in payload and 'synthesis' not in payload
    assert calls[0]['output_config']['format']['schema']['additionalProperties'] is False
    assert report.records[0].backend.startswith('llm:test-model:')


def test_unaligned_export_requires_explicit_draft(case, tmp_path):
    import subprocess
    import sys
    from pathlib import Path
    result, store = case
    manifest = tmp_path / 'manifest.json'
    manifest.write_text(result.model_dump_json())
    output = tmp_path / 'report.md'
    command = [sys.executable, str(Path(__file__).resolve().parents[1] / 'scripts/research.py'),
               str(manifest), '--store', str(store.root), '--markdown', str(output)]
    run = subprocess.run(command, capture_output=True, text=True)
    assert run.returncode == 1 and not output.exists()
    assert 'unchecked or blocked' in run.stderr
    run = subprocess.run(command + ['--draft'], capture_output=True, text=True)
    assert run.returncode == 0, run.stderr
    assert 'DRAFT' in output.read_text()


def test_schema_constraints_are_enforced_locally(case):
    from gleipnir.oracle import alignment_schema
    from pydantic import ValidationError
    schema = alignment_schema()
    assert 'maximum' not in schema['properties']['support_score']
    assert 'maxLength' not in schema['properties']['rationale']
    for score in (1.5, -1.0, float('nan')):
        with pytest.raises(ValidationError):
            judgment(support_score=score)


def test_budget_and_resume_with_multiple_atoms(case):
    result, store = case
    data = result.model_dump()
    data['evidence'] = (*data['evidence'], dict(data['evidence'][0], id='a2'))
    data['synthesis']['evidence_ids'] = ['a', 'a2']
    result = ResearchResult.model_validate(data)
    backend = Stub()
    partial = assess(result, store, backend, max_calls=1)
    assert len(backend.calls) == 1 and len(partial.records) == 1 and len(partial.pending) == 1
    assert not gate(result, store, partial)['synthesis_evidence_eligible']
    backend.answer = judgment(relation='contradicts', issues=('negation',))
    complete = assess(result, store, backend, max_calls=1)
    assert len(backend.calls) == 2 and not complete.pending
    assert gate(result, store, complete)['eligible_atoms'] == ['a']
    assert gate(result, store, complete)['blocked_atoms'] == ['a2']


def test_evaluation_does_not_count_unassessed_as_correct_rejections(tmp_path):
    import subprocess
    import sys
    from pathlib import Path
    output = tmp_path / 'evaluation.json'
    script = Path(__file__).resolve().parents[1] / 'scripts/evaluate_alignment.py'
    run = subprocess.run([sys.executable, str(script), '--backend', 'llm', '--model', 'unconfigured',
                          '--max-calls', '0', '--store', str(tmp_path / 'store'),
                          '--output', str(output)], capture_output=True, text=True)
    assert run.returncode == 0, run.stderr
    metrics = json.loads(output.read_text())['metrics']
    assert metrics['total'] == 16 and metrics['unassessed'] == 16
    assert metrics['assessed'] == 0 and metrics['calls'] == 0
    assert metrics['false_accept_rate'] is None and metrics['false_reject_rate'] is None


def test_explicit_context_window_is_recorded_and_replay_bound(case):
    result, store = case
    backend = Stub()
    report = assess(result, store, backend, max_calls=1, context_margin=0)
    assert report.context_margin == 0
    assert report.records[0].input.context == result.passages[0].quote
    assert report.records[0].input.clipped_left and report.records[0].input.clipped_right
    assert gate(result, store, report)['eligible_atoms'] == ['a']
    with pytest.raises(ValueError, match='stale'):
        gate(result, store, report.model_copy(update={'context_margin': 1200}))
    with pytest.raises(ValueError):
        assess(result, store, backend, max_calls=1, context_margin=-1)
    assert len(backend.calls) == 1


def test_support_metrics_do_not_require_atomicity_assessment(tmp_path, monkeypatch):
    import importlib.util
    import sys
    from pathlib import Path
    script = Path(__file__).resolve().parents[1] / 'scripts/evaluate_alignment.py'
    spec = importlib.util.spec_from_file_location('evaluate_alignment_test', script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    class SupportOnly:
        identity = 'test-pretrained'
        def __init__(self, directory):
            pass
        def assess(self, pair):
            return judgment(atomic=None, truth_evaluable=None, context_sufficient=None,
                            supporting_quote=pair.context)
    monkeypatch.setattr('gleipnir.pretrained.PretrainedNLIBackend', SupportOnly)
    output = tmp_path / 'evaluation.json'
    monkeypatch.setattr(sys, 'argv', [str(script), '--max-calls', '16', '--output', str(output),
                                    '--store', str(tmp_path / 'store')])
    assert module.main() == 0
    data = json.loads(output.read_text())
    assert data['metrics']['source_pairs_assessed'] == 16
    assert data['metrics']['assessed'] == 0
    assert data['support_metrics']['assessed'] == 15
    assert data['support_metrics']['false_accepts'] == 8  # always-support stub
    assert data['support_metrics']['false_rejects'] == 0
