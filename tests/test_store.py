import json

import pytest

from test_workspace import STUDIES, Entails, framed, gl, grounding, predicted
from gleipnir.store import (Store, _known_by, atom_id, compile_workspace, confirm, import_project, locate,
                            matrix_view, records_as_of)
from gleipnir.workspace import ToolError, Workspace

DATED = STUDIES.replace(b'<html><body>', b'<html><head><script type="application/ld+json">'
                                         b'{"datePublished": "2012-05-01"}</script></head><body>')
LATER = (b'<html><head><script type="application/ld+json">{"datePublished": "2020-02-01"}</script></head><body>'
         b'<p>The Cohort Study followed 9,000 patients meeting the Canadian Consensus Criteria and found no change in '
         b'cytokines after exercise.</p></body></html>')


def workspace(tmp_path, name, page=DATED, url='https://review.example/a'):
    w = Workspace(tmp_path / name, tmp_path / 'raw', classifier=Entails())
    w.init('What causes the illness?')
    sid = w.ingest(url, page, 200)['id']
    p = {k: w.cut(sid, a, before=0, after=150)['passage'] for k, a in (
        ('decon', 'Some authors propose'), ('immune', 'Others propose'), ('trial', 'The Graded Trial randomised'),
        ('cyto', 'The Cytokine Study'), ('critique', 'A published reanalysis'), ('inst', 'In 2007 the Institute'))}
    return w, p


def framed_ws(tmp_path, name, **kw):
    w, p = workspace(tmp_path, name, **kw)
    w.frame(framed())
    m = predicted(p)
    m['looked_for'] = [{'item': 'the newest trial of graded exercise', 'row': 'graded-trial'},
                       {'item': 'the largest cohort with immune markers', 'not_found': 'none in this review'}]
    assert w.matrix(m)['refused'] == []
    return w, p


def store(tmp_path):
    return Store(tmp_path / 'research', tmp_path / 'raw')


def test_words_are_located_as_every_quote_check_reads_them():
    text = 'Intro.  The  Graded\nTrial ran-\ndomised 641 patients.'
    s, e = locate(text, 'the graded trial randomised 641')
    assert text[s:e] == 'The  Graded\nTrial ran-\ndomised 641'
    assert locate(text, 'the graded trial', 20) is None and locate(text, '') is None


def test_a_partial_date_is_known_only_once_all_of_it_has_passed():
    assert _known_by('2018', '2018') and _known_by('2018', '2019-01-01') and not _known_by('2018', '2018-06-01')
    assert _known_by('2018-05-02', '2018-06') and not _known_by(None, '2030')


def test_the_matrix_view_reproduces_matrix_show_exactly(tmp_path):
    w, p = framed_ws(tmp_path, 'ws')
    w.evidence_status('cytokine-study', 'disputed', p['critique'], 'recovery rates fell sharply',
                      note='a critique', kind='objection')
    raw = json.loads((w.root / 'matrix.json').read_text())         # parts the workspace drops on use
    raw['evidence'][0]['status'].append({'status': 'disputed', 'passage': p['critique'], 'note': 'no kind',
                                         'words': 'recovery rates fell sharply'})
    raw['evidence'][1]['cells']['H2']['words'] = 'words that are not in the passage'
    (w.root / 'matrix.json').write_text(json.dumps(raw))
    st = store(tmp_path)
    out = compile_workspace(st, w.root, 'proj')
    want = w.matrix_show()
    assert want['dropped_on_use'] and matrix_view(st, 'proj') == want
    assert out['not_grounded'] == 1 and out['atoms_local'] == 0 and out['atoms_global'] > 5


def test_compile_only_reads_the_workspace_and_is_idempotent(tmp_path):
    w, _ = framed_ws(tmp_path, 'ws')
    before = {f.name: f.read_bytes() for f in w.root.iterdir()}
    st = store(tmp_path)
    first = compile_workspace(st, w.root, 'proj')
    second = compile_workspace(Store(tmp_path / 'research', tmp_path / 'raw'), w.root, 'proj')
    assert {f.name: f.read_bytes() for f in w.root.iterdir()} == before
    assert first['new_records'] > 0 and second['new_records'] == 0
    assert second['cache']['verified'] == 0 and second['cache']['cached'] > 0      # nothing re-verified
    other, _ = framed_ws(tmp_path, 'other')
    with pytest.raises(ToolError, match='import'):
        compile_workspace(st, other.root, 'proj')


