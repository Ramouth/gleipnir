# Gleipnir

Evidence you can check. Gleipnir turns sources into small, dated, attributed
statements, verifies every step in code, and says exactly where a step failed.
A language model may propose; only deterministic code may conclude.

It has two parts:

- **Research core** (new, active): passages → *reports* ("source X says Y") →
  closed statements → source track records. Domain-independent.
- **Corporate screening** (first domain): one company identifier in; a chain of
  comparable facts and a short list of findings out, for concealed ownership
  and control of corporate assets.

## Core ideas

**No implicit "now".** Every truth value is evaluated from a position: the time
the statement is about, and what was known when. A statement that cannot be
dated is `UNKNOWN` at every date, never `TRUE` at every date.

**Atoms are eternal sentences.** "X is CEO" is stored as "X was CEO in 2024".
The time moves into the statement, so its truth no longer changes as time
passes. Present tense exists only in questions and rendered output.

**Every atom is a report: X reports Y.** Reports nest (a newspaper reports that
a CEO denied that p) and are read both ways. Forward, a report is evidence about
Y. Backward, once Y is settled by *independent* sources, it is evidence about X:
a source's track record. Because statements are dated, a source is judged by
what was true when it spoke, not by what changed later.

**Every step is checkable, and a failure has one address.** Each atom carries an
ordered trace — quote → report chain → subject → time → statement — where each
step is *ok*, *open* (a gap in the source, correctly left unbound) or a *defect*
(an error by the atomiser). Errors are charged to whoever made them: a misread
quote is never held against the source.

**Four values, not two.** `TRUE` · `FALSE` (checked, positively absent) ·
`UNKNOWN` (answerable by spending more) · `UNKNOWABLE` (no connected source
covers it). Collapsing the middle two is how a coverage hole renders as a clean
check.

## Research core

| path | what |
|---|---|
| `src/gleipnir/atomiser.py` | the atom contract: nested reports, closed claims, the deterministic checker and `trace()` |
| `src/gleipnir/ledger.py` | source track records from resolved reports: independent sources only, repeats count once |
| `src/gleipnir/research.py` | the `gleipnir.research/1` output: sources, exact passages, atoms with world time, assessments |
| `src/gleipnir/alignment.py` | does a source passage support an atom? model- or classifier-backed, never a truth claim |
| `src/gleipnir/pretrained.py` | offline NLI baseline for alignment (ONNX, CPU, pinned artifacts) |

An atom closes only when every part is bound:

- **subject** — a rigid identifier (company number, LEI, DOI, arXiv id, exact
  model version), never a name;
- **time** — stated in the passage, or bounded by the source's own date;
- **report chain** — every speaker named in the passage, denials as a verb
  (`denies`), and anything the atomiser concludes itself as `infers`, which
  never closes;
- **statement** — no words tied to an implicit now, negation and hedges kept.

The report and the claim are checked separately. "Paper X reports that method M
over-filters" can be a verified fact about X (the quote is in X) while the claim
about M stays open because M has no identifier yet.

**First pilot (2026-09-22)**, with a model atomiser working blind from the
contract, scored in code:

| set | result |
|---|---|
| 37 adversarial, fictional passages (prompt injection, fabricated ids and dates, nested denials, ambiguous dates, name collisions) | 37 pass, 0 wrongly closed |
| 97 atoms from 40 passages of 19 arXiv papers | report verified for 94; claim closed for 4 |

Most claims stay open on the subject: methods and models have no rigid
identifier yet. That is the next step, followed by matching the same claim
across sources so the ledger can produce its first track records. The pilot
also found more bugs in the checker than in the model, which the trace made
easy to tell apart.

## Corporate screening

### What it does

```
Identifier → Goals → Agenda → Acquire → Raw store → Extract → Project → Evaluate → Report
                ↑                                                    │
                └────────── a new UNKNOWN that could still flip a goal ┘
```

The feedback edge is the design. A predicate returning `UNKNOWN` names the source
that would settle it, and the engine goes and gets it. It halts when every
remaining need is satisfied, unknowable, or cannot change a verdict.

