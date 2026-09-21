"""Four-valued predicate logic.

A boolean cannot express what an OSINT system knows. Databases run closed-world
— not in the store means false — and OSINT is emphatically open-world, so
`FALSE` and `UNKNOWN` must not share a representation. Collapsing them into
SQL NULL is how coverage holes silently render as clean.

    TRUE        established
    FALSE       checked, positively absent          -> evidence, a green datum
    UNKNOWN     not yet checked                     -> a research task
    UNKNOWABLE  checked; the source cannot cover it -> a coverage statement

Every result carries the raw quantity it thresholded and a one-line evidence
string naming source and date. A predicate that reports only a verdict is not
reportable: `docs/predicate-selection.md` requires `raw_quantity` so the
threshold can be recalibrated after Phase 0 and so a finding can be defended.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class V(StrEnum):
    TRUE = "TRUE"
    FALSE = "FALSE"
    UNKNOWN = "UNKNOWN"
    UNKNOWABLE = "UNKNOWABLE"


class Tier(StrEnum):
    """Admission tier from docs/predicates.md. Determines what a hit may do."""

    A = "A"          # legal test — may state a conclusion
    B = "B"          # recurrence/timing — strong concealment signal
    C = "C"          # contributing — may not colour a finding alone
    OBSERVE = "OBS"  # recorded as fact, no polarity claimed


@dataclass(frozen=True)
class Result:
    predicate: str
    value: V
    tier: Tier
    #: The underlying continuous value, where one was thresholded. Kept so the
    #: threshold stays recalibratable and the finding stays defensible.
    raw: Any = None
    #: Facts only: what was checked, against what, on what date. No adjectives.
    evidence: str = ""
    detail: dict[str, Any] = field(default_factory=dict)

    def line(self) -> str:
        raw = "" if self.raw is None else f"{self.raw}"
        return f"{self.value:<11}{self.tier:<5}{self.predicate:<38}{raw:<24}{self.evidence}"


def unknown(name: str, tier: Tier, why: str) -> Result:
    return Result(name, V.UNKNOWN, tier, evidence=why)


def unknowable(name: str, tier: Tier, why: str) -> Result:
    return Result(name, V.UNKNOWABLE, tier, evidence=why)
