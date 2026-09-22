import pytest

from gleipnir.atomiser import ATOMISER, Atom, Passage, check

P = Passage(id='p', source_id='fixture:gazette', source_date='2025-03-01',
            text='Example Holding A/S (CVR 00000001) did not sell its stake in 2024, the chief executive said.')
Q = 'Example Holding A/S (CVR 00000001) did not sell its stake in 2024, the chief executive said'

CLAIM = dict(subject={'id': 'cvr:00000001', 'label': 'Example Holding A/S'},
             predicate='owns', value='its stake',
             statement='Example Holding A/S did not sell its stake in 2024.',
             holds={'start': '2024', 'end': '2024', 'basis': 'stated'},
             polarity='negated', modality='actual')


def atom(claim=None, inner_verb='says', outer=None, quote=None, **claim_changes):
    content = {**CLAIM, **(claim or {}), **claim_changes}
    report = outer or {'speaker': {'id': 'fixture:gazette', 'label': 'the gazette'}, 'verb': 'reports',
                       'content': {'speaker': {'id': None, 'label': 'the chief executive'},
                                   'verb': inner_verb, 'content': content}}
    return Atom.model_validate({'passage_id': 'p', 'report': report,
                                'quote': quote or Q})


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


def test_local_ids_are_rigid_inside_their_source_only():
    p = Passage(id='p', source_id='arxiv:2406.19276v1', source_date='2024',
                text='We find that VeriScore over-filters claims on AskDocsAI.')
    claim = {'subject': {'id': 'local:arxiv:2406.19276v1#veriscore', 'label': 'VeriScore'},
             'predicate': 'reduces', 'value': 'claims', 'statement': 'VeriScore over-filters claims (2024).',
             'holds': {'end': '2024', 'basis': 'asserted'}, 'polarity': 'affirmed', 'modality': 'actual'}
    own = lambda c: {'speaker': {'id': 'arxiv:2406.19276v1', 'label': 'paper'}, 'verb': 'reports', 'content': c}
    ok = check(atom(outer=own(claim), quote='VeriScore over-filters claims'), p)
    assert ok['closed_local'] and not ok['closed'] and ok['scope'] == 'local'
    elsewhere = {**claim, 'subject': {'id': 'local:arxiv:9999.00001#veriscore', 'label': 'VeriScore'}}
    assert 'subject_local_id_wrong_source' in check(atom(outer=own(elsewhere), quote='VeriScore over-filters claims'), p)['defects']
    ghost = {**claim, 'subject': {'id': 'local:arxiv:2406.19276v1#factscore', 'label': 'FActScore'}}
    assert 'subject_local_id_not_in_passage' in check(atom(outer=own(ghost), quote='VeriScore over-filters claims'), p)['defects']
    no_relation = {**claim, 'predicate': None, 'value': None}
    assert check(atom(outer=own(no_relation), quote='VeriScore over-filters claims'), p)['claim']['open'] == ['relation']


def test_predicate_vocabulary():
    assert 'predicate_not_in_vocabulary' in check(atom(predicate='can_be_inflated_by'), P)['defects']
    assert check(atom(predicate='other_sold_quietly'), P)['claim']['open'] == ['relation']
    assert check(atom(predicate='owns', value='its stake'), P)['claim']['closed']


def test_version_numbers_in_local_ids_and_the_source_as_subject():
    from gleipnir.atomiser import _id_defects
    p = Passage(id='p', source_id='web:arxiv.org/8122d58e62', source_date='2025-11',
                text='We evaluate Llama-3.1-70B-Instruct on the benchmark.')
    assert _id_defects('local:web:arxiv.org/8122d58e62#llama-3.1-70b-instruct', p, p.text.casefold()) == []
    assert _id_defects('web:arxiv.org/8122d58e62', p, p.text.casefold()) == []
    assert _id_defects('web:arxiv.org/0000000000', p, p.text.casefold()) == ['not_rigid']


def test_meaning_checks_are_review_items_not_refusals():
    v = check(atom(polarity='affirmed', statement='Example Holding A/S sold its stake in 2024.'), P)
    assert 'polarity_review' in v['review'] and not v['defects'] and not v['closed']
