"""The resolution planner — the knowledge structure drives acquisition.

Predicates evaluate whatever data happens to be present. That is the wrong way
round: what the system needs is for the *goal* to decide which source to hit
next. This module is that layer.

A goal decomposes into needs. A need that is unsatisfied and could change the
goal's verdict becomes a typed action against a named source. The engine runs
evaluate → collect unsatisfied needs → act → re-evaluate, until no remaining
action could change any conclusion, or the budget is spent.

Three properties this buys, and each is why it is a rule engine rather than a
prompt:

**The agenda is derived, not judged.** MYCIN asked the physician for a lab value
only when that value could change a conclusion. Same here: an action is only
generated when the need it satisfies gates a goal that is still open.

**The stopping rule is deterministic.** Halt when every remaining need is
`UNKNOWABLE`, or satisfied, or cannot flip a goal. No budget heuristic, no model
deciding it has read enough.

**The LLM shrinks to the leaves.** `architecture.md` §7.1's invariant survives
intact: the engine decides what to look up; the model only answers bounded
pairwise questions the engine cannot — is this the same person, is this the same
company — one pair, one typed answer, cached.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum
from typing import Callable


class Act(StrEnum):
    FETCH = "fetch"          # get a document from a source
    EXPAND = "expand"        # walk one more hop of a chain
    RESOLVE = "resolve"      # a bounded pairwise question for the oracle
    ESCALATE = "escalate"    # a human, or a question for the counterparty
    NONE = "none"


@dataclass(frozen=True)
class Need:
    """One thing a goal requires in order to be decidable."""

    key: str                 # 'ownership_chain', 'website', 'accounts', ...
    subject: str             # entity the need is about
    source: str              # which adapter can answer it
    act: Act
    cost: int                # API calls, so the planner can order by price
    #: Which goal wants it, and why — printed in the agenda so a human can see
    #: the derivation rather than a bare list of URLs to fetch.
    because: str


@dataclass(frozen=True)
class Goal:
    """A question the screen is trying to close.

    `needs` returns what is still missing given the current state. Returning an
    empty list means the goal is decidable now — which is the halt condition,
    computed rather than guessed.
    """

    name: str
    question: str
    needs: Callable[["State"], list[Need]]
    #: Goals that must be closed before this one is worth pursuing. A goal whose
    #: prerequisite came back UNKNOWABLE is not asked at all — that is how a
    #: coverage limit propagates instead of becoming a false negative.
    after: tuple[str, ...] = ()


@dataclass
class State:
    """What is known right now. Deliberately dumb — the planner reads it, the
    executors write it."""

    root: str
    as_of: date
    have: dict[str, object] = field(default_factory=dict)
    unknowable: set[str] = field(default_factory=set)
    spent: int = 0

    def has(self, key: str, subject: str | None = None) -> bool:
        """Do we actually hold this?

        **False for an abandoned need.** `give_up` records the absence so the
        store does not lie by omission, but a goal asking `if not s.has(k)` must
        still see that it wants k — otherwise "we gave up" reads as "we have
        it", the goal reports itself decidable, and the difference between
        *closed* and *blocked* silently collapses. The agenda filters dead needs
        separately, so nothing is queued for it either way.
        """
        k = f"{key}:{subject or self.root}"
        return k in self.have and k not in self.unknowable

    def mark(self, key: str, subject: str, value: object) -> None:
        self.have[f"{key}:{subject}"] = value

    def give_up(self, key: str, subject: str, why: str) -> None:
        """Record that a need cannot be met from any connected source.

        Distinct from "not fetched yet": an UNKNOWABLE need must stop generating
        actions forever, or the planner loops on it and the report renders a
        coverage hole as an open question.
        """
        self.unknowable.add(f"{key}:{subject}")
        self.have[f"{key}:{subject}"] = None

    def dead(self, key: str, subject: str | None = None) -> bool:
        """Was this abandoned? Distinct from absent, and from held."""
        return f"{key}:{subject or self.root}" in self.unknowable

    def recorded(self, key: str, subject: str | None = None) -> bool:
        """Did we reach a conclusion about this at all — held OR abandoned?"""
        return f"{key}:{subject or self.root}" in self.have


def is_blocked(goal: Goal, state: State) -> bool:
    """The goal still wants things, and every one of them is unknowable.

    Distinct from "closed": a closed goal has no outstanding needs and is
    decidable. A blocked goal is undecidable and will stay that way, so
    anything depending on it must be dropped rather than asked — that is how a
    coverage limit propagates instead of becoming a false negative.
    """
    needs = goal.needs(state)
    return bool(needs) and all(state.dead(n.key, n.subject) for n in needs)


def agenda(goals: list[Goal], state: State) -> list[Need]:
    """Every action worth taking now, cheapest first.

    Ordering by cost is not a nicety: the cheap needs are often the ones that
    make the expensive ones unnecessary. Reading a website costs nothing and can
    close a question that would otherwise cost ten registry calls to answer
    structurally.
    """
    by_name = {g.name: g for g in goals}
    blocked = {name for name, g in by_name.items() if is_blocked(g, state)}
    out: list[Need] = []
    seen: set[tuple[str, str]] = set()
    for g in sorted(goals, key=lambda g: g.name):
        if any(a in blocked for a in g.after):
            continue                     # prerequisite is unknowable: drop the goal
        for n in g.needs(state):
            if state.dead(n.key, n.subject):
                continue
            k = (n.key, n.subject)
            if k in seen:
                continue
            seen.add(k)
            out.append(n)
    return sorted(out, key=lambda n: (n.cost, n.key, n.subject))


def run(goals: list[Goal], state: State,
        execute: Callable[[Need, State], bool],
        *, budget: int = 25, max_rounds: int = 8) -> list[Need]:
    """Drive to fixpoint. Returns the needs left unmet.

    `execute` performs one need and returns whether it changed the state. A need
    that executes without changing anything is marked unknowable rather than
    retried — otherwise a source that answers 404 forever keeps its slot in the
    agenda.
    """
    for _ in range(max_rounds):
        todo = agenda(goals, state)
        if not todo:
            return []
        progressed = False
        for need in todo:
            if state.spent + need.cost > budget:
                continue
            before = state.spent
            if execute(need, state):
                progressed = True
            else:
                state.give_up(need.key, need.subject, "source returned nothing")
            state.spent = max(state.spent, before)
        if not progressed:
            break
    return agenda(goals, state)
