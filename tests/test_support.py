from gleipnir.atomiser import trace
from gleipnir.support import check_support, hypothesis
from test_atomiser import P, atom, check


class Stub:
    def __init__(self, label):
        self.label, self.seen = label, None

    def classify(self, premise, hypothesis):
        self.seen = (premise, hypothesis)
        return {k: (0.9 if k == self.label else 0.05) for k in ('entailment', 'neutral', 'contradiction')}


def test_hypothesis_drops_the_time_the_atomiser_bound():
    a = atom(statement='By 2024, Example Holding A/S did not sell its stake.')
    assert hypothesis(a) == 'The chief executive said that Example Holding A/S did not sell its stake.'


def test_support_flag_is_open_for_review_never_a_defect():
    a = atom()
    stub = Stub('neutral')
    s = check_support(a, stub)
    assert stub.seen[0] == a.quote and s['flag'] == 'support_review'
    t = trace(check(a, P), s)
    assert t['first_failure'] == 'support' and t['steps']['support']['status'] == 'open'
    assert trace(check(a, P), check_support(a, Stub('entailment')))['first_failure'] is None


def test_a_flag_reaches_the_llm_as_a_question_never_as_a_verdict():
    from gleipnir.support import review_request
    a = atom()
    request = review_request(a, check_support(a, Stub('contradiction')))
    text = str(request).lower()
    assert request['quote'] == a.quote
    assert not any(w in text for w in ('contradiction', 'neutral', 'entail', 'score', '0.9', 'classifier', 'verified'))
    assert review_request(a, check_support(a, Stub('entailment'))) is None


def test_a_skipped_check_is_visible():
    class TooLong:
        def classify(self, premise, hypothesis):
            raise ValueError('pair has 900 tokens; maximum 512')
    a = atom()
    s = check_support(a, TooLong())
    assert s['flag'] == 'support_not_run'
    assert trace(check(a, P), s)['steps']['support']['status'] == 'open'
