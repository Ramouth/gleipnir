"""Pairing self-declared claims against registry claims.

`architecture.md` §5 makes a Contradiction a first-class node rather than an
error state. This module is the deterministic pairing step that produces
candidates for it — the fan-out the graph does before the model is asked
anything (§7.1).

The pairing is driven by `extract.website.CHECKS_AGAINST`, so a website claim
with no registry counterpart never becomes an eligible pair. Nothing here
decides truth; it decides *what is worth comparing*.

**Materiality is applied here, not at report time.** §10's honesty requirement
is that a discrepancy is not evidence of deception: registries lag, titles are
informal, small companies are sloppy. The single best free discriminator is
time-since-registry-change — a stale page three weeks after a director change is
expected; four years after, it is telling a different story. Every candidate
carries that gap in days so a downstream rule can threshold it instead of
counting raw mismatches.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum
from typing import Any

from gleipnir.claims import Claim, EpistemicTier, Predicate, at
from gleipnir.extract.website import CHECKS_AGAINST


class Kind(StrEnum):
    VALUE_CONFLICT = "value_conflict"          # both assert, values differ
    EXISTENCE_CONFLICT = "existence_conflict"  # self-declared, registry silent
    TEMPORAL_CONFLICT = "temporal_conflict"    # agree on value, not on period


class Status(StrEnum):
    CANDIDATE = "candidate"        # pairing only; nothing adjudicated
    CORROBORATED = "corroborated"  # sources agree — a green datum


@dataclass(frozen=True)
class Pair:
    predicate: Predicate
    kind: Kind | None
    status: Status
    declared: Any
    registered: Any
    #: Days between the website observation and the last registry change to the
    #: same predicate. None when the registry never recorded one.
    staleness_days: int | None
    evidence: str
    detail: dict[str, Any] = field(default_factory=dict)

    def line(self) -> str:
        k = self.kind or self.status
        stale = "" if self.staleness_days is None else f"{self.staleness_days}d"
        return (f"{str(k):<20}{self.predicate:<20}"
                f"{str(self.declared)[:30]:<32}{str(self.registered)[:26]:<28}{stale:<8}"
                f"{self.evidence}")


def _role_key(claim: Claim) -> str:
    label = getattr(claim.object, "label", None) or str(claim.object)
    return label.casefold().strip()


def _last_change(registry: list[Claim], pred: Predicate) -> date | None:
    dates = [c.valid_from for c in registry if c.predicate is pred and c.valid_from]
    return max(dates) if dates else None


def pair_claims(
    website: list[Claim], registry: list[Claim], *, as_of: date
) -> list[Pair]:
    """Deterministic pairing. No model, no scoring, no conclusions."""
    out: list[Pair] = []
    for wc in website:
        target = CHECKS_AGAINST.get(wc.predicate)
        if target is None:
            continue
        counterparts = at(registry, target, as_of)
        changed = _last_change(registry, target)
        stale = (as_of - changed).days if changed else None

        if wc.predicate is Predicate.HAS_ROLE:
            out.append(_pair_role(wc, counterparts, stale))
        elif wc.predicate is Predicate.CLAIMS_IDENTIFIER:
            out.append(_pair_identifier(wc, registry, stale))
        elif wc.predicate is Predicate.EMPLOYS:
            out.append(_pair_employees(wc, counterparts, stale))
        elif wc.predicate is Predicate.HAS_LOCATION:
            out.append(_pair_location(wc, counterparts, stale))
        else:
            out.append(_pair_generic(wc, counterparts, stale, target))
    return out


def _pair_role(wc: Claim, counterparts: list[Claim], stale: int | None) -> Pair:
    declared = getattr(wc.object, "label", str(wc.object))
    registered_names = {_role_key(c) for c in counterparts}
    if _role_key(wc) in registered_names:
        return Pair(Predicate.HAS_ROLE, None, Status.CORROBORATED, declared,
                    declared, stale,
                    f"person named on site holds a registered function "
                    f"({wc.qualifiers.get('role')})")
    return Pair(
        Predicate.HAS_ROLE, Kind.EXISTENCE_CONFLICT, Status.CANDIDATE,
        f"{declared} — {wc.qualifiers.get('role')}",
        f"{len(counterparts)} registered person(s)", stale,
        "named on site with a role; no registered function under that name. "
        "NOT adjudicated: name match only, and informal titles are common",
        {"registered": sorted(registered_names)[:8]},
    )


def _pair_identifier(wc: Claim, registry: list[Claim], stale: int | None) -> Pair:
    """The displayed CVR number either is this entity's, or it is not."""
    declared = str(wc.object).removeprefix("DK")
    subject_key = wc.subject.key
    if declared == subject_key:
        return Pair(Predicate.CLAIMS_IDENTIFIER, None, Status.CORROBORATED,
                    declared, subject_key, stale,
                    "displayed CVR matches the entity screened — deterministic join")
    return Pair(Predicate.CLAIMS_IDENTIFIER, Kind.VALUE_CONFLICT, Status.CANDIDATE,
                declared, subject_key, stale,
                "site displays a different CVR than the entity screened; "
                "may be a group sibling — expand it")