def test_two_workspaces_citing_one_passage_share_one_global_atom(tmp_path):
    a, pa = framed_ws(tmp_path, 'a')
    b, pb = framed_ws(tmp_path, 'b')
    st = store(tmp_path)
    compile_workspace(st, a.root, 'pa')
    second = compile_workspace(st, b.root, 'pb')
    assert second['cache']['verified'] == 0 and second['cache']['cached'] > 0
    atoms = [json.loads(line) for line in open(tmp_path / 'research' / 'atoms.jsonl')]
    assert len(atoms) == len({x['id'] for x in atoms}) == second['atoms_global']
    words = 'The Graded Trial randomised 641 patients'
    assert sum(x['report'] == words.casefold() for x in atoms) == 1
    ids = lambda name: {r.get('atom') for r in records_as_of(st, name)} - {None}
    assert ids('pa') == ids('pb')


def test_an_undated_page_keeps_its_report_atoms_local(tmp_path):
    w, _ = framed_ws(tmp_path, 'ws', page=STUDIES)
    st = store(tmp_path)
    out = compile_workspace(st, w.root, 'proj')
    assert out['atoms_global'] == 0 and out['atoms_local'] > 0
    assert not (tmp_path / 'research' / 'atoms.jsonl').exists()
    assert matrix_view(st, 'proj') == w.matrix_show()


def test_an_undo_withdraws_a_record_and_an_edited_record_is_dropped_on_use(tmp_path):
    w, _ = framed_ws(tmp_path, 'ws')
    st = store(tmp_path)
    compile_workspace(st, w.root, 'proj')
    project = st.project('proj')
    cell = next(r for r in project.standing() if r['type'] == 'reading' and r['explanation'] == 'H1'
                and r['evidence'] == 'cytokine-study')
    with pytest.raises(ToolError, match='note'):
        project.undo(cell['id'], '')
    project.undo(cell['id'], 'misread')
    grid = matrix_view(st, 'proj')['grid']
    assert grid[2].split()[:3] == ['cytokine-study', '.', 'C']
    assert sum(1 for r in project.records() if r['id'] == cell['id']) == 1        # the file keeps both
    lines = project.path.read_text().replace('Graded Trial randomised 641', 'Graded Trial randomised 999')
    project.path.write_text(lines)
    assert any(d.get('row') == 'graded-trial' for d in matrix_view(st, 'proj')['dropped_on_use'])


def test_a_position_query_keeps_only_evidence_dated_by_then(tmp_path):
    w, p = framed_ws(tmp_path, 'ws')
    sid = w.ingest('https://cohort.example/b', LATER, 200)['id']
    pid = w.cut(sid, 'The Cohort Study', before=0, after=150)['passage']
    m = json.loads((w.root / 'matrix.json').read_text())
    m['evidence'].append({'id': 'cohort-2020', 'passage': pid, 'words': 'followed 9,000 patients', 'design': 'cohort',
                          'n': 9000, 'case_definition': 'Canadian Consensus Criteria', 'cells': {
                              'H1': {'reading': 'inconsistent', 'words': 'found no change in cytokines after exercise'}}})
    assert w.matrix(m)['refused'] == []
    st = store(tmp_path)
    compile_workspace(st, w.root, 'proj')
    rows = lambda k: [line.split()[0] for line in matrix_view(st, 'proj', k)['grid'][1:]]
    assert rows(None) == ['graded-trial', 'cytokine-study', 'cohort-2020']
    assert rows('2019') == rows('2020-01-31') == ['graded-trial', 'cytokine-study']
    assert rows('2012-04') == []                                   # the review itself is from May 2012
    assert matrix_view(st, 'proj', '2020-02-01')['grid'] == matrix_view(st, 'proj')['grid']
    with pytest.raises(ToolError, match='date'):
        matrix_view(st, 'proj', 'last year')


