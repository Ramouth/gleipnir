from gleipnir.ledger import Entry, Resolution, track_record


def entry(source='s1', group='g1', key='c1', closed=True, verb='reports', value='TRUE', by=('g2',)):
    res = Resolution(value, frozenset(by), '2026-09-22') if value else None
    return Entry(source, group, key, closed, verb, res)


def test_independent_confirmation_and_refutation():
    rec = track_record([entry(), entry(key='c2', value='FALSE'), entry(key='c3', value=None)])
    assert rec['s1'] == {'confirmed': 1, 'refuted': 1, 'unresolved': 1}


def test_self_or_copy_confirmation_does_not_count():
    assert track_record([entry(by=('g1',))])['s1'] == {'unresolved': 1}


def test_repetition_counts_once_and_extraction_errors_are_not_charged():
    rec = track_record([entry(), entry(), entry(key='c2', closed=False, value='FALSE')])
    assert rec['s1'] == {'confirmed': 1}


def test_a_denial_of_a_true_claim_refutes_the_denier():
    assert track_record([entry(verb='denies')])['s1'] == {'refuted': 1}
