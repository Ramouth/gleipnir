import json

from gleipnir.differences import WHY_QUESTION, candidates, why_differ
from test_workspace import PAGE, Workspace


def row(source, value, published, **kw):
    return {'source': source, 'value': value, 'published': published, 'polarity': 'affirmed',
            'statement': f'The talks took {value}.', **kw}


def test_close_numbers_and_different_dates_become_leads_not_conclusions():
    kinds = [c['kind'] for c in candidates(row('a', '69 days', '2026-06-01'), row('b', '71 days', '2026-07-09'))]
    assert 'time' in kinds and 'definition' in kinds and kinds[-1] == 'error'


def test_agreeing_sources_get_no_question():
    assert why_differ([row('a', '38 seats', '2026-06-01'), row('b', '38 seats', '2026-06-02')]) is None
    why = why_differ([row('a', '38 seats', '2026-06-01'), row('b', '32 seats', '2026-07-09')])
    assert why['question'] == WHY_QUESTION and why['pairs'][0]['values'] == ['38 seats', '32 seats']


def test_an_explanation_needs_a_real_quote(tmp_path):
    import pytest
    from gleipnir.workspace import ToolError
    w = Workspace(tmp_path / 'ws', tmp_path / 'raw', classifier=False)
    w.init('q')
    sid = w.ingest('https://example.org/a', PAGE, 200)['id']
    pid = w.cut(sid, 'Example Holding A/S', before=0, after=80)['passage']
    with pytest.raises(ToolError):
        w.explain('x|y', 'definition', pid, 'words that are not there', 'n')
    with pytest.raises(ToolError):
        w.explain('x|y', 'definition', pid, '', 'no quote')
    e = w.explain('x|y', 'time', pid, 'sold its stake in Example Shipping A/S in 2024', 'dated sale')
    assert e['reason'] == 'time'
    assert w.explain('x|y', 'unexplained', pid, '', 'nothing in the text')['reason'] == 'unexplained'