def test_an_import_regrounds_inherits_readings_unchecked_and_never_writes_the_source(tmp_path):
    a, _ = framed_ws(tmp_path, 'a')
    b, pb = workspace(tmp_path, 'b')
    b.frame(framed())
    b.matrix({'explanations': grounding(pb), 'evidence': []})
    st = store(tmp_path)
    compile_workspace(st, a.root, 'pa')
    compile_workspace(st, b.root, 'pb')
    theirs = (st.project('pa').path).read_bytes()
    out = import_project(st, 'pb', 'pa')
    assert (st.project('pa').path).read_bytes() == theirs
    assert out['readings_unchecked'] == 6 and out['refused'] == [] and out['atoms_already_in_b'] > 0
    assert out['cache']['verified'] == 0                            # the atoms arrive already verified
    grid = matrix_view(st, 'pb')['grid']
    assert grid[1].split()[:4] == ['graded-trial', '.', '.', '.']   # inherited readings do not count yet
    recs = st.project('pb').standing()
    reading = next(r for r in recs if r['type'] == 'reading' and r['explanation'] == 'H1'
                   and r['evidence'] == 'graded-trial')
    assert reading['inherited']['status'] == 'unchecked in this context' and reading['imported']['from'] == 'pa'
    with pytest.raises(ToolError, match='note'):
        confirm(st, 'pb', reading['id'], ' ')
    confirm(st, 'pb', reading['id'], 'same population and definition here')
    assert matrix_view(st, 'pb')['grid'][1].split()[:4] == ['graded-trial', 'C', '.', '.']
    again = import_project(st, 'pb', 'pa')
    assert again['imported'] == 0 and again['already_in_b'] == out['imported'] + out['already_in_b']
    log = [json.loads(line) for line in open(st.project('pb').root / 'log.jsonl')]
    assert [e['tool'] for e in log][-3:] == ['import', 'confirm', 'import'] and log[-1]['from'] == 'pa'


def test_an_inherited_reading_counts_only_for_the_same_explanation(tmp_path):
    a, _ = framed_ws(tmp_path, 'a')
    b, pb = workspace(tmp_path, 'b')
    f = framed()
    f['questions'][0]['explanations'][0]['claim'] = 'a different idea under the same id'
    b.frame(f)
    st = store(tmp_path)
    compile_workspace(st, a.root, 'pa')
    compile_workspace(st, b.root, 'pb')
    out = import_project(st, 'pb', 'pa', only=['cytokine-study'])
    kinds = {r['type'] for r in st.project('pb').standing() if r.get('imported')}
    assert 'rests' in kinds and 'reading' in kinds and 'explanation' not in kinds   # the framing stays A's
    assert [line.split()[0] for line in matrix_view(st, 'pb')['grid'][1:]] == ['cytokine-study']
    h1 = next(r for r in st.project('pb').standing() if r['type'] == 'reading' and r['explanation'] == 'H1')
    with pytest.raises(ToolError, match='not the explanation'):
        confirm(st, 'pb', h1['id'], 'looks the same')
    with pytest.raises(ToolError, match='no evidence'):
        import_project(st, 'pb', 'pa', only=['nothing-like-it'])
    assert out['imported'] > 0


def test_atom_ids_are_content_addresses():
    assert atom_id('ab', 1, 5, 'x y') == atom_id('ab', 1, 5, 'x y') != atom_id('ab', 1, 6, 'x y')


def test_the_cli_compiles_imports_and_views(tmp_path):
    w, _ = framed_ws(tmp_path, 'ws')
    raw, research = tmp_path / 'raw', tmp_path / 'research'
    out = gl('compile', w.root, 'proj', '--store', raw, '--research', research)
    assert out.returncode == 0 and json.loads(out.stdout)['new_records'] > 0
    shown = gl('matrix', w.root, '--store', raw)
    view = gl('view', 'proj', 'matrix', '--store', raw, '--research', research)
    assert view.returncode == 0 and view.stdout == shown.stdout
    assert gl('view', 'proj', 'matrix', '--as-of', '2011', '--store', raw, '--research', research).returncode == 0
    out = gl('view', 'proj', 'matrix', '--only', 'x', '--store', raw, '--research', research)
    assert out.returncode == 2 and 'view does not take --only' in out.stderr
    out = gl('import', 'other', 'proj', '--store', raw, '--research', research)
    assert out.returncode == 0 and json.loads(out.stdout)['readings_unchecked'] == 6
    assert gl('compile', w.root, '../up', '--store', raw, '--research', research).returncode == 2