```
.venv/bin/python scripts/gcli.py screen <CVR> --as-of=<YYYY-MM-DD>
```

Reads the raw store; spends registry quota only up to `--budget` (default 0).

### What it will and will not say

**Red requires direct documented involvement** — a designation or watchlist hit,
adjudicated fraud, litigation by a counterparty, or explicit public support.
Structure never colours anything, however unusual it looks.

**Every fact arrives with its denominator.** A fact is shown alongside its
measured rate in the reference population. A fact with no measured comparator
prints "no measured comparator" rather than implying rarity.

### Layout

| path | what |
|---|---|
| `src/gleipnir/rawstore.py` | content-hashed, append-only; a parser fix is a reparse, not a refetch |
| `src/gleipnir/claims.py` | the atom: one source asserting one thing, bitemporal, never merged |
| `src/gleipnir/extract/` | pure functions, payload → claims; `contract.py` fails on any field silently dropped |
| `src/gleipnir/chain.py` | ownership expansion, path-product across every acyclic route |
| `src/gleipnir/contradict.py` | self-declared claims paired against registry claims |
| `src/gleipnir/predicates/` | four-valued, against measured and stratified base rates |
| `src/gleipnir/backtest.py` | outcome backtest: as-of evaluation, matched controls, no hindsight |
| `src/gleipnir/narrative.py` | gaps: where the account requires something no source shows |
| `src/gleipnir/finding.py` | the output model; red needs a ground, an authority and a citation |
| `src/gleipnir/oracle.py` | the only place a language model touches the system |
| `src/gleipnir/agents.py` | who may write what — deny-by-default, and enforced over the table |
| `src/gleipnir/analyst.py` | human judgement, scoped and pinned to the documents it was made against |
| `src/gleipnir/factsheet.py` | one company at one date, facts only, rendered by CLI and web alike |
| `src/gleipnir/adapters/` | source adapters: company registers, patents, research affiliation, sanctions, DNS |
| `src/gleipnir/cv.py` | every dated position one person held; a foreign entry licenses the next register |
| `src/gleipnir/attestation.py` | what a human read on a profile or document, as claims rather than judgement |
| `src/gleipnir/institutions.py` | designations (may ground a finding) and assessments (never may) |
| `src/gleipnir/jsonstream.py` | streams large register files in bounded memory |
| `src/gleipnir/web/` | the analyst workbench — HTTP over the same library the CLI calls |

### Backtest

Predicates are measured against outcomes the register records, not against
intuition. Every predicate is evaluated **before the transition** and against
controls matched on legal form, age band and owner count.

```
.venv/bin/python scripts/cohort_scan.py     # builds the cohorts
.venv/bin/python scripts/backtest.py --sweep
```

Three matching arms are printed side by side. A predicate whose separation
disappears under matching was measuring the matched variable, not the outcome.

### Factsheet

```
.venv/bin/python scripts/factsheet.py <CVR>
```

Renders one company at one date, using the booleans only to sort lines into
sections — "established", "checked, positively absent", "no connected source
covers it". A sheet that rendered blanks as clean would be lying.

### The workbench

```
.venv/bin/python scripts/serve.py          # http://127.0.0.1:8000
```

Server-rendered HTML over the same library the CLI calls, so a page cannot show
a fact the terminal would not. Every line links to the bytes it was projected
from.

**It holds a registry budget of zero and no route raises it.** Its only writing
routes are the two human roles — the analyst's scoped, pinned judgement and the
reviewer's outcomes. Both append.

### Who may do what

`src/gleipnir/agents.py` states, as an enforced table, what each party acting on
the system may write. Two questions are answered by construction: **who can put
a colour on a company** — the rule table, and nothing else — and **who can spend
registry quota** — the acquirer, and only against the source named on a need the
planner derived. No model-backed role may hold either, and that is asserted over
the whole roster.

### Gathering facts, then expanding on them

```
.venv/bin/python scripts/resolve.py <CVR> --budget=<N>
```

`plan.py` derives the agenda; executing it discovers facts; the new facts derive
the next agenda. An entry outside the home jurisdiction names the register that
could resolve it.