def _pair_location(wc: Claim, counterparts: list[Claim], stale: int | None) -> Pair:
    """Compare by postcode, not by string.

    A site prints "8382 Hinnerup" and the register holds "Eksempelvej 14 8382
    Hinnerup". String equality called that a value conflict on nearly every site
    with an address — a false finding manufactured entirely by the comparator.

    Registered offices AND production units both count: a multi-site business
    lists its branches, and comparing a branch against the legal seat produced
    seven false conflicts on one company.
    """
    declared = str(wc.object)
    pc = wc.qualifiers.get("postcode") or ""
    registered = {c.qualifiers.get("postcode") for c in counterparts
                  if c.qualifiers.get("postcode")}
    if not registered:
        return Pair(Predicate.REGISTERED_AT, Kind.EXISTENCE_CONFLICT, Status.CANDIDATE,
                    declared, None, stale, "address on site; no postcode in the register")
    if pc and pc in registered:
        return Pair(Predicate.REGISTERED_AT, None, Status.CORROBORATED, declared,
                    pc, stale, "postcode matches a registered office or production unit")
    return Pair(Predicate.REGISTERED_AT, Kind.VALUE_CONFLICT, Status.CANDIDATE,
                declared, "/".join(sorted(registered)[:3]), stale,
                "site names a location with no matching registered postcode; a "
                "branch not registered as a production unit is the usual cause")


def _pair_employees(wc: Claim, counterparts: list[Claim], stale: int | None) -> Pair:
    declared = int(wc.object)
    filed = [int(c.object) for c in counterparts
             if isinstance(c.object, (int, float))]
    if not filed:
        return Pair(Predicate.EMPLOYS, Kind.EXISTENCE_CONFLICT, Status.CANDIDATE,
                    declared, None, stale,
                    "employee count stated on site; none filed in the registry")
    best = max(filed)
    # Bands, not equality: CVR files employment in ranges and a site rounds.
    ratio = declared / best if best else float("inf")
    if 0.5 <= ratio <= 2.0:
        return Pair(Predicate.EMPLOYS, None, Status.CORROBORATED, declared, best,
                    stale, f"within a factor of 2 of the filed figure")
    return Pair(Predicate.EMPLOYS, Kind.VALUE_CONFLICT, Status.CANDIDATE,
                declared, best, stale,
                f"stated {declared} vs filed {best} — factor {ratio:.1f}")


def _pair_generic(wc: Claim, counterparts: list[Claim], stale: int | None,
                  target: Predicate) -> Pair:
    declared = getattr(wc.object, "label", None) or wc.object
    values = {str(getattr(c.object, "label", None) or c.object) for c in counterparts}
    if str(declared) in values:
        return Pair(target, None, Status.CORROBORATED, declared, declared, stale,
                    "self-declared value matches a registered one")
    if not values:
        return Pair(target, Kind.EXISTENCE_CONFLICT, Status.CANDIDATE, declared,
                    None, stale, "stated on site; registry records nothing comparable")
    return Pair(target, Kind.VALUE_CONFLICT, Status.CANDIDATE, declared,
                "; ".join(sorted(values)[:3]), stale,
                "self-declared value differs from every registered one")
