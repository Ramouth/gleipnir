"""The rule layer: booleans in, flags out — and the named question when neither.

The predicates are fodder. On their own they are facts with denominators, and
`finding.py` refuses to let a fact become a colour: red needs a `RedGround`
plus an authority and a citation, and no CVR boolean can supply an
adjudication record. That refusal is correct and it left a hole — a fired
boolean simply sat in a list, and nothing said *what would have to be true* for
it to become amber or red.

This module closes it with a declarative table. Every predicate that can fire
appears exactly once, with one of three dispositions:

    RAISE      the boolean IS documented involvement -> a Finding, with ground
    ESCALATE   the boolean cannot colour, but names the source that could,
               and the bounded question to put to it
    FACT       rated, ranked by measured LR, and may never become a colour

**The table is where the LLM's room to manoeuvre ends.** `architecture.md` §7.1
keeps the model at the leaves — is this the same person, is this the same
company. An `ESCALATE` row is exactly that leaf: it names the source, the
question, and what answer would change the disposition. The model answers the
question; the rule assigns the colour. A model that could colour directly would
make the whole four-valued apparatus decorative.

**No rule may raise red.** Enforced, not documented — `test_flags.py` asserts it
over the whole table. Six iterations of measurement produced not one predicate
that constitutes direct involvement, so a red rule here would be drift.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from gleipnir.finding import AMBER_QUALIFIERS, Colour, RedGround
from gleipnir.predicates.core import Result, V


class Disposition(StrEnum):
    RAISE = "raise"          # becomes a Finding
    ESCALATE = "escalate"    # names the source and question that could colour it
    FACT = "fact"            # rated and ranked; may never become a colour


@dataclass(frozen=True)
class Rule:
    predicate: str
    on: V                       # the value that triggers this rule
    disposition: Disposition
    #: RAISE only. The ground the finding carries.
    ground: RedGround | None = None
    colour: Colour | None = None
    qualifier: str = ""
    #: ESCALATE only. The source to ask, the question to ask it, and the answer
    #: that would change the disposition. All three, or the row is not a task.
    source: str = ""
    question: str = ""
    would_change: str = ""
    #: FACT only. Why it may never colour.
    holds_as: str = ""


#: Closed table. Adding a row is a deliberate change with a measurement behind
#: it, never a call-site decision.
RULES: tuple[Rule, ...] = (
    # ── documented involvement: the only thing that may raise a finding ──────
    Rule("designated_holder_candidate", V.TRUE, Disposition.RAISE,
         ground=RedGround.DESIGNATION, colour=Colour.AMBER,
         qualifier="unadjudicated_name_match"),
    Rule("designated_in_chain", V.TRUE, Disposition.RAISE,
         ground=RedGround.DESIGNATION, colour=Colour.AMBER,
         qualifier="unadjudicated_name_match"),

    # ── the boolean names its next source ────────────────────────────────────
    Rule("majority_owner_unresolvable", V.TRUE, Disposition.ESCALATE,
         source="gleif",
         question="does this holder have an active LEI, and a Level 2 parent?",
         would_change="a Level 2 parent places it inside a documented group and "
                      "the chain continues; a bare LEI locates it and does NOT "
                      "suppress; no LEI leaves the chain terminated here"),
    Rule("chain_terminates_unresolvable", V.TRUE, Disposition.ESCALATE,
         source="gleif",
         question="does the terminal node resolve to an LEI in any jurisdiction?",
         would_change="an LEI continues the chain past CVR's edge; nothing found "
                      "makes the termination itself the reportable fact"),
    Rule("dissolution_threat_on_file", V.TRUE, Disposition.ESCALATE,
         source="cvr",
         question="on what ground was the threat issued, and was it lifted?",
         would_change="lifted and the company filed is an administrative lapse "
                      "corrected; still in force is the registrar's own dated "
                      "act against the entity (5.0x [1.9, 13.0] a year before "
                      "bankruptcy, 4.8x at five years)"),
    Rule("registered_audit_election_absent", V.TRUE, Disposition.ESCALATE,
         source="regnskaber",
         question="has this company ever filed an annual report?",
         would_change="never filed makes this an administrative-death marker "
                      "(32.9x struck off, 53.0x bankrupt) and nothing more; "
                      "filed but with no election is unexplained and stays open"),
    Rule("chain_terminates_unresolvable", V.UNKNOWN, Disposition.ESCALATE,
         source="cvr",
         question="what lies beyond the nodes left unexpanded by OUR budget?",
         would_change="raising max_depth answers it — this is a spending "
                      "decision, not a coverage limit"),

    # ── rated, and may never become a colour ─────────────────────────────────
    Rule("ownership_register_events", V.TRUE, Disposition.FACT,
         holds_as="churn in the ownership register. 1.62x [1.49, 1.76] before "
                  "bankruptcy, decaying to 1.43x at five years — it reads the "
                  "run-up, and a run-up is not involvement"),
    Rule("signing_rule_changed", V.TRUE, Disposition.FACT,
         holds_as="1.63x [1.39, 1.92] before bankruptcy. A company may change "
                  "who signs for it for any reason"),
    Rule("audit_waived", V.TRUE, Disposition.FACT,
         holds_as="1.72x [1.58, 1.87] before forced dissolution and 1.03x "
                  "before bankruptcy. Waiving audit is lawful and ordinary"),
    Rule("voting_exceeds_equity", V.TRUE, Disposition.FACT,
         holds_as="the threat-model.md §2.2 '49% plus control' limb, and it "
                  "fires LESS on both outcome cohorts than on matched controls "
                  "(0.59x, 0.50x). Reported, and NOT evidence toward failure"),
    Rule("capital_change_same_day_as_ownership", V.TRUE, Disposition.FACT,
         holds_as="one transaction recorded in two registers. 1.59x unmatched "
                  "and 0.93x matched — the apparent signal was legal form and "
                  "age, and on most companies it is the founding date"),
    Rule("ownership_residual_unaccounted", V.TRUE, Disposition.FACT,
         holds_as="structural — 1.3% at one owner, 82.2% at six. It restates "
                  "the size of the cap table"),
    Rule("subthreshold_aggregate_over_50", V.UNKNOWN, Disposition.FACT,
         holds_as="structural — 0.0% at one owner, 91.4% at six, and whether "
                  "the holders are associated is the person-resolution question "
                  "this predicate cannot answer"),
    Rule("unusual_ownership_percentage", V.TRUE, Disposition.FACT,
         holds_as="~0.06% of 35,071 filed percentages. Rare is not suspicious: "
                  "an odd percentage is what a negotiated cap table looks like"),
    Rule("nominee_density", V.TRUE, Disposition.FACT,
         holds_as="a director holding 100+ directorships, infrastructure "
                  "excluded. Company-level base rate 0.94%"),
    Rule("insolvency_or_dissolution", V.TRUE, Disposition.FACT,
         holds_as="the registry's own lifecycle state. It is the label the "
                  "outcome cohorts were defined on, so it has no denominator "
                  "and predicts nothing"),
    Rule("effective_ownership_over_50", V.TRUE, Disposition.FACT,
         holds_as="the Tier A legal test, computed. Who controls a company is "
                  "a fact about the company, not an allegation about them"),
    Rule("dilution_masks_control", V.TRUE, Disposition.FACT,
         holds_as="no direct holder over 50% yet an indirect one above it. "
                  "Ordinary in any group with a holding company"),
    Rule("ultimate_owners_are_named_persons", V.TRUE, Disposition.FACT,
         holds_as="green datum — every route ends at a named natural person"),
)

_BY_KEY = {(r.predicate, r.on): r for r in RULES}


def rule_for(result: Result) -> Rule | None:
    """The one rule matching this result, or None if the value does not trigger.

    Deliberately keyed on (predicate, value): `chain_terminates_unresolvable`
    dispatches differently on TRUE and UNKNOWN, because a chain stopped by the
    structure and a chain stopped by our budget are not the same statement and
    conflating them reports a spending decision as a coverage limit.
    """
    return _BY_KEY.get((result.predicate, result.value))


def apply(results: list[Result]) -> dict[Disposition, list[tuple[Rule, Result]]]:
    """Sort fired booleans into the three dispositions. Deterministic.

    A fired predicate with no row is returned under `UNGOVERNED` so it is
    visible rather than silently dropped — a boolean the table does not cover
    is a gap in the table, and hiding it is how a rule layer rots.
    """
    out: dict[Disposition, list[tuple[Rule, Result]]] = {d: [] for d in Disposition}
    out[UNGOVERNED] = []
    for r in sorted(results, key=lambda r: r.predicate):
        if r.value in (V.FALSE, V.UNKNOWABLE):
            continue
        rule = rule_for(r)
        if rule is None:
            out[UNGOVERNED].append((None, r))
        else:
            out[rule.disposition].append((rule, r))
    return out


#: Not a Disposition: it is the absence of one.
UNGOVERNED = "ungoverned"


def agenda(results: list[Result]) -> list[str]:
    """The bounded questions the fired booleans generate, in source order.

    This is the whole of the model's remit. It answers these; it does not
    decide what they mean.
    """
    return [f"{rule.source}: {rule.question}"
            for rule, _ in apply(results)[Disposition.ESCALATE]]
