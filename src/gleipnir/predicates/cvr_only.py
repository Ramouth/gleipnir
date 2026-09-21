"""Predicates computable from ONE CVR document — no chain expansion.

Deliberately the cheap tier: one API request per company, so the whole set can
be run over a control sample without burning quota. Chain-walking predicates
(the Tier A path-product) cost one request per node and live elsewhere.

Each returns facts. No predicate here produces prose, a score, or an adjective.

**`on` is required, everywhere.** A verdict must be a pure function of (pinned
raw bytes, explicit as-of date) — architecture.md §9. An implicit `date.today()`
default made the same claim list return `UNKNOWABLE` on one day and
`TRUE raw=0.6000` on another, and made the machine's timezone decide the answer.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any, Iterable

from gleipnir.claims import Claim, Predicate, at, latest
from gleipnir.predicates.core import Result, Tier, V, unknowable, unknown

#: Legally salient ownership thresholds. Decimal, not float: the window
#: subtraction below must be exact at every threshold, and `float(1/3)` sits
#: *below* true one-third, which made the one stake most likely to be
#: threshold-optimised invisible to the bunching predicate.
#:
#: 0.3333 / 0.6667 rather than exact thirds because that is what CVR filings
#: contain — the register stores four decimal places.
THRESHOLDS: tuple[Decimal, ...] = tuple(Decimal(t) for t in (
    "0.05", "0.10", "0.25", "0.3333", "0.50", "0.6667", "0.90"))
BUNCHING_WINDOW = Decimal("0.02")

#: One epsilon, used by both the gate and the reported quantity. Previously the
#: gate used 1e-9 while the display rounded to 6dp, so a 1e-7 divergence was
#: reported as `TRUE` with `raw="max +0.0pp"` — a finding asserting its own
#: magnitude was zero. CVR files 4 decimal places; anything below that is noise.
MIN_DIVERGENCE = Decimal("0.0001")


def _shares(claims: list[Claim], pred: Predicate, on: date) -> dict[str, list[tuple[Decimal, str]]]:
    """Holder key -> every filed share, with the register that filed it.

    Deliberately does NOT collapse to one value per holder. The same
    enhedsNummer can appear in both EJERREGISTER and REELLE EJERE with
    different percentages; overwriting would resolve a contradiction by JSON
    array order, which is exactly what the claim model exists to prevent.
    Callers must decide explicitly what to do with a holder filed twice.
    """
    out: dict[str, list[tuple[Decimal, str]]] = {}
    for c in at(claims, pred, on):
        s = c.qualifiers.get("share")
        if s is None:
            continue
        value = s if isinstance(s, Decimal) else Decimal(str(s))
        out.setdefault(c.subject.key, []).append(
            (value, str(c.qualifiers.get("register") or "")))
    for k in out:
        out[k].sort(key=lambda t: (t[0], t[1]))   # total order, no ties
    return out


def _single_shares(shares: dict[str, list[tuple[Decimal, str]]]) -> tuple[dict[str, Decimal], list[str]]:
    """Collapse to one value per holder, reporting which holders conflicted.

    On conflict the LARGEST filed value is used and the holder is named. Largest
    rather than latest because the aggregation tests are about whether a
    threshold could be crossed, and understating a holding fails open.
    """
    single: dict[str, Decimal] = {}
    conflicted: list[str] = []
    for key in sorted(shares):
        values = {v for v, _ in shares[key]}
        if len(values) > 1:
            conflicted.append(key)
        single[key] = max(values)
    return single, conflicted


def _history(claims: list[Claim], pred: Predicate, on: date) -> list[Claim]:
    """Versions valid at `on`, oldest first, totally ordered.

    Honours `valid_to` as well as `valid_from`: an expired version must not be
    reported as the current value. Ties on `valid_from` are broken by the
    stringified object so array order never decides which value is "current" —
    two same-day REVISION_FRAVALGT versions previously produced opposite
    verdicts depending on JSON order.
    """
    return sorted(at(claims, pred, on),
                  key=lambda c: (c.valid_from or date.min, str(c.object)))


# ── Tier C (promoted): voting rights vs equity ───────────────────────────────

def voting_exceeds_equity(claims: list[Claim], on: date) -> Result:
    """CVR files EJERANDEL_PROCENT and EJERANDEL_STEMMERET_PROCENT separately.

    threat-model.md §2.2's "49% plus control" limb is therefore a subtraction of
    two filed numbers — no inference, no private documents.
    """
    name = "voting_exceeds_equity"
    equity, _ = _single_shares(_shares(claims, Predicate.OWNS, on))
    votes, _ = _single_shares(_shares(claims, Predicate.HAS_VOTING_RIGHTS, on))
    if not equity and not votes:
        return unknowable(name, Tier.C, "no ownership register entries filed")

    zero = Decimal("0")
    diffs = {k: votes[k] - equity.get(k, zero) for k in votes
             if votes[k] - equity.get(k, zero) >= MIN_DIVERGENCE}
    if not diffs:
        return Result(name, V.FALSE, Tier.C, raw="max +0.0000",
                      evidence=f"{len(equity)} holding(s) compared; no divergence "
                               f"at or above {MIN_DIVERGENCE}")
    # Total order: largest divergence, then holder key. max() alone returns the
    # first-inserted key on a tie, i.e. whichever the JSON listed first.
    worst = max(sorted(diffs), key=lambda k: (diffs[k], k))
    return Result(name, V.TRUE, Tier.C, raw=f"max +{diffs[worst]:.4f}",
                  evidence=f"holder {worst}: equity {equity.get(worst, zero):.4f} "
                           f"votes {votes[worst]:.4f}",
                  detail={"divergences": {k: str(v) for k, v in sorted(diffs.items())}})


# ── Tier C (new): how much of the cap table is invisible ─────────────────────

def ownership_residual(claims: list[Claim], on: date) -> Result:
    """The ejerregister does not have to sum to 1.0.

    The gap is holdings below the 5% registration threshold — precisely the
    space where sub-threshold aggregation hides.
    """
    name = "ownership_residual_unaccounted"
    shares = _shares(claims, Predicate.OWNS, on)
    equity, conflicted = _single_shares(shares)
    unquantified = [c for c in at(claims, Predicate.OWNS, on)
                    if c.qualifiers.get("share") is None]
    if not equity and not unquantified:
        return unknowable(name, Tier.C, "no ownership register entries filed")
    # Decimal sum: exact and order-independent, so the residual no longer
    # depends on how the same cap table happened to be split across rows.
    total = sum(equity.values(), Decimal("0"))
    residual = Decimal("1") - total
    ev = f"{len(equity)} quantified holding(s) sum to {total:.4f}"
    if unquantified:
        ev += f"; {len(unquantified)} listed with no percentage"
    if conflicted:
        ev += f"; {len(conflicted)} holder(s) filed twice with different shares"
    from gleipnir.calibration import stratum_rate
    rate = stratum_rate(name, len(equity))
    ev += f"; base rate at {len(equity)} owner(s): {rate:.0%}" if rate is not None else ""
    if residual < MIN_DIVERGENCE:
        return Result(name, V.FALSE, Tier.OBSERVE, raw=f"{residual:.4f}", evidence=ev)
    return Result(name, V.TRUE, Tier.OBSERVE, raw=f"{residual:.4f}", evidence=ev,
                  detail={"total": str(total), "unquantified": len(unquantified),
                          "conflicted_holders": conflicted})


# ── Tier C: bunching just below a legal threshold ────────────────────────────

def stake_just_below_threshold(claims: list[Claim], on: date) -> Result:
    name = "stake_just_below_threshold"
    equity, _ = _single_shares(_shares(claims, Predicate.OWNS, on))
    if not equity:
        return unknowable(name, Tier.C, "no quantified holdings filed")
    hits = []
    for holder in sorted(equity):
        share = equity[holder]
        for t in THRESHOLDS:
            if t - BUNCHING_WINDOW <= share < t:
                hits.append((t - share, holder, share, t))
    if not hits:
        return Result(name, V.FALSE, Tier.C, raw=f"{len(equity)} holding(s)",
                      evidence=f"none within {BUNCHING_WINDOW} below "
                               f"{len(THRESHOLDS)} thresholds")
    # Report the TIGHTEST gap, not whichever the JSON listed first — the raw
    # quantity is what docs/predicate-selection.md recalibrates on.
    hits.sort(key=lambda h: (h[0], h[1], h[3]))
    gap, holder, share, t = hits[0]
    return Result(name, V.TRUE, Tier.C, raw=f"{share:.4f} vs {t:.4f}",
                  evidence=f"holder {holder} sits {gap:.4f} below {t:.4f}"
                           + (f" (+{len(hits)-1} more)" if len(hits) > 1 else ""),
                  detail={"hits": [[str(x) for x in h] for h in hits]})


# ── Tier A precursor: sub-threshold aggregation ──────────────────────────────

def subthreshold_aggregation(claims: list[Claim], on: date) -> Result:
    """Multiple holders each under 50%, aggregating over it.

    UNKNOWN, not TRUE, when it fires: whether the holders are *associated* is
    the person-resolution question, and this predicate cannot answer it. It
    marks a research task — which is the whole point of the four-valued logic.
    """
    name = "subthreshold_aggregate_over_50"
    equity, _ = _single_shares(_shares(claims, Predicate.OWNS, on))
    if not equity:
        return unknowable(name, Tier.A, "no quantified holdings filed")
    half = Decimal("0.50")
    under = {k: v for k, v in equity.items() if v < half}
    total_under = sum(under.values(), Decimal("0"))
    # `> half` exactly: the EU test is ownership *exceeding* 50%, and Decimal
    # makes the boundary well defined rather than a float knife-edge.
    from gleipnir.calibration import stratum_rate
    rate = stratum_rate(name, len(equity))
    note = f" — base rate at {len(equity)} owner(s) is {rate:.0%}" if rate is not None else ""
    if len(under) < 2 or total_under <= half:
        return Result(name, V.FALSE, Tier.OBSERVE, raw=f"{total_under:.4f}",
                      evidence=f"{len(under)} holder(s) under 50% summing to "
                               f"{total_under:.4f}{note}")
    # Tier.OBSERVE, not Tier.A. Stratified by owner count this fires 0.0% at one
    # owner and 91.4% at six or more: it restates the size of the cap table and
    # carries no evidence of association beyond it.
    return Result(name, V.UNKNOWN, Tier.OBSERVE, raw=f"{total_under:.4f} across {len(under)}",
                  evidence="holders under 50% aggregate over 50%; association between "
                           "them NOT established, and this fires on most multi-owner "
                           f"companies{note}",
                  detail={"holders": {k: str(v) for k, v in sorted(under.items())}})


# ── Tier B: designation nexus among filed holders ────────────────────────────

def designated_holder(claims: list[Claim], index, on: date) -> Result:
    """Filed holders checked against the designation list.

    Identifier matches would be deterministic; CVR gives us names for corporate
    holders and enhedsNummer for persons, neither of which OpenSanctions keys
    on, so this is a NAME check and therefore produces candidates only.
    """
    name = "designated_holder_candidate"
    if index is None:
        # Its sibling `designated_in_chain` guarded this and it did not. A
        # screen run without the list must say it could not check — that is a
        # material limit, and returning FALSE would report "no designation
        # nexus" about a check that never ran.
        return unknowable(name, Tier.B, "no designation list loaded")
    holders = {c.subject.key: (c.subject.label or "")
               for c in at(claims, Predicate.OWNS, on)}
    if not holders:
        return unknowable(name, Tier.B, "no ownership register entries filed")
    named = {k: v for k, v in holders.items() if v}
    if not named:
        return unknowable(name, Tier.B, f"{len(holders)} holder(s), none carrying a name")
    hits = {}
    for key in sorted(named):
        # Dedupe by id: a target whose name and alias normalise identically is
        # returned once per spelling, which overstated the cited count.
        found = {t.id: t for t in index.by_name(named[key])}
        if found:
            hits[key] = (named[key], [found[i] for i in sorted(found)])
    if not hits:
        # The designation count is deliberately NOT in the evidence string: it
        # changes on every OpenSanctions export, so two identical screens would
        # produce non-identical reports. The corpus is identified by blob hash
        # in the report header instead.
        return Result(name, V.FALSE, Tier.B, raw=f"{len(named)} checked",
                      evidence="0 name matches against the pinned designation list")
    key = sorted(hits)[0]
    label, found = hits[key]
    return Result(name, V.TRUE, Tier.B, raw=f"{len(hits)} of {len(named)}",
                  evidence=f"{label!r} matches {found[0].id} "
                           f"[{', '.join(sorted(found[0].designating_programmes)[:3])}] "
                           f"— NAME MATCH, NOT adjudicated"
                           + (f"; {len(found)} designations share this name" if len(found) > 1 else ""),
                  detail={"hits": {k: v[0] for k, v in sorted(hits.items())}})


# ── Observations: recorded as fact, no polarity ──────────────────────────────

def signing_rule_changes(claims: list[Claim], on: date) -> Result:
    name = "signing_rule_changed"
    # Every version recorded up to the as-of date, not only those still in
    # force. This predicate asks whether the signing rule has *ever* changed;
    # filtering to versions valid at `on` would always answer "one version".
    hist = sorted((c for c in claims
                   if c.predicate is Predicate.SIGNING_RULE
                   and c.valid_from and c.valid_from <= on),
                  key=lambda c: (c.valid_from, str(c.object)))
    if not hist:
        return unknowable(name, Tier.OBSERVE, "TEGNINGSREGEL not filed before as-of")
    if len(hist) < 2:
        return Result(name, V.FALSE, Tier.OBSERVE, raw="1 version",
                      evidence=f"unchanged since {hist[0].valid_from}")
    return Result(name, V.TRUE, Tier.OBSERVE, raw=f"{len(hist)} versions",
                  evidence=f"last change {hist[-1].valid_from}",
                  detail={"dates": [str(c.valid_from) for c in hist]})


def same_day_capital_and_ownership_change(claims: list[Claim], on: date) -> Result:
    """A capital change dated identically to an ownership change.

    One transaction, two registers. Recorded because timing coincidence is what
    Tier B reads, and this is its cheapest within-company form.
    """
    name = "capital_change_same_day_as_ownership"
    cap = {c.valid_from for c in claims
           if c.predicate is Predicate.HAS_CAPITAL and c.valid_from and c.valid_from <= on}
    own = {c.valid_from for c in claims
           if c.predicate is Predicate.OWNS and c.valid_from and c.valid_from <= on}
    if not cap or not own:
        return unknowable(name, Tier.OBSERVE, "capital or ownership history absent")
    both = sorted(cap & own)
    if not both:
        return Result(name, V.FALSE, Tier.OBSERVE,
                      raw=f"{len(cap)} cap / {len(own)} own dates",
                      evidence="no shared dates")
    return Result(name, V.TRUE, Tier.OBSERVE, raw=f"{len(both)} date(s)",
                  evidence=f"{', '.join(str(d) for d in both[:3])}")


def ownership_events(claims: list[Claim], on: date) -> Result:
    """Count of distinct dates on which the ownership register changed."""
    name = "ownership_register_events"
    dates = sorted({c.valid_from for c in claims
                    if c.predicate is Predicate.OWNS and c.valid_from
                    and c.valid_from <= on})
    if not dates:
        return unknowable(name, Tier.OBSERVE, "no ownership history")
    return Result(name, V.TRUE if len(dates) > 1 else V.FALSE, Tier.OBSERVE,
                  raw=f"{len(dates)} date(s)",
                  evidence=f"first {dates[0]}, last {dates[-1]}")


def audit_waived(claims: list[Claim], on: date) -> Result:
    name = "audit_waived"
    cur = latest(claims, Predicate.AUDIT_WAIVED, on)
    if cur is None:
        return unknowable(name, Tier.OBSERVE,
                          "REVISION_FRAVALGT not filed or not valid at as-of")
    versions = [c for c in claims if c.predicate is Predicate.AUDIT_WAIVED
                and c.valid_from and c.valid_from <= on]
    current = str(cur.object).lower() == "true"
    return Result(name, V.TRUE if current else V.FALSE, Tier.OBSERVE,
                  raw=f"{len(versions)} version(s)",
                  evidence=f"value in force from {cur.valid_from}")


ALL_CVR_ONLY = [
    subthreshold_aggregation, voting_exceeds_equity, ownership_residual,
    stake_just_below_threshold, signing_rule_changes,
    same_day_capital_and_ownership_change, ownership_events, audit_waived,
]


def insolvency_status(claims: list[Claim], on: date) -> Result:
    """Composite lifecycle state from `sammensatStatus`.

    Recorded as an observation, not a red flag: forced dissolution and
    bankruptcy are the *majority* mode of company death (>500k/yr in the UK
    alone per the base-rate research), and the criminal-enterprise lane shows
    short lifecycle points toward VAT fraud rather than concealed state
    ownership — which optimises for durability, not exit.
    """
    name = "insolvency_or_dissolution"
    # `at(..., on)`, not a scan of every claim. This predicate accepted `on` and
    # ignored it — the defect class `docs/determinism-audit.md` found in five
    # others — and because `sammensatStatus` was also filed with no validity
    # period, a screen of Example Bank A/S as of 2010 returned `TRUE UNDERKONKURS`
    # a year before the bank failed. The extractor now dates the composite from
    # the current status period; this reads that date.
    comp = sorted((c for c in at(claims, Predicate.HAS_STATUS, on)
                   if c.qualifiers.get("composite")),
                  key=lambda c: (c.valid_from or date.min, str(c.object)))
    if not comp:
        return unknowable(name, Tier.OBSERVE,
                          f"no composite sammensatStatus valid at {on}")
    value = str(comp[-1].object)
    flagged = value.upper() in {
        "UNDERKONKURS", "OPLØSTEFTERKONKURS", "TVANGSOPLØST",
        "UNDERTVANGSOPLØSNING", "UNDERREASSUMERING", "UNDERFRIVILLIGLIKVIDATION",
        "OPLØSTEFTERERKLÆRING", "OPLØSTEFTERFRIVILLIGLIKVIDATION",
    }
    return Result(name, V.TRUE if flagged else V.FALSE, Tier.OBSERVE, raw=value,
                  evidence="composite registry status")


ALL_CVR_ONLY.append(insolvency_status)


def unusual_ownership_percentage(claims: list[Claim], on: date) -> Result:
    """A filed ownership percentage that is not one of the ~12 round values.

    Replaces `stake_just_below_threshold`, which measured a phenomenon that does
    not occur: across 35,071 filed percentages, the 2-point bucket below every
    legal threshold held 0, 2, 0 and 0 values. Danish ownership is not a
    continuous distribution to look for bunching in — it is a small set of round
    numbers, and 51.5% of all filings are simply 1.0.

    So the informative event is the opposite one. A company filing 0.4900 or
    0.2490 has done something ~0.06% of filings do. That is a *measured* rarity
    (see `calibration.py`), not a chosen threshold.

    Rare is not suspicious: an unusual percentage is what a real negotiated
    cap table looks like. It is admitted as contributing evidence only.
    """
    from gleipnir.calibration import ROUND_SHARES

    name = "unusual_ownership_percentage"
    equity, _ = _single_shares(_shares(claims, Predicate.OWNS, on))
    if not equity:
        return unknowable(name, Tier.C, "no quantified holdings filed")
    odd = {k: v for k, v in equity.items() if v not in ROUND_SHARES}
    if not odd:
        return Result(name, V.FALSE, Tier.C, raw=f"{len(equity)} holding(s)",
                      evidence="every filed percentage is one of the 11 values "
                               "covering 99.9% of Danish filings")
    key = sorted(odd)[0]
    return Result(name, V.TRUE, Tier.C, raw=f"{odd[key]:.4f}",
                  evidence=f"holder {key} filed {odd[key]:.4f}; non-round values are "
                           f"~0.06% of 35,071 filed percentages"
                           + (f" (+{len(odd)-1} more)" if len(odd) > 1 else ""),
                  detail={"unusual": {k: str(v) for k, v in sorted(odd.items())}})


def majority_owner_unresolvable(claims: list[Claim], on: date) -> Result:
    """A majority holder that is neither a CVR-registered company nor a person.

    In a 25,000-company scan every `VIRKSOMHED` participant carried a CVR
    number, so a corporate owner outside the Danish register arrives as
    `ANDEN_DELTAGER`. 7.22% of companies with a filed register have one;
    5.04% have one holding a majority.

    What such a party *is* — foreign company, partnership, estate, foundation,
    trust — is NOT established by this field, and the finding must not say
    "foreign". It says the chain cannot continue from this source, which is
    `poc.md` §6.5's point exactly.
    """
    name = "majority_owner_unresolvable"
    shares = _shares(claims, Predicate.OWNS, on)
    if not shares:
        return unknowable(name, Tier.B, "no quantified holdings filed")
    equity, _ = _single_shares(shares)
    # `fp:` is a named foreign natural person — a human, not an opaque party.
    # Counting them here reported "not a natural person" about named humans.
    opaque = {k: v for k, v in equity.items()
              if k.startswith(("anden:", "enh:", "fc:"))}
    if not opaque:
        return Result(name, V.FALSE, Tier.B, raw=f"{len(equity)} holding(s)",
                      evidence="every filed owner is a CVR-registered company or a "
                               "natural person")
    major = {k: v for k, v in opaque.items() if v > Decimal("0.50")}
    if not major:
        key = sorted(opaque)[0]
        return Result(name, V.FALSE, Tier.B, raw=f"{opaque[key]:.4f} minority",
                      evidence=f"{len(opaque)} owner(s) not resolvable to a CVR number "
                               f"or a person, none holding a majority")
    key = sorted(major, key=lambda k: (-major[k], k))[0]
    return Result(name, V.TRUE, Tier.B, raw=f"{major[key]:.4f}",
                  evidence=f"majority holder is not a CVR-registered company and not a "
                           f"natural person; ownership beyond it is not determinable "
                           f"from CVR (base rate 5.04%)",
                  detail={"holders": {k: str(v) for k, v in sorted(major.items())}})


ALL_CVR_ONLY.remove(stake_just_below_threshold)
ALL_CVR_ONLY.extend([unusual_ownership_percentage, majority_owner_unresolvable])


def nominee_density(claims: list[Claim], directorships: dict[str, int],
                    on: date) -> Result:
    """A person holding management roles in an implausible number of companies.

    `poc.md` §7 calls this "trivially computable, no external data, high value".
    It is trivially computable and had **no denominator anywhere** — no
    published statistic exists, and Companies House cannot produce one because
    of unresolved duplicate director identities. So `docs/predicates.md` §5
    parked it.

    Measured 2026-08-27 over 115,734 people in a 25,000-company scan:

        1 directorship   76.8%          10 or fewer   99.5%
        2 or fewer       90.6%          20 or fewer   99.91%
        5 or fewer       98.0%          max observed  2,708

    Threshold 20 puts a person in the top ~0.09%. Counts are lower bounds
    (see `calibration.build_directorship_index`), so this understates.
    """
    from gleipnir.calibration import (INFRASTRUCTURE_MIN_DIRECTORSHIPS,
                                      NOMINEE_DENSITY_THRESHOLD)

    name = "nominee_density"
    people = {c.object.key: (c.object.label or c.object.key)
              for c in at(claims, Predicate.HAS_ROLE, on)
              if getattr(c.object, "kind", None) == "person"}
    if not people:
        return unknowable(name, Tier.C, "no natural persons in a management role")
    # Exclude corporate infrastructure. Twelve enhedsNummer values hold 200+
    # directorships each and sit on 13.8% of all Danish companies; including
    # them made this predicate fire on 16-22% of Denmark and measure nothing.
    known = {k: directorships[k] for k in sorted(people)
             if k in directorships
             and directorships[k] < INFRASTRUCTURE_MIN_DIRECTORSHIPS}
    if not known:
        return unknown(name, Tier.C,
                       f"{len(people)} person(s), none present in the sampled "
                       f"directorship index — expand the scan to answer this")
    top = max(sorted(known), key=lambda k: (known[k], k))
    if known[top] < NOMINEE_DENSITY_THRESHOLD:
        return Result(name, V.FALSE, Tier.C, raw=f"max {known[top]}",
                      evidence=f"{len(known)} of {len(people)} person(s) in the index; "
                               f"highest holds {known[top]} directorship(s), below the "
                               f"threshold of {NOMINEE_DENSITY_THRESHOLD} "
                               f"(company-level base rate 0.94%)")
    return Result(name, V.TRUE, Tier.C, raw=f"{known[top]}+",
                  evidence=f"a director holds at least {known[top]} directorships, "
                           f"excluding known mass-incorporation infrastructure "
                           f"(company-level base rate 0.94%); count is a lower bound "
                           f"from a 49,000-company sample",
                  detail={"counts": {k: known[k] for k in sorted(known)}})


def registered_audit_election_absent(claims: list[Claim], on: date) -> Result:
    """No `REVISION_FRAVALGT` election on file at the as-of date.

    Found by the outcome backtest rather than by reasoning, and it started life
    as a coverage row. `audit_waived` returns `UNKNOWABLE` when the attribute is
    missing, which sent every one of these to the "could not be evaluated"
    section — where the largest separation in the whole test was sitting
    unread.

    The distinction the four values exist to draw applies to our own output
    here. `UNKNOWABLE` asserts *the source cannot cover this*. The source covers
    it for **99.9%** of live Danish companies, so its absence is not a hole in
    our data; it is a fact about the subject, and belongs in `TRUE`/`FALSE`.
    `audit_waived` keeps its `UNKNOWABLE` — "was audit waived" genuinely has no
    filed answer — and this predicate answers the different question of whether
    an answer was ever filed.

    Measured 2026-08-28 (`calibration.OUTCOME_LR_365`), matched 1:1 on legal
    form, age band and owner count, evaluated 365 days before the transition:

        forced dissolution   47.4% vs 1.4% control    33.0x  [20.5, 53.1]
        bankruptcy            5.0% vs 0.1% control    53.5x  [13.2, 216.4]

    Flat at a five-year lead (33.3x and 36.0x), so it is not reading the run-up.
    Document completeness is ruled out: FORMÅL, TEGNINGSREGEL and KAPITAL are
    present at ~99.9% in all three cohorts and the mean attribute count differs
    by one.

    **What it predicts is administrative death, not concealment.** Forced
    dissolution is the registrar's response to a company that has not filed, and
    a company with no audit election on file is largely a company that has not
    filed. It is Tier C, and `threat-model.md` is not about companies that stop
    filing — it is about ones that keep filing while concealing who owns them.
    """
    from gleipnir.calibration import AUDIT_ELECTION_ON_FILE

    name = "registered_audit_election_absent"
    # A company that cannot make the election is not a company that declined to.
    # Example Bank A/S — listed, and under Finanstilsynet supervision — carries no
    # REVISION_FRAVALGT because a listed supervised bank may not opt out of
    # audit. Read a year before its 2011 collapse this predicate reported the
    # absence as an established fact about the bank. It is a fact about Danish
    # company law. Both attributes are already extracted; neither was consulted.
    barred = [p for p, label in ((Predicate.IS_LISTED, "listed"),
                                 (Predicate.SUPERVISORY_CATEGORY, "under supervision"))
              if latest(claims, p, on) is not None]
    if barred:
        why = " and ".join("listed" if p is Predicate.IS_LISTED else "under supervision"
                           for p in barred)
        return unknowable(name, Tier.C,
                          f"{why} at {on} — audit may not be waived, so no election "
                          f"is filed and its absence carries no information")
    versions = [c for c in claims if c.predicate is Predicate.AUDIT_WAIVED
                and (c.valid_from is None or c.valid_from <= on)]
    control = AUDIT_ELECTION_ON_FILE["control"]
    if latest(claims, Predicate.AUDIT_WAIVED, on) is not None:
        return Result(name, V.FALSE, Tier.C, raw=f"{len(versions)} version(s)",
                      evidence=f"an audit election is on file, as it is for "
                               f"{control:.1%} of active Danish companies")
    return Result(name, V.TRUE, Tier.C, raw="0 versions",
                  evidence=f"no REVISION_FRAVALGT filed on or before {on}; on file for "
                           f"{control:.1%} of active companies, "
                           f"{AUDIT_ELECTION_ON_FILE['bankruptcy']:.1%} of bankrupt and "
                           f"{AUDIT_ELECTION_ON_FILE['forced-dissolution']:.1%} of "
                           f"force-dissolved ones (n=4,000 each)")


ALL_CVR_ONLY.append(registered_audit_election_absent)


def dissolution_threat_on_file(claims: list[Claim], on: date) -> Result:
    """The registrar has formally warned this company it will be struck off.

    `OPLØSNINGSTRUSSEL_SENESTE`. Not authored — **found**, by
    `scripts/questions.py` enumerating every dated filing in the register and
    ranking what separates matched pairs. It was the top surviving row for the
    bankruptcy cohort and nobody had thought to look for it.

    Measured, matched 1:1 on legal form, age band and owner count:

        1 year before bankruptcy    1.18% vs 0.24%    5.00x [1.92, 13.04]
        3 years before              0.94% vs 0.30%    3.17x [1.27,  7.91]
        5 years before              1.06% vs 0.22%    4.75x [1.62, 13.93]

    Rare and stable: it separates at every lead, so it is not reading the
    run-up. All 100 filed values carry a validity period, so the as-of read is
    sound.

    **Not established for forced dissolution** — 5 of 1,183 against 1, interval
    [0.59, 42.73]. Counter-intuitive, since the threat is the registrar's step
    toward exactly that outcome, and reported rather than smoothed: the
    attribute records the *latest* threat, so one acted on may not survive in
    the form this reads.

    Tier C. It is an authority's dated act, which is more than any structural
    predicate here can say — and it is still not one of `RedGround`'s four.
    A dissolution threat is an administrative notice, not an adjudication.
    """
    name = "dissolution_threat_on_file"
    versions = at(claims, Predicate.DISSOLUTION_THREAT, on)
    if not versions:
        return Result(name, V.FALSE, Tier.C, raw="0 on file",
                      evidence=f"no OPLØSNINGSTRUSSEL_SENESTE in force at {on}; "
                               f"0.24% of matched active companies carry one")
    newest = max(versions, key=lambda c: (c.valid_from or date.min, str(c.object)))
    return Result(name, V.TRUE, Tier.C, raw=str(newest.object),
                  evidence=f"registrar dissolution threat in force from "
                           f"{newest.valid_from}; 0.24% of matched active companies "
                           f"and 1.18% of companies bankrupt a year later (5.0x)",
                  detail={"versions": len(versions)})


ALL_CVR_ONLY.append(dissolution_threat_on_file)