**The country is read, never inferred.** A source that does not say where an
entity sits yields an entry that is neither foreign nor domestic — a question,
not a trigger. **A foreign entry is an agenda item and never a colour.**

## Running

```
uv venv && uv pip install -e '.[dev,web]'
.venv/bin/python -m pytest -q
```

Corpus tests skip automatically without an ingested raw store.

## Data

`raw/` and `.env` are not tracked. The raw store can hold registry documents
naming private individuals; it never leaves the machine it was ingested on.

## Research manifests and alignment

The first domain-independent research slice accepts a curated JSON manifest,
verifies its stored evidence, appends a versioned snapshot, and exports a
Markdown research note:

```
.venv/bin/python scripts/research.py <manifest.json> --store <raw-store-directory> --save --draft --markdown <report.md>
```

`src/gleipnir/research.py` defines the `gleipnir.research/1` contract: questions,
source versions, exact passages, atomic evidence proposals, attributed evidence
assessments, synthesis, unresolved questions, and limitations. Sources carry
origin groups so copies or related publications need not be counted as
independent corroboration. Offsets refer to the module's versioned UTF-8 text
representation. HTML and plain text are supported; PDF ingestion is not yet
implemented in this path.

Source payloads must already be in `RawStore`, with a successful fetch record
whose resource ID matches the source URL. The command runs offline. It checks
hashes, source attribution, passage bounds and all evidence references before
saving or exporting. Corrections create new content-addressed snapshots.

**A matching quotation does not prove an interpretation.** Assessments and
synthesis remain attributed proposals; the validator explicitly reports
`semantic_truth_verified: false`. This path does not emit corporate claims,
colours, or screening predicates. Acquisition, atomisation and assessment are
currently operator/assistant-led; an autonomous research loop and model-provider
integration remain future work.

The local fourth-spatial-dimension case exercises this path with five source
versions. Its research files and raw payloads remain local under the existing
ignore rules. Run the reusable contract checks with:

```
.venv/bin/python -m pytest -q tests/test_research.py
```

### Source-to-atom alignment

