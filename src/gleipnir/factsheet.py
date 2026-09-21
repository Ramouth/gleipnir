"""One company, one date, facts only — assembled once, rendered anywhere.

`scripts/factsheet.py` printed this to a terminal, and the workbench needed the
same thing in HTML. The tables that make it a factsheet rather than a dump —
which question each predicate answers, which heading a four-valued result lands
under, what the measured comparator says — are the content, and a second copy of
them behind a template is how two surfaces start disagreeing about what the
register says.

So the assembly lives here and returns a :class:`Sheet`. Both surfaces are
projections of it, and neither can invent a line the other does not have.

**The booleans are scaffolding.** `TRUE`/`FALSE`/`UNKNOWN`/`UNKNOWABLE` decide
which section a line lands in and do not themselves appear: they become
"established", "checked, positively absent", "answerable by spending more" and
"no connected source covers it". What a reader sees is the register's own facts,
each with the denominator that makes it information.

No score. No ranking. No adjective. The sheet does not say what the facts mean.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any

from gleipnir.backtest import LABEL_PREDICATES, subject_from_hit
from gleipnir.calibration import OUTCOME_LEAD_SWEEP, OUTCOME_LR_365, OUTCOME_REJECTED
from gleipnir.extract.cvr import extract_company
from gleipnir.flags import UNGOVERNED, Disposition, Rule, apply
from gleipnir.predicates.core import Result, V
from gleipnir.predicates.cvr_only import ALL_CVR_ONLY, designated_holder

#: The question each predicate answers, in words. Without it the absent-answer
#: sections read as a list of reasons with nothing attached to them — three
#: lines saying "no quantified holdings filed" and no way to tell which three
#: questions went unanswered.
QUESTIONS: dict[str, str] = {
    "subthreshold_aggregate_over_50": "whether holders under 50% together exceed it",
    "voting_exceeds_equity": "whether any holder's votes exceed their equity",
    "ownership_residual_unaccounted": "how much of the cap table is unaccounted for",
    "signing_rule_changed": "whether the signing rule has ever changed",
    "capital_change_same_day_as_ownership":
        "whether capital and ownership changed on the same day",
    "ownership_register_events": "how often the ownership register changed",
    "audit_waived": "whether audit was waived",
    "insolvency_or_dissolution": "the composite lifecycle status",
    "unusual_ownership_percentage": "whether any filed percentage is a non-round value",
    "majority_owner_unresolvable":
        "whether a majority holder sits outside the Danish register",
    "registered_audit_election_absent": "whether an audit election was ever filed",
    "nominee_density": "whether a director holds an implausible number of directorships",
    "designated_holder_candidate": "whether a filed holder matches the designation list",
}

OUTCOME_PHRASE = {
    "bankruptcy": "that went bankrupt",
    "forced-dissolution": "that were struck off",
}

SECTIONS: dict[V, str] = {
    V.TRUE: "ESTABLISHED",
    V.UNKNOWN: "ANSWERABLE BY SPENDING MORE — a research task, not a clean result",
    V.FALSE: "CHECKED, POSITIVELY ABSENT",
    V.UNKNOWABLE: "NO CONNECTED SOURCE COVERS IT — not the same as clean",
}

#: Denmark's ownership register opened in June 2015. Read before then, a company
#: with no filed owners is a company the register did not yet ask about — and a
#: sheet that rendered that silence as absence would be lying.
REGISTER_OPENED = date(2015, 6, 1)
REGISTER_NOTE = (
    "Denmark's ownership register opened in June 2015. Read before then, a "
    "company with no filed owners is a company the register did not yet ask "
    "about.")


def comparator(predicate: str) -> list[str]:
    """The measured denominator for one fact, or the statement that it has none."""
    if predicate in LABEL_PREDICATES:
        return ["no denominator: this is the field the outcome cohorts were defined on"]
    rows = OUTCOME_LR_365.get(predicate)
    if not rows:
        why = OUTCOME_REJECTED.get(predicate)
        return ["no measured comparator" + (f": {why}" if why else "")]
    out = []
    for outcome in sorted(rows):
        a, n1, c, n2, ratio, lo, hi = rows[outcome]
        sweep = OUTCOME_LEAD_SWEEP.get(predicate, {}).get(outcome, {})
        trail = ", ".join(f"{d // 365}y {v:.1f}x" for d, v in sorted(sweep.items()))
        out.append(f"seen in {c / n2:.1%} of matched active companies and "
                   f"{a / n1:.1%} of companies {OUTCOME_PHRASE[outcome]} a year later "
                   f"— {ratio:.1f}x [{lo:.1f}, {hi:.1f}]; by lead {trail}")
    return out


def question_for(predicate: str) -> str:
    """What this predicate answers, in words. Falls back to its own name so a
    new predicate appears as itself rather than vanishing."""
    return QUESTIONS.get(predicate, predicate)


@dataclass(frozen=True)
class Row:
    """One predicate, as a line on the sheet."""

    predicate: str
    question: str
    evidence: str
    raw: Any
    value: V
    #: Populated for established facts only — a comparator on an absent answer
    #: would be a rate for something that did not happen.
    comparator: tuple[str, ...] = ()


@dataclass(frozen=True)
class Section:
    value: V
    heading: str
    rows: tuple[Row, ...]
    of_total: int

    @property
    def count(self) -> int:
        return len(self.rows)


@dataclass
class Sheet:
    """One company at one date: what the register holds, and what it does not."""

    cvr: str
    name: str
    legal_form: str
    founded: Any
    blob: str
    as_of: date
    #: Why this date and not another. Printed, because a sheet read at a date
    #: chosen by the tool has to say so.
    as_of_reason: str
    status_history: tuple[tuple[Any, Any, str], ...] = ()
    results: tuple[Result, ...] = ()
    sections: tuple[Section, ...] = ()
    raised: tuple[tuple[Rule, Result], ...] = ()
    escalate: tuple[tuple[Rule, Result], ...] = ()
    facts: tuple[tuple[Rule, Result], ...] = ()
    ungoverned: tuple[tuple[Any, Result], ...] = ()
    register_note: str = ""
    events: tuple[Any, ...] = field(default_factory=tuple)

    @property
    def blind(self) -> tuple[Result, ...]:
        return tuple(r for r in self.results if r.value is V.UNKNOWABLE)

    def status_at(self, on: date) -> str:
        for frm, to, value in self.status_history:
            if (frm is None or frm <= on) and (to is None or to >= on):
                return value
        return ""

    def covers(self, frm: Any, to: Any) -> bool:
        """Does this status period contain the as-of date? The marker in both
        renderings comes from here rather than from two copies of the test."""
        return ((frm is None or frm <= self.as_of)
                and (to is None or to >= self.as_of))


class NotInStore(LookupError):
    """The company has never been fetched. Distinct from having no facts."""


def unwrap_hit(doc: Any) -> Any:
    """The single company hit inside whatever the store holds.

    Three shapes are on file, because the adapter has been written more than
    once: a bare hit, a list of hits, and the whole Elasticsearch response
    envelope. 19 of 329 cached companies are envelopes, and a reader that
    handles only the first two reports them as *not in the raw store* — a
    parser gap rendering as a coverage statement, which is the inversion this
    project exists to prevent. The store is append-only and immutable, so the
    old shapes are permanent and the reader accommodates them.
    """
    if isinstance(doc, list):
        return doc[0] if doc else None
    if isinstance(doc, dict) and "hits" in doc:
        inner = (doc.get("hits") or {}).get("hits") or []
        return inner[0] if inner else None
    return doc


def resolve(store, cvr: str):
    """The cached CVR document for one company, or (None, None, None)."""
    rec = store.latest("cvr", "virksomhed", cvr)
    if rec is None:
        return None, None, None
    hit = unwrap_hit(store.get_json(rec.content_hash))
    if hit is None:
        return None, None, None
    return hit, rec.content_hash, subject_from_hit(hit, "subject", rec.content_hash)


def choose_as_of(subject, as_of: date | None) -> tuple[date, str]:
    """The date to read at, and why.

    With none given, a company that later failed is read **365 days before its
    first adverse transition**, so nothing on the sheet is hindsight.
    """
    if as_of is not None:
        return as_of, "as specified"
    if subject.event:
        return (date.fromordinal(subject.event.toordinal() - 365),
                f"365 days before {subject.event_status} on {subject.event} — "
                f"nothing below is hindsight")
    return (datetime.now(timezone.utc).date(),
            "no adverse transition on file; read at today's date")


def build(store, cvr: str, index=None, as_of: date | None = None,
          directorships: dict[str, int] | None = None) -> Sheet:
    """Assemble one sheet. Reads the store; never fetches, never spends quota."""
    hit, blob, subject = resolve(store, cvr)
    if subject is None:
        raise NotInStore(cvr)

    on, why = choose_as_of(subject, as_of)
    claims = extract_company(hit, raw_ref=blob, observed_at=str(on))
    results = [p(claims, on) for p in ALL_CVR_ONLY]
    if index is not None:
        results.append(designated_holder(claims, index, on))

    sections = []
    for value, heading in SECTIONS.items():
        rows = tuple(
            Row(predicate=r.predicate, question=question_for(r.predicate),
                evidence=r.evidence, raw=r.raw, value=r.value,
                comparator=tuple(comparator(r.predicate)) if value is V.TRUE else ())
            for r in sorted((r for r in results if r.value is value),
                            key=lambda r: r.predicate))
        if rows:
            sections.append(Section(value=value, heading=heading, rows=rows,
                                    of_total=len(results)))

    governed = apply(results)
    blind = [r for r in results if r.value is V.UNKNOWABLE]
    note = (REGISTER_NOTE if on < REGISTER_OPENED and any(
        "ownership" in r.evidence or "holdings" in r.evidence for r in blind) else "")

    return Sheet(
        cvr=cvr, name=subject.name, legal_form=subject.legal_form,
        founded=subject.founded, blob=blob, as_of=on, as_of_reason=why,
        status_history=tuple(subject.status_history),
        results=tuple(results), sections=tuple(sections),
        raised=tuple(governed[Disposition.RAISE]),
        escalate=tuple(governed[Disposition.ESCALATE]),
        facts=tuple(governed[Disposition.FACT]),
        ungoverned=tuple(governed[UNGOVERNED]),
        register_note=note,
    )
