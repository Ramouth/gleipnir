"""Wiring tests; the real pretrained-model pilot is recorded separately."""
import hashlib
import json
from types import SimpleNamespace

import pytest

from gleipnir.alignment import AlignmentInput, passes
from gleipnir.pretrained import (
    ARTIFACTS, MODEL_ID, REVISION, ContextWindowExceeded,
    PretrainedNLIBackend, prepare, verify_artifacts,
)


@pytest.fixture
def artifacts(tmp_path, monkeypatch):
    # Tiny artifacts stand in for network downloads; production pins real hashes.
    data = {'model.onnx': b'fixture', 'tokenizer.json': b'{}',
            'config.json': json.dumps({'id2label': {'0': 'neutral', '1': 'contradiction', '2': 'entailment'}}).encode()}
    hashes = {name: hashlib.sha256(payload).hexdigest() for name, payload in data.items()}
    monkeypatch.setattr('gleipnir.pretrained.EXPECTED_HASHES', hashes)
    monkeypatch.setattr('importlib.metadata.version', lambda name: 'test-version')
    manifest = dict(model_id=MODEL_ID, revision=REVISION, files={})
    for name, payload in data.items():
        (tmp_path / name).write_bytes(payload)
        manifest['files'][name] = dict(sha256=hashes[name], bytes=len(payload), url='fixture')
    (tmp_path / 'manifest.json').write_text(json.dumps(manifest))
    return tmp_path


def pair():
    return AlignmentInput(evidence_id='a', passage_id='p', source_ref='a'*64,
                          statement='A claim.', qualification='Only this context.', quote='Source.',
                          context='Source.', context_start=0, context_end=7,
                          clipped_left=False, clipped_right=False)


def backend_with_runtime(artifacts, logits, length=8):
    pytest.importorskip('numpy')
    backend = PretrainedNLIBackend(artifacts)
    calls = []
    class Tokenizer:
        def encode(self, context, statement):
            calls.append((context, statement))
            return SimpleNamespace(ids=[1]*length, attention_mask=[1]*length, type_ids=[0]*length)
    class Session:
        def get_inputs(self):
            return [SimpleNamespace(name='input_ids'), SimpleNamespace(name='attention_mask')]
        def run(self, outputs, inputs):
            calls.append(inputs)
            return [[logits]]
    backend._session = Session()
    backend._tokenizer = Tokenizer()
    return backend, calls


def test_artifacts_are_pinned_and_prepare_reuses_verified_download(artifacts):
    assert set(verify_artifacts(artifacts)['files']) == set(ARTIFACTS)
    assert prepare(artifacts)['revision'] == REVISION
    (artifacts / 'model.onnx').write_bytes(b'changed')
    with pytest.raises(ValueError, match='mismatch'):
        verify_artifacts(artifacts)


def test_manifest_cannot_bless_modified_weights(artifacts):
    payload = b'new weights'
    (artifacts / 'model.onnx').write_bytes(payload)
    path = artifacts / 'manifest.json'
    manifest = json.loads(path.read_text())
    manifest['files']['model.onnx'].update(sha256=hashlib.sha256(payload).hexdigest(), bytes=len(payload))
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match='mismatch'):
        verify_artifacts(artifacts)


def test_support_uses_config_labels_not_hardcoded_positions(artifacts):
    backend, calls = backend_with_runtime(artifacts, [0.0, -1.0, 5.0])
    judgment = backend.assess(pair())
    assert calls[0] == ('Source.', 'A claim.')
    assert judgment.relation == 'supports' and judgment.support_score > 0.98
    assert judgment.atomic is None and not passes(judgment, 0.8)


def test_contradiction_and_missing_support_differ(artifacts):
    backend, _ = backend_with_runtime(artifacts, [0.0, 5.0, -1.0])
    assert backend.assess(pair()).relation == 'contradicts'
    backend, _ = backend_with_runtime(artifacts, [5.0, 0.0, -1.0])
    assert backend.assess(pair()).relation == 'insufficient'


def test_oversized_context_is_not_silently_truncated(artifacts):
    backend, calls = backend_with_runtime(artifacts, [0.0, 0.0, 5.0], length=513)
    with pytest.raises(ContextWindowExceeded):
        backend.assess(pair())
    assert len(calls) == 1  # tokenizer only; no inference on an incomplete source


def test_invalid_scores_do_not_become_support(artifacts):
    backend, _ = backend_with_runtime(artifacts, [float('nan'), 0.0, 0.0])
    with pytest.raises(ValueError, match='invalid NLI'):
        backend.assess(pair())
