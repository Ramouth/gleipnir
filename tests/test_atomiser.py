import pytest

from gleipnir.atomiser import ATOMISER, Atom, Passage, check

P = Passage(id='p', source_id='fixture:gazette', source_date='2025-03-01',
            text='Example Holding A/S (CVR 00000001) did not sell its stake in 2024, the chief executive said.')

CLAIM = dict(subject={'id': 'cvr:00000001', 'label': 'Example Holding A/S'},
             statement='Example Holding A/S did not sell its stake in 2024.',
             holds={'start': '2024', 'end': '2024', 'basis': 'stated'},
             polarity='negated', modality='actual')


def atom(claim=None, inner_verb='says', outer=None, quote=None, **claim_changes):
    content = {**CLAIM, **(claim or {}), **claim_changes}
    report = outer or {'speaker': {'id': 'fixture:gazette', 'label': 'the gazette'}, 'verb': 'reports',
                       'content': {'speaker': {'id': None, 'label': 'the chief executive'},
                                   'verb': inner_verb, 'content': content}}
    return Atom.model_validate({'passage_id': 'p', 'report': report,
                                'quote': quote or 'did not sell its stake in 2024, the chief executive said'})


def test_correct_atom_is_closed_at_both_levels():
    v = check(atom(), P)
    assert v['closed'] and v['report']['closed'] and v['claim']['closed']


@pytest.mark.parametrize('kw,defect', [
    (dict(quote='sold its stake'), 'quote_not_in_passage'),
    (dict(subject={'id': 'Example Holding A/S', 'label': 'x'}), 'subject_not_rigid'),
    (dict(subject={'id': 'cvr:99999999', 'label': 'x'}), 'subject_id_not_in_passage'),
    (dict(statement='Example Holding A/S currently has not sold its stake.'), 'indexical_in_statement'),
    (dict(holds={'start': '2019', 'end': '2019', 'basis': 'stated'}), 'time_not_in_source'),
    (dict(holds={'end': '2024-01-01', 'basis': 'asserted'}), 'asserted_time_not_source_date'),
    (dict(polarity='affirmed', statement='Example Holding A/S sold its stake in 2024.'), 'negation_dropped'),
])
def test_claim_defects(kw, defect):
    assert defect in check(atom(**kw), P)['defects']


def test_report_defects():
    own_voice = {'speaker': {'id': 'fixture:gazette', 'label': 'g'}, 'verb': 'asserts', 'content': CLAIM}
    assert 'attribution_dropped' in check(atom(outer=own_voice), P)['report']['defects']
    other = {'speaker': {'id': 'fixture:elsewhere', 'label': 'g'}, 'verb': 'reports', 'content': CLAIM}
    assert 'outer_speaker_not_source' in check(atom(outer=other), P)['report']['defects']
    ghost = {'speaker': {'id': 'fixture:gazette', 'label': 'g'}, 'verb': 'reports',
             'content': {'speaker': {'id': None, 'label': 'the finance minister'}, 'verb': 'says', 'content': CLAIM}}
    assert 'speaker_not_in_passage' in check(atom(outer=ghost), P)['report']['defects']


def test_denial_is_structural_not_a_dropped_negation():
    v = check(atom(inner_verb='denies', polarity='affirmed', statement='Example Holding A/S sold its stake in 2024.'), P)
    assert 'negation_dropped' not in v['defects']


def test_inference_never_closes():
    inferred = {'speaker': {'id': ATOMISER, 'label': 'atomiser'}, 'verb': 'infers',
                'content': {'speaker': {'id': 'fixture:gazette', 'label': 'g'}, 'verb': 'reports', 'content': CLAIM}}
    v = check(atom(outer=inferred), P)
    assert not v['closed'] and 'inferred' in v['report']['open'] and v['claim']['closed']


def test_closed_report_of_open_claim():
    v = check(atom(subject={'id': None, 'label': 'the stake'}, holds={'basis': 'open'}), P)
    assert v['report']['closed'] and not v['claim']['closed'] and v['claim']['open'] == ['subject', 'time']


def test_relative_year_and_decade_are_not_fabrication():
    p = Passage(id='p', source_id='fixture:gazette', source_date='2025-03-01',
                text='Last year Example Holding A/S (CVR 00000001) sold a stake. It was founded in the early 2000s.')
    own = lambda c: {'speaker': {'id': 'fixture:gazette', 'label': 'g'}, 'verb': 'asserts', 'content': {**CLAIM, **c}}
    sold = atom(outer=own(dict(statement='Example Holding A/S sold a stake in 2024.', polarity='affirmed')),
                quote='Last year Example Holding A/S')
    assert 'time_not_in_source' not in check(sold, p)['defects']
    founded = atom(outer=own(dict(statement='Example Holding A/S was founded between 2000 and 2009.', polarity='affirmed',
                                  holds={'start': '2000', 'end': '2009', 'basis': 'stated'})),
                   quote='founded in the early 2000s')
    assert 'time_not_in_source' not in check(founded, p)['defects']


def test_trace_points_at_the_first_failing_step():
    from gleipnir.atomiser import trace
    t = trace(check(atom(subject={'id': None, 'label': 'x'},
                         holds={'start': '2019', 'end': '2019', 'basis': 'stated'}), P))
    assert t['first_failure'] == 'subject'
    assert (t['steps']['subject']['status'], t['steps']['time']['status']) == ('open', 'defect')
    assert trace(check(atom(), P))['first_failure'] is None