`gleipnir.alignment` adds an [AlignScore-inspired](https://aclanthology.org/2023.acl-long.634/)
context/claim interface. It retrieves the quoted passage and up to 1,200
characters on either side from the verified source, bounded at 6,000 characters.
Clipped context is explicit. A separate model call checks whether each candidate
is one truth-evaluable proposition and preserves negation, uncertainty, scope,
attribution, time, quantities and surrounding context. It receives neither the
generator's reasoning nor the proposed conclusion. Candidate atomization should
call this stage before using an atom as evidence for synthesis.

The LLM backend uses the existing oracle boundary and optional Anthropic SDK.
Choose a supported model explicitly; set `ANTHROPIC_API_KEY` in the environment:

```
.venv/bin/python scripts/research.py <manifest.json> --store <raw-store-directory> --alignment-model <model-id> --max-alignment-calls 10 --alignment-output <alignment.json> --markdown <report.md>
```

The call budget defaults to zero; SDK retries are disabled. Each completed pair
is saved immediately. Repeating the command reuses judgments pinned to the atom,
qualification, quote, source version, exact context, model and method version.
Use a versioned model identifier when available. Increasing the budget resumes
missing work. A prompt/method change requires a version bump. Provider errors or
malformed outputs remain pending rather than becoming positive evidence.

Offline replay is also available:

```
.venv/bin/python scripts/research.py <manifest.json> --store <raw-store-directory> --alignment-report <alignment.json> --markdown <report.md>
```

Normal Markdown export requires eligible cited atoms. `--draft` permits explicit
unfinished exports; raw research snapshots may always retain unfinished work.
An atom is eligible only when every attached passage supports it, all semantic
checks pass, and the support score meets the routing threshold (default 0.8).
This threshold is experimental, not calibrated confidence. Multiple-source
combination, synthesis reasoning and source truth remain outside this gate.
Scores are not averaged: one contradicted or unchecked passage blocks the atom.
Replayed reports must match the stored assessment records and current inputs.

`AlignScoreBackend` accepts an upstream scorer with the documented
`score(contexts=[...], claims=[...])` interface and an explicit checkpoint/version
identity. It retains the scalar as a diagnostic. A scalar alone cannot establish
atomicity, context sufficiency, or distinguish contradiction from absent support;
it cannot pass the full gate by itself. The upstream model and its heavyweight
dependencies are optional and are not installed by this change. This implementation
does not train or reproduce AlignScore's neural architecture.

The 16 synthetic development cases in `tests/fixtures/alignment_cases.json`
include faithful paraphrases and corrupted negation, modality, scope, attribution,
context, time, quantities and atomicity. Labels are assistant-authored and still
need independent review. Measure a selected backend with:

```
.venv/bin/python scripts/evaluate_alignment.py --backend llm --model <model-id> --max-calls 16 --output <evaluation.json>
```

The evaluator reports false acceptance of corrupted atoms, false rejection of
faithful atoms, and unassessed cases separately. Unit tests use stubs to test
routing and provenance; they do not measure semantic model accuracy. A zero-budget
run reports pending cases and no accuracy estimate when the cache is empty.

### Pretrained-first baseline

Start with an existing model; custom Gleipnir training is deferred until measured
failures on independently reviewed cases justify it. The first runnable baseline
is [cross-encoder/nli-MiniLM2-L6-H768](https://huggingface.co/cross-encoder/nli-MiniLM2-L6-H768),
a pretrained natural-language inference classifier, **not the AlignScore model**.
The original AlignScore package pins Torch <2, incompatible with this project's
Python >=3.12 environment. The baseline uses ONNX Runtime on CPU, with no API key,
training, GPU requirement, or downloaded executable model code.

Install the optional dependencies, download the pinned artifacts, and evaluate:

```
uv pip install -e '.[nli]'
.venv/bin/python scripts/prepare_nli.py
.venv/bin/python scripts/evaluate_alignment.py --max-calls 16 --output research/pretrained-evaluation.json
```

The default evaluation backend is now `pretrained`; use `--backend llm --model
<model-id>` for the existing LLM assessor. Model files live under ignored `raw/`.
The repository revision and SHA-256 of the graph, tokenizer and configuration are
pinned in `gleipnir.pretrained`. Inference is offline after preparation; it uses
two CPU threads. The pretrained path requests 600 characters on each side of
the quote (LLM default: 1,200); `--context-margin` overrides this explicitly.
The chosen margin, actual offsets and clipping flags are recorded and checked
on replay. Each source-context/statement pair must fit 512 tokens. Longer
pairs remain pending with `ContextWindowExceeded`, never silently truncated.

To score a research case:

```
.venv/bin/python scripts/research.py <manifest.json> --store <raw-store-directory> --pretrained --max-alignment-calls 10 --alignment-output <alignment.json>
```

This classifier distinguishes support, contradiction, and neutrality. It does not
certify atomicity, contextual completeness, or qualification preservation, so its
scores alone cannot pass the full export gate. Retain LLM/human assessment for
those tasks. The evaluation separates source-support labels from full atom
eligibility: a compound proposition can be supported while failing atomicity.
Unassessed semantic checks are not counted as classifier errors or successes.

Initial local pilot (16 synthetic development examples, threshold 0.8): all 7
source-supported cases passed; 7 of 8 unsupported cases were rejected. One
interrogative was excluded from source-support scoring. The model incorrectly
accepted an uncertainty-strengthening change from “may indicate” to “establishes”.
The run took approximately 3.3 seconds for inference including lazy model loading
on the available CPU. This is a single-run measurement, not a throughput guarantee.
No threshold was fitted to these examples and no weights were trained. These
assistant-authored cases need independent review and expansion before reliability
claims or a training decision.

The real fourth-spatial-dimension pilot is less encouraging: all five atoms fit
with the explicit 600-character context margin, but the classifier returned
insufficient support for all five. These are model outputs, not findings that
the sources are false. Attribution and context presentation need investigation
alongside model suitability. The initial 1,200-character windows exceeded the
model's token capacity and were correctly left pending. Both runs are retained
locally. The pretrained baseline is experimental and does not replace contextual
LLM/human assessment on the strength of the synthetic results.
