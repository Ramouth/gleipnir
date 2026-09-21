"""Gaps: where the account requires something no connected source shows.

The chain answers *what is on record*. This asks the other question — **what
does the record imply should also exist, and is it there?**

A gap is not a fact and not a finding. It is an absence with an expectation
attached, and the expectation has to be stated so the reader can reject it. "The
site claims 200 employees; no accounts are filed" is checkable. "Something feels
off" is not, and is what this exists to avoid.

**Three rules, because this is where a system starts editorialising.**

1. **Every expectation names its trigger.** A gap that fires on every company is
   measuring the population, not the company — the same confounder that made
   sub-threshold aggregation look like a signal until it was stratified.
2. **A gap is stated as a hole in the account, never as suspicion.** The wording
   is "the account requires X; no connected source shows X". The reader supplies
   the inference, or declines to.
3. **A gap we could close by spending is not a gap.** If GLEIF has not been
   asked yet, that is an agenda item. Only an absence that survives the sources
   actually consulted is reported, and each gap records which those were.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum
from typing import Any, Callable, Iterable

from gleipnir.claims import Claim, Predicate, at, latest


class Strand(StrEnum):
    """Which thread of the story a gap sits in."""

    IDENTITY = "identity"
    OWNERSHIP = "ownership"
    PEOPLE = "people"
    PRESENCE = "presence"
    MONEY = "money"
    TIMELINE = "timeline"


#: How often each expectation fails across 329 cached Danish companies.
#: A gap without a denominator is exactly the thing this system refuses to print
#: about a fact, and there is no reason the rule should be weaker here.
#:
#: `quiet_transfer` at 15.8% is the honest outlier: a share transfer between
#: existing holders genuinely does not require a capital or officer change, so
#: most of those 52 are ordinary. It is kept because the rate is stated, not
#: because it discriminates.
GAP_BASE_RATES: dict[str, float] = {
    "quiet_transfer": 0.158,
    "parent_not_identifiable": 0.030,
    "officer_not_registered": 0.027,
    "archive_starts_late": 0.027,
    "domain_older_than_the_company": 0.009,
    "headcount_without_accounts": None,      # accounts adapter not built
}
GAP_COHORT_N = 329


@dataclass(frozen=True)
class Gap:
    strand: Strand
    trigger: str            # what in the record raised the expectation
    expected: str           # what should therefore exist
    found: str              # what is actually there
    consulted: tuple[str, ...] = ()   # sources actually asked
    closable_by: str = ""   # a source that could settle it, if one exists
    name: str = ""          # the expectation that produced it
    base_rate: float | None = None    # how often it fails across the cohort

    @property
    def is_agenda(self) -> bool:
        """A gap a source could close is work, not a hole in the account."""
        return bool(self.closable_by)

    @property
    def common(self) -> bool:
        """Fails on more than a tenth of companies — worth saying out loud."""
        return self.base_rate is not None and self.base_rate > 0.10

    def line(self) -> str:
        tail = (f"could be closed by {self.closable_by}" if self.closable_by
                else f"consulted: {', '.join(self.consulted) or 'none'}")
        rate = ("no measured rate" if self.base_rate is None else
                f"{self.base_rate:.1%} of {GAP_COHORT_N} Danish companies"
                + (" — common" if self.common else ""))
        return (f"{self.strand:<10}{self.trigger}\n"
                f"{'':<10}expects  {self.expected}\n"
                f"{'':<10}found    {self.found}\n"
                f"{'':<10}rate     {rate}   ({tail})")


@dataclass
class Expectation:
    """A trigger, what it implies, and how to check it."""

    strand: Strand
    name: str
    trigger: Callable[["Context"], str | None]
    check: Callable[["Context"], tuple[bool, str]]
    expected: str
    closable_by: str = ""
    consulted: tuple[str, ...] = ()


@dataclass
class Context:
    """Everything the expectations may read. Missing pieces are None, and an
    expectation that needs a source we never asked must not fire."""

    cvr: str
    as_of: date
    registry: list[Claim] = field(default_factory=list)
    website: list[Claim] = field(default_factory=list)
    chain: Any = None
    archive: Any = None            # wayback History
    domain: Any = None             # dkhostmaster DomainRecord
    accounts: Any = None
    asked: set[str] = field(default_factory=set)

    def reg(self, pred: Predicate) -> list[Claim]:
        return at(self.registry, pred, self.as_of)

    def one(self, pred: Predicate):
        return latest(self.registry, pred, self.as_of)

    def web(self, pred: Predicate) -> list[Claim]:
        return [c for c in self.website if c.predicate is pred]


# ── the expectations ────────────────────────────────────────────────────────

def _claimed_headcount(ctx: Context) -> str | None:
    w = ctx.web(Predicate.EMPLOYS)
    if not w:
        return None
    n = max(int(c.object) for c in w if isinstance(c.object, int))
    return f"the site states {n} employees" if n >= 10 else None


def _accounts_present(ctx: Context) -> tuple[bool, str]:
    if "regnskaber" not in ctx.asked:
        return True, "accounts not requested"
    return bool(ctx.accounts), ("filed accounts on record" if ctx.accounts
                                else "no filed accounts on record")


def _wholly_owned_by_a_company(ctx: Context) -> str | None:
    owns = ctx.reg(Predicate.OWNS)
    corporate = [c for c in owns if c.subject.kind != "person"
                 and (c.qualifiers.get("share") or 0) > 0.5]
    if not corporate:
        return None
    return f"majority holder {corporate[0].subject.label!r} is not a natural person"


def _parent_identifiable(ctx: Context) -> tuple[bool, str]:
    if ctx.chain is None:
        return True, "chain not expanded"
    unres = getattr(ctx.chain, "unresolvable", [])
    if not unres:
        return True, "every corporate node resolved to a register"
    return False, (f"{len(unres)} node(s) carry a name and nothing a register "
                   "can be joined on")


def _control_changed(ctx: Context) -> str | None:
    """Not any ownership update — a change of **control**.

    The first version fired on 38% of companies, large listed banks included, because
    the ejerregister has its own filing cadence and updates alone as a matter
    of course. An expectation that common measures the population.

    What is actually uncommon is the majority holder changing while nothing
    else in the register moves.
    """
    half = 0.5
    majors: dict[date, set[str]] = {}
    for c in ctx.registry:
        if c.predicate is not Predicate.OWNS or not c.valid_from:
            continue
        if c.valid_from > ctx.as_of:
            continue
        share = c.qualifiers.get("share")
        if share is not None and float(share) > half:
            majors.setdefault(c.valid_from, set()).add(c.subject.key)
    if len(majors) < 2:
        return None
    dates = sorted(majors)
    if majors[dates[-1]] == majors[dates[-2]]:
        return None
    return (f"the majority holder changed on {dates[-1]} "
            f"({', '.join(sorted(majors[dates[-2]]))} -> "
            f"{', '.join(sorted(majors[dates[-1]]))})")


def _something_else_moved(ctx: Context) -> tuple[bool, str]:
    half = 0.5
    dates = sorted({c.valid_from for c in ctx.registry
                    if c.predicate is Predicate.OWNS and c.valid_from
                    and c.valid_from <= ctx.as_of
                    and c.qualifiers.get("share") is not None
                    and float(c.qualifiers["share"]) > half})
    if not dates:
        return True, "no change of control"
    when = dates[-1]
    others = {p: [c.valid_from for c in ctx.registry
                  if c.predicate is p and c.valid_from == when]
              for p in (Predicate.HAS_CAPITAL, Predicate.HAS_NAME,
                        Predicate.REGISTERED_AT, Predicate.HAS_ROLE,
                        Predicate.SIGNING_RULE)}
    moved = [str(p) for p, v in others.items() if v]
    if moved:
        return True, f"{', '.join(moved)} changed on the same date"
    return False, "nothing else in the register changed on that date"


def _site_names_an_officer(ctx: Context) -> str | None:
    roles = ctx.web(Predicate.HAS_ROLE)
    if not roles:
        return None
    return f"the site names {len(roles)} person(s) in a senior role"


def _officers_registered(ctx: Context) -> tuple[bool, str]:
    named = {(getattr(c.object, "label", "") or "").casefold()
             for c in ctx.web(Predicate.HAS_ROLE)}
    reg = {(getattr(c.object, "label", "") or "").casefold()
           for c in ctx.reg(Predicate.HAS_ROLE)}
    missing = sorted(n for n in named if n and n not in reg)
    if not missing:
        return True, "every person named on the site holds a registered function"
    return False, (f"{len(missing)} of {len(named)} hold no registered function "
                   f"(e.g. {missing[0]})")


#: The Internet Archive began capturing in 1996. Expecting web content from
#: before that is not a gap in a company's story, it is a gap in history —
#: "earliest capture 45 years after incorporation" was firing on a company
#: founded in 1976.
ARCHIVE_BEGINS = date(1996, 10, 1)


def _has_filed_a_website(ctx: Context) -> str | None:
    """Anchor on when the company **told CVR it had a website**, not on when it
    was incorporated. That is the date from which an archive gap means
    something."""
    w = ctx.one(Predicate.HAS_WEBSITE)
    if not w or not w.object or not w.valid_from:
        return None
    since = max(w.valid_from, ARCHIVE_BEGINS)
    years = (ctx.as_of - since).days // 365
    if years < 3:
        return None
    return f"a website has been on the register since {w.valid_from}"


def _web_presence_covers_its_life(ctx: Context) -> tuple[bool, str]:
    if ctx.archive is None or "wayback" not in ctx.asked:
        return True, "archive not consulted"
    first = getattr(ctx.archive, "first_seen", None)
    if first is None:
        return False, "the archive holds no capture of this site at all"
    w = ctx.one(Predicate.HAS_WEBSITE)
    anchor = max(w.valid_from, ARCHIVE_BEGINS) if (w and w.valid_from) else ARCHIVE_BEGINS
    lag = (first - anchor).days // 365
    if lag <= 2:
        return True, f"archived from {first}, within 2 years of the filing"
    return False, (f"the site has been on the register since {anchor}, but the "
                   f"earliest capture is {first} — {lag} years later")


def _domain_predates_company(ctx: Context) -> str | None:
    if ctx.domain is None or not getattr(ctx.domain, "is_evidence", False):
        return None
    f = ctx.one(Predicate.FOUNDED_ON)
    reg = getattr(ctx.domain, "registered", None)
    if not (f and reg and isinstance(f.object, date) and reg < f.object):
        return None
    return (f"the domain was registered {(f.object - reg).days // 365} years "
            "before the company existed")


def _domain_history_explained(ctx: Context) -> tuple[bool, str]:
    return False, "no source connects the domain's earlier use to this company"


EXPECTATIONS: list[Expectation] = [
    Expectation(Strand.MONEY, "headcount_without_accounts",
                _claimed_headcount, _accounts_present,
                "filed accounts, since a company of that size files them",
                closable_by="regnskaber", consulted=("cvr", "website")),
    Expectation(Strand.OWNERSHIP, "parent_not_identifiable",
                _wholly_owned_by_a_company, _parent_identifiable,
                "a parent that some register can be joined on",
                consulted=("cvr",)),
    Expectation(Strand.TIMELINE, "quiet_transfer",
                _control_changed, _something_else_moved,
                "a capital, name, address, officer or signing-rule change "
                "on the same date, as most transfers carry",
                consulted=("cvr",)),
    Expectation(Strand.PEOPLE, "officer_not_registered",
                _site_names_an_officer, _officers_registered,
                "each named person to hold a registered function",
                consulted=("cvr", "website")),
    Expectation(Strand.PRESENCE, "archive_starts_late",
                _has_filed_a_website, _web_presence_covers_its_life,
                "archived content from within a couple of years of the filing",
                closable_by="wayback", consulted=("cvr",)),
    Expectation(Strand.PRESENCE, "domain_older_than_the_company",
                _domain_predates_company, _domain_history_explained,
                "an account of what the domain was used for before",
                consulted=("cvr", "dkhm")),
]


def gaps(ctx: Context, expectations: Iterable[Expectation] = EXPECTATIONS) -> list[Gap]:
    """Every expectation whose trigger fired and whose check did not hold."""
    out = []
    for e in expectations:
        trig = e.trigger(ctx)
        if not trig:
            continue
        ok, found = e.check(ctx)
        if ok:
            continue
        closable = e.closable_by if e.closable_by not in ctx.asked else ""
        out.append(Gap(strand=e.strand, trigger=trig, expected=e.expected,
                       found=found, consulted=e.consulted, closable_by=closable,
                       name=e.name, base_rate=GAP_BASE_RATES.get(e.name)))
    return out
