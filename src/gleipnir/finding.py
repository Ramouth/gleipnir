"""What a screen outputs: a chain of comparable facts, and a short list of findings.

Gleipnir parses sources into knowledge that is **operational and comparable**.
It does not conclude. The chain starts at a CVR number, expands outward from
that fact, and a human supplies the judgement at the end.

That has two structural consequences, and both are enforced here rather than
left to a report template.

**Colour is narrow, and requires direct documented involvement.** Red is not
something a structure can imply. It is reserved for grounds where a source has
recorded the entity's own involvement:

    a designation or watchlist hit on the entity or a person in it
    adjudicated fraud
    litigation brought by a counterparty
    explicit public support for an aggressor state

Six iterations of measurement kept concluding that structural predicates "fail"
as detectors. They were never detectors. Sub-threshold aggregation fires on 91%
of six-owner companies, and that is fine — it was never a finding, it is a fact
about the cap table. Miscasting chain facts as colours is what made them look
like failures.

**A chain fact carries its comparator.** "Mail infrastructure resolves to RU" is
not a verdict; it becomes information the moment it arrives with *0.0% of
ordinary Danish companies, 63.5% of designated entities*. Comparability is the
product. A fact printed without its denominator is the thing this system exists
not to do.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum
from typing import Any


class Colour(StrEnum):
    RED = "red"
    AMBER = "amber"
    GREEN = "green"
    #: Checked, and no connected source can answer. Never rendered as green.
    GREY = "grey"


class RedGround(StrEnum):
    """The closed set of grounds on which a finding may be red.

    Closed because the failure mode is drift: every structural signal eventually
    argues for its own promotion, and none of them are direct involvement.
    Adding a ground is a deliberate change, not a call-site decision.
    """

    DESIGNATION = "designation_or_watchlist"
    ADJUDICATED_FRAUD = "adjudicated_fraud"
    COUNTERPARTY_LITIGATION = "litigation_by_counterparty"
    PUBLIC_SUPPORT_FOR_AGGRESSOR = "explicit_public_support"


#: Amber is the same grounds, not yet adjudicated: reported, alleged, pending,
#: or resting on a name match no verdict has resolved.
AMBER_QUALIFIERS = ("alleged", "reported", "pending", "unadjudicated_name_match")


@dataclass(frozen=True)
class Comparator:
    """What this fact looks like in populations we have measured."""

    ordinary: float | None = None       # rate among ordinary Danish companies
    ordinary_n: int | None = None
    contrast: float | None = None       # rate in a contrast population
    contrast_label: str = ""
    contrast_n: int | None = None
    conditioned_on: str = ""            # the variable the rate is stratified by

    def line(self) -> str:
        if self.ordinary is None:
            return "no measured comparator — not admissible as a rated fact"
        bits = [f"{self.ordinary:.1%} of ordinary Danish companies"
                + (f" (n={self.ordinary_n:,})" if self.ordinary_n else "")]
        if self.contrast is not None:
            bits.append(f"{self.contrast:.1%} of {self.contrast_label}"
                        + (f" (n={self.contrast_n:,})" if self.contrast_n else ""))
        if self.conditioned_on:
            bits.append(f"conditioned on {self.conditioned_on}")
        return " · ".join(bits)


@dataclass(frozen=True)
class ChainFact:
    """One uncoloured, comparable fact discovered by expanding from the CVR.

    Deliberately has no colour field. A chain fact is knowledge; turning it into
    a verdict is the reader's job.
    """

    predicate: str
    statement: str                      # what is true, in the source's terms
    value: Any = None
    source: str = ""
    as_of: date | None = None
    evidence_ref: str = ""              # content hash of the payload
    comparator: Comparator = field(default_factory=Comparator)
    hops: int = 0

    def line(self) -> str:
        v = "" if self.value is None else f"{self.value}"
        return (f"{self.predicate:<38}{v:<22}{self.statement}\n"
                f"{'':<38}{'':<22}{self.comparator.line()}")


@dataclass(frozen=True)
class Finding:
    """A coloured statement. Red requires a ground and a citation."""

    colour: Colour
    ground: RedGround | None
    statement: str
    authority: str = ""                 # who recorded it — cite this, not an aggregator
    recorded_on: date | None = None
    citation: str = ""                  # URL, OJ reference, case number
    quote: str = ""                     # the source's own words
    qualifier: str = ""                 # for amber: alleged / reported / pending
    evidence_ref: str = ""
    subject_hops: int = 0               # 0 = the screened entity itself

    def __post_init__(self) -> None:
        if self.colour is Colour.RED:
            if self.ground is None:
                raise ValueError(
                    "a red finding needs a ground from RedGround — structure "
                    "alone is never red")
            if not (self.authority and self.citation):
                raise ValueError(
                    "a red finding needs an authority and a citation; an "
                    "uncitable red is an accusation")
            if self.subject_hops > 0:
                raise ValueError(
                    "red attaches to the entity itself. Involvement recorded "
                    "against a party N hops away is that party's finding, not "
                    "this one's — legal facts propagate by defined rules, "
                    "allegations do not propagate at all")
        if self.colour is Colour.AMBER and self.qualifier not in AMBER_QUALIFIERS:
            raise ValueError(
                f"amber needs a qualifier from {AMBER_QUALIFIERS} saying why it "
                "is not adjudicated")

    def line(self) -> str:
        head = f"{self.colour.upper():<7}{self.ground or '':<28}{self.statement}"
        tail = f"{'':<35}{self.authority}"
        if self.recorded_on:
            tail += f", {self.recorded_on}"
        if self.citation:
            tail += f" · {self.citation}"
        return head + "\n" + tail


@dataclass
class Screen:
    """The whole output of one screen: the chain, then the findings."""

    cvr: str
    name: str
    as_of: date
    chain: list[ChainFact] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    #: Every limit, reported in full so coverage is visible.
    unknowable: list[str] = field(default_factory=list)
    #: Where the account requires something no connected source shows.
    gaps: list = field(default_factory=list)
    #: The subset that stopped the screen answering a question it exists to
    #: answer. Kept apart because "one peripheral predicate had no data" and
    #: "we could not check designations at all" are not the same statement, and
    #: colouring both grey makes an ordinary company look unscreenable — a
    #: company that simply never filed a signing rule was coming back grey.
    blocked_on: list[str] = field(default_factory=list)

    @property
    def colour(self) -> Colour:
        """The screen's colour is the strongest finding's, and nothing else.

        Notably: not a function of how many chain facts fired. A company with
        eleven unusual structural facts and no recorded involvement is not red,
        and a system that lets volume of structure become colour is one that
        concludes on the client's behalf.
        """
        if any(f.colour is Colour.RED for f in self.findings):
            return Colour.RED
        if any(f.colour is Colour.AMBER for f in self.findings):
            return Colour.AMBER
        if self.blocked_on:
            return Colour.GREY
        return Colour.GREEN
