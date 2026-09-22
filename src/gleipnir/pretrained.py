"""Pinned, pretrained NLI inference on CPU. No training or remote inference.

Baseline model: https://huggingface.co/cross-encoder/nli-MiniLM2-L6-H768
The pretrained classifier checks source support only; atomicity still needs an
assessment. Oversized pairs are refused rather than silently truncated.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path

import httpx

from gleipnir.alignment import AlignmentInput, Judgment

MODEL_ID = 'cross-encoder/nli-MiniLM2-L6-H768'
REVISION = 'b95119ce93d3e065de6214e38cd4a97b0f2f2c6d'
# Full-precision graph is portable across CPU architectures; no GPU required.
ARTIFACTS = {'model.onnx': 'onnx/model.onnx', 'tokenizer.json': 'tokenizer.json',
             'config.json': 'config.json'}
EXPECTED_HASHES = {
    'model.onnx': '807d33fffefd95ad60e2a91f54eed50a92aa53c077945a6842bb587a49a3fdf3',
    'tokenizer.json': '82139106e603ee4e1d5bc99d056ccbed5a92bc24848b1b5a7137c26e00d0dbf6',
    'config.json': '8b0e41caff7567c0f53e6983f35591c3dec59507c9173ab125c5823394fb57f3',
}
DEFAULT_DIRECTORY = Path('raw/models/nli-MiniLM2-L6-H768')
METHOD = 'pretrained-nli-onnx/1'
MAX_TOKENS = 512


def find_directory(store: Path | None = None) -> Path:
    """The model directory: beside the raw store if given, else under the
    current directory, else under the repository that holds this package (a
    git worktree sits inside its main checkout). Never just the working
    directory, which changes with the caller."""
    tail = DEFAULT_DIRECTORY.relative_to('raw')
    here = Path(__file__).resolve()
    candidates = ([Path(store) / tail] if store else []) + [DEFAULT_DIRECTORY.resolve()] + \
        [parent / DEFAULT_DIRECTORY for parent in here.parents]
    for directory in candidates:
        if (directory / 'manifest.json').exists():
            return directory
    raise FileNotFoundError(f'no support model in {candidates[0]} or any raw/models above {here.parent}: '
                            'run scripts/prepare_nli.py, or pass --store where raw/models lives')


class ContextWindowExceeded(ValueError):
    pass


def digest(path: Path) -> str:
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def prepare(directory: Path = DEFAULT_DIRECTORY) -> dict:
    """Download only the pinned public model artifacts; record actual hashes."""
    directory.mkdir(parents=True, exist_ok=True)
    manifest_path = directory / 'manifest.json'
    if manifest_path.exists():
        return verify_artifacts(directory)
    manifest = {'model_id': MODEL_ID, 'revision': REVISION, 'files': {}}
    with httpx.Client(follow_redirects=True, timeout=60) as client:
        for filename, remote in ARTIFACTS.items():
            path = directory / filename
            temporary = directory / (filename + '.partial')
            url = f'https://huggingface.co/{MODEL_ID}/resolve/{REVISION}/{remote}'
            try:
                with client.stream('GET', url) as response:
                    response.raise_for_status()
                    with temporary.open('wb') as stream:
                        for chunk in response.iter_bytes():
                            stream.write(chunk)
                os.replace(temporary, path)
            finally:
                temporary.unlink(missing_ok=True)
            actual_hash = digest(path)
            if actual_hash != EXPECTED_HASHES[filename]:
                raise ValueError(f'download differs from pinned artifact: {filename}')
            manifest['files'][filename] = {'sha256': actual_hash, 'url': url,
                                          'bytes': path.stat().st_size}
    temporary = directory / 'manifest.json.partial'
    temporary.write_text(json.dumps(manifest, indent=2)+'\n')
    os.replace(temporary, manifest_path)
    return manifest


def verify_artifacts(directory: Path) -> dict:
    manifest = json.loads((directory / 'manifest.json').read_text())
    if manifest['model_id'] != MODEL_ID or manifest['revision'] != REVISION:
        raise ValueError('unexpected pretrained model or revision')
    if set(manifest['files']) != set(ARTIFACTS):
        raise ValueError('incomplete pretrained artifacts')
    for filename, expected in manifest['files'].items():
        path = directory / filename
        if (expected['sha256'] != EXPECTED_HASHES[filename]
                or path.stat().st_size != expected['bytes']
                or digest(path) != expected['sha256']):
            raise ValueError(f'pretrained artifact mismatch: {filename}')
    return manifest


class PretrainedNLIBackend:
    def __init__(self, directory: Path | None = None, *, threads: int = 2):
        if threads < 1:
            raise ValueError('threads must be positive')
        self.directory = Path(directory) if directory else find_directory()
        self.threads = threads
        self.manifest = verify_artifacts(self.directory)
        config = json.loads((self.directory / 'config.json').read_text())
        self.labels = [config['id2label'][str(i)].lower() for i in range(3)]
        if set(self.labels) != {'entailment', 'contradiction', 'neutral'}:
            raise ValueError('unsupported NLI label mapping')
        fingerprint = hashlib.sha256(json.dumps(self.manifest, sort_keys=True).encode()).hexdigest()
        from importlib.metadata import version
        self.identity = (f'{METHOD}:{MODEL_ID}@{REVISION}:{fingerprint}:'
                         f'ort={version("onnxruntime")}:tokenizers={version("tokenizers")}:threads={threads}')
        self._session = None
        self._tokenizer = None

    def _load(self):
        if self._session is not None:
            return
        import onnxruntime as ort
        from tokenizers import Tokenizer
        options = ort.SessionOptions()
        options.intra_op_num_threads = self.threads
        options.inter_op_num_threads = 1
        session = ort.InferenceSession(str(self.directory / 'model.onnx'),
                                            sess_options=options, providers=['CPUExecutionProvider'])
        tokenizer = Tokenizer.from_file(str(self.directory / 'tokenizer.json'))
        tokenizer.no_truncation()
        tokenizer.no_padding()
        self._session, self._tokenizer = session, tokenizer

    def classify(self, premise: str, hypothesis: str) -> dict[str, float]:
        """Entailment, contradiction and neutral probabilities for one pair."""
        from gleipnir.agents import Capability, require
        require("source_aligner", Capability.PROPOSE_EVIDENCE)
        import numpy as np
        self._load()
        encoded = self._tokenizer.encode(premise, hypothesis)
        if len(encoded.ids) > MAX_TOKENS:
            raise ContextWindowExceeded(f'pair has {len(encoded.ids)} tokens; maximum {MAX_TOKENS}')
        available = {'input_ids': [encoded.ids], 'attention_mask': [encoded.attention_mask],
                     'token_type_ids': [encoded.type_ids]}
        inputs = {i.name: np.asarray(available[i.name], dtype=np.int64)
                  for i in self._session.get_inputs()}
        logits = self._session.run(None, inputs)[0][0]
        if len(logits) != 3 or not all(math.isfinite(float(x)) for x in logits):
            raise ValueError('invalid NLI output')
        shifted = [math.exp(float(x)-float(max(logits))) for x in logits]
        return {label: value/sum(shifted) for label, value in zip(self.labels, shifted)}

    def assess(self, pair: AlignmentInput) -> Judgment:
        from gleipnir.agents import Capability, require
        require("source_aligner", Capability.PROPOSE_EVIDENCE)
        import numpy as np
        self._load()
        encoded = self._tokenizer.encode(pair.context, pair.statement)
        if len(encoded.ids) > MAX_TOKENS:
            raise ContextWindowExceeded(f'pair has {len(encoded.ids)} tokens; maximum {MAX_TOKENS}')
        available = {'input_ids': [encoded.ids], 'attention_mask': [encoded.attention_mask],
                     'token_type_ids': [encoded.type_ids]}
        inputs = {i.name: np.asarray(available[i.name], dtype=np.int64)
                  for i in self._session.get_inputs()}
        logits = self._session.run(None, inputs)[0][0]
        if len(logits) != 3 or not all(math.isfinite(float(x)) for x in logits):
            raise ValueError('invalid NLI output')
        shifted = [math.exp(float(x)-float(max(logits))) for x in logits]
        probabilities = {label: value/sum(shifted) for label, value in zip(self.labels, shifted)}
        label = max(probabilities, key=probabilities.get)
        relation = {'entailment': 'supports', 'contradiction': 'contradicts', 'neutral': 'insufficient'}[label]
        return Judgment(
            relation=relation, support_score=probabilities['entailment'],
            atomic=None, truth_evaluable=None, context_sufficient=None, issues=(),
            supporting_quote=pair.context if relation != 'insufficient' else '',
            rationale='Pretrained NLI classification of the statement against the full supplied context: '
                      + json.dumps(probabilities, sort_keys=True)
                      + '. Does not assess atomicity, qualification completeness, context sufficiency or source truth.')
