from gleipnir.atomiser import Atom, Passage, check
from gleipnir.graph import Graph


def passage(src, text):
    return Passage(id=src, source_id=src, source_date='2024', text=text)


def atom(src, label, polarity='affirmed', quote=None, predicate='reduces'):
    claim = {'subject': {'id': f'local:{src}#{label.lower()}', 'label': label},
             'predicate': predicate, 'value': 'claims', 'statement': f'{label} over-filters claims.',
             'holds': {'end': '2024', 'basis': 'asserted'}, 'polarity': polarity, 'modality': 'actual'}
    return Atom.model_validate({'passage_id': src, 'quote': quote or f'{label} over-filters claims',
                                'report': {'speaker': {'id': src, 'label': 'paper'}, 'verb': 'reports',
                                           'content': claim}})


def build(pairs):
    g = Graph()
    for i, (a, p) in enumerate(pairs):
        g.add(a, p, check(a, p), f'u{i}')
    return g


A = passage('arxiv:2401.00001', 'We find that VeriScore over-filters claims.')
B = passage('arxiv:2402.00002', 'VeriScore over-filters claims in our replication.')
C = passage('arxiv:2403.00003', 'VeriScore does not over-filter claims.')


def test_same_claim_matches_across_sources_only_when_label_aliases_are_accepted():
    g = build([(atom('arxiv:2401.00001', 'VeriScore'), A), (atom('arxiv:2402.00002', 'VeriScore'), B)])
    assert g.stats()['claims_in_two_or_more_sources'] == 0     # local ids never merge silently
    assert g.propose_label_aliases() == 2
    assert g.stats(accept={'same-label'})['claims_in_two_or_more_sources'] == 1


def test_opposite_polarity_is_a_contradiction():
    neg = atom('arxiv:2403.00003', 'VeriScore', polarity='negated', quote='VeriScore does not over-filter claims')
    g = build([(atom('arxiv:2401.00001', 'VeriScore'), A), (neg, C)])
    g.propose_label_aliases()
    assert g.stats(accept={'same-label'})['contradictions'] == 1


def test_misread_report_stays_out_of_the_graph():
    g = Graph()
    bad = atom('arxiv:2401.00001', 'VeriScore', quote='VeriScore under-filters claims')
    assert g.add(bad, A, check(bad, A), 'u0') is False
    assert g.stats()['nodes'] == {}


def test_report_chain_is_traversable():
    g = build([(atom('arxiv:2401.00001', 'VeriScore'), A)])
    kinds = {k for (k,) in g.db.execute('SELECT DISTINCT kind FROM edges')}
    assert {'by', 'content', 'subject'} <= kinds


def test_same_subject_and_relation_is_counted_even_when_objects_differ():
    b = atom('arxiv:2402.00002', 'VeriScore')
    b = Atom.model_validate({**b.model_dump(), 'report': {**b.model_dump()['report'],
                             'content': {**b.model_dump()['report']['content'], 'value': 'context'}}})
    g = build([(atom('arxiv:2401.00001', 'VeriScore'), A), (b, B)])
    g.propose_label_aliases()
    st = g.stats(accept={'same-label'})
    assert (st['claims_in_two_or_more_sources'], st['subject_relation_in_two_or_more_sources']) == (0, 1)
