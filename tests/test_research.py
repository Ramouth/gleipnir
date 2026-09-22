import json

import pytest
from pydantic import ValidationError

from gleipnir.rawstore import RawStore
from gleipnir.research import ResearchResult, render, save, source_text, validate


@pytest.fixture
def case(tmp_path):
    store = RawStore(tmp_path)
    payload = b'<html><script>discovered</script><p>No significant excess.</p></html>'
    rec = store.put(payload=payload, source='experiment', resource_type='web_page',
                    resource_id='https://example.org/paper', http_status=200, request_params={})
    data = dict(id='dimensions', question='Does another spatial dimension exist?',
                as_of='2026-09-21', method='Curated research test',
                sources=[dict(id='s1', title='Experiment', url='https://example.org/paper',
                              raw_ref=rec.content_hash, media_type='text/html',
                              origin_group='experiment-team', scope='abstract only')],
                questions=[dict(id='q1', text='Did the experiment report an excess?')],
                passages=[dict(id='p1', source_id='s1', start=0, end=22, quote='No significant excess.')],
                evidence=[dict(id='e1', question_id='q1', passage_ids=['p1'],
                               statement='The experiment reports no significant excess.',
                               qualification='Limited to its sensitivity.', proposed_by='test-model')],
                assessments=[dict(id='a1', evidence_id='e1', relation='supports',
                                  rationale='The passage directly states the result.',
                                  author='test-model', author_kind='model')],
                synthesis=dict(text='Physical existence remains unresolved.', evidence_ids=['e1'], author='test-model'),
                unresolved=['Other parameter regimes remain untested.'], limitations=['Not an exhaustive review.'])
    return store, data


def test_roundtrip_and_immutable_versions(case):
    store, data = case
    result = ResearchResult.model_validate(data)
    checks = validate(result, store)
    assert checks['passages_verified'] == 1
    assert checks['semantic_truth_verified'] is False
    first = save(result, store)
    data['synthesis']['text'] = 'A revised interpretation, still unresolved.'
    second = save(ResearchResult.model_validate(data), store)
    assert first != second
    assert ResearchResult.model_validate_json(store.get(first)) == result
    assert len(store.fetches()) == 3
    assert 'https://example.org/paper' in render(result)


@pytest.mark.parametrize('change,match', [
    (lambda d: d['passages'][0].update(quote='A discovery was made.'), 'does not match'),
    (lambda d: d['passages'][0].update(end=999), 'bounds'),
    (lambda d: d['passages'][0].update(source_id='missing'), 'missing source'),
    (lambda d: d['evidence'][0].update(question_id='missing'), 'missing question'),
    (lambda d: d['evidence'][0].update(passage_ids=['missing']), 'missing passage'),
    (lambda d: d['assessments'][0].update(evidence_id='missing'), 'missing evidence'),
    (lambda d: d['synthesis'].update(evidence_ids=['missing']), 'missing evidence'),
    (lambda d: d['sources'][0].update(url='https://different.org'), 'successful source fetch'),
])
def test_rejects_broken_provenance(case, change, match):
    store, data = case
    change(data)
    with pytest.raises(ValueError, match=match):
        save(ResearchResult.model_validate(data), store)
    assert len(store.fetches()) == 1


def test_tampered_source_rejected(case):
    store, data = case
    store.path_of(data['sources'][0]['raw_ref']).write_bytes(b'tampered')
    with pytest.raises(ValueError, match='hash mismatch'):
        validate(ResearchResult.model_validate(data), store)


def test_successful_fetch_required(case):
    store, data = case
    records = [json.loads(line) for line in store.log_path.read_text().splitlines()]
    records[0]['http_status'] = 404
    store.log_path.write_text(json.dumps(records[0])+'\n')
    with pytest.raises(ValueError, match='successful source fetch'):
        validate(ResearchResult.model_validate(data), store)


def test_real_quote_cannot_promote_interpretation_to_verified_truth(case):
    store, data = case
    data['evidence'][0]['statement'] = 'This proves an extra spatial dimension exists.'
    result = ResearchResult.model_validate(data)
    assert validate(result, store)['semantic_truth_verified'] is False
    assert result.assessments[0].status == 'proposed'
    data['assessments'][0]['status'] = 'verified'
    with pytest.raises(ValidationError):
        ResearchResult.model_validate(data)


def test_duplicate_ids_and_schema_versions_rejected(case):
    _, data = case
    data['questions'].append(data['questions'][0])
    with pytest.raises(ValidationError, match='duplicate IDs'):
        ResearchResult.model_validate(data)
    data['questions'].pop()
    data['schema_version'] = 'gleipnir.research/999'
    with pytest.raises(ValidationError):
        ResearchResult.model_validate(data)


def test_text_representation_is_explicit():
    assert source_text(b'<p>A  B</p><style>hidden</style>', 'text/html') == 'A B'
    assert source_text(b'A\n B', 'text/plain') == 'A B'
    with pytest.raises(ValueError):
        source_text(b'PDF', 'application/pdf')


@pytest.mark.parametrize('valid', [True, False])
def test_cli_validates_before_export(case, tmp_path, valid):
    import subprocess
    import sys
    from pathlib import Path

    store, data = case
    if not valid:
        data['passages'][0]['quote'] = 'Invented finding.'
    manifest = tmp_path / 'manifest.json'
    manifest.write_text(json.dumps(data))
    output = tmp_path / 'report.md'
    script = Path(__file__).resolve().parents[1] / 'scripts/research.py'
    completed = subprocess.run(
        [sys.executable, str(script), str(manifest), '--store', str(store.root),
         '--save', '--draft', '--markdown', str(output)], capture_output=True, text=True,
    )
    assert completed.returncode == (0 if valid else 1), completed.stderr
    assert output.exists() is valid
    assert len(store.fetches()) == (2 if valid else 1)


def test_atom_without_time_is_open_not_timeless(case):
    store, data = case
    result = ResearchResult.model_validate(data)
    assert validate(result, store)['time_open'] == ['e1']
    assert 'OPEN' in render(result)


def test_source_date_bounds_atom_as_asserted(case):
    store, data = case
    data['sources'][0]['published_on'] = '2024-05-01'
    result = ResearchResult.model_validate(data)
    checks = validate(result, store)
    assert (checks['time_asserted'], checks['time_open']) == (1, [])
    assert 'as asserted by 2024-05-01' in render(result)


def test_stated_period_wins_and_is_ordered(case):
    store, data = case
    data['evidence'][0]['holds'] = {'start': '2023-01-01', 'end': '2023-06-30'}
    result = ResearchResult.model_validate(data)
    assert validate(result, store)['time_stated'] == 1
    assert '2023-01-01 → 2023-06-30' in render(result)
    data['evidence'][0]['holds'] = {'start': '2023-06-30', 'end': '2023-01-01'}
    with pytest.raises(ValidationError, match='ends before'):
        ResearchResult.model_validate(data)
