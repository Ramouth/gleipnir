# Gleipnir

Screening for concealed ownership and control of corporate assets.

One company identifier in; a chain of comparable facts and a short list of
findings out. The system parses sources into knowledge that is **operational and
comparable**. It does not conclude — a human supplies the judgement at the end.

## What it does

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

## What it will and will not say

**Red requires direct documented involvement** — a designation or watchlist hit,
adjudicated fraud, litigation by a counterparty, or explicit public support.
Structure never colours anything, however unusual it looks.

**Every fact arrives with its denominator.** A fact is shown alongside its
measured rate in the reference population. A fact with no measured comparator
prints "no measured comparator" rather than implying rarity.

**Four values, not two.** `TRUE` · `FALSE` (checked, positively absent) ·
`UNKNOWN` (answerable by spending more) · `UNKNOWABLE` (no connected source
covers it). Collapsing the middle two is how a coverage hole renders as a clean
check.

## Layout

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

## Backtest

Predicates are measured against outcomes the register records, not against
intuition. Every predicate is evaluated **before the transition** and against
controls matched on legal form, age band and owner count.

```
.venv/bin/python scripts/cohort_scan.py     # builds the cohorts
.venv/bin/python scripts/backtest.py --sweep
```

Three matching arms are printed side by side. A predicate whose separation
disappears under matching was measuring the matched variable, not the outcome.

## Factsheet

```
.venv/bin/python scripts/factsheet.py <CVR>
```

Renders one company at one date, using the booleans only to sort lines into
sections — "established", "checked, positively absent", "no connected source
covers it". A sheet that rendered blanks as clean would be lying.

## The workbench

```
.venv/bin/python scripts/serve.py          # http://127.0.0.1:8000
```

Server-rendered HTML over the same library the CLI calls, so a page cannot show
a fact the terminal would not. Every line links to the bytes it was projected
from.

**It holds a registry budget of zero and no route raises it.** Its only writing
routes are the two human roles — the analyst's scoped, pinned judgement and the
reviewer's outcomes. Both append.

## Who may do what

`src/gleipnir/agents.py` states, as an enforced table, what each party acting on
the system may write. Two questions are answered by construction: **who can put
a colour on a company** — the rule table, and nothing else — and **who can spend
registry quota** — the acquirer, and only against the source named on a need the
planner derived. No model-backed role may hold either, and that is asserted over
the whole roster.

## Gathering facts, then expanding on them

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
