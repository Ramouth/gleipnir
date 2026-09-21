"""Outcome backtest: did the booleans fire BEFORE the red flag appeared?

`docs/loop-log.md` iteration 1 ran the predicates over eight companies picked
from news coverage of their own collapse and recorded why the numbers were
worthless. Three defects, and this module exists to close all three.

**1. Selection on the outcome.** `insolvency_or_dissolution` scored 59x because
the cohort was chosen for having failed and the controls were filtered to
`sammensatStatus=NORMAL`. Any predicate reading lifecycle state scores perfectly
by construction. Here the outcome IS the label, so every predicate computed from
it is excluded by name (`LABEL_PREDICATES`) rather than reported with a caveat.

**2. Hindsight.** A predicate evaluated on today's document sees a register that
has already recorded the collapse — the curator appointed, the owners struck.
The register carries a dated status history, so the collapse date is a fact, and
every predicate here is evaluated at `event - lead`, on the register as it stood
before anything had happened. A case whose status at that date is not already
NORMAL is dropped, not repaired.

**3. Unmatched controls.** Iteration 6 measured that owner count mechanically
drives two of the predicates, so an unmatched comparison measures cap-table size.
Controls are matched 1:1 on legal form, owner count at the as-of date, and age
band, drawn without replacement, deterministically.

What remains uncontrolled, stated because it cannot be fixed by fetching more:
CVR gives **valid time, not transaction time**. A correction filed after the
collapse and backdated to before it is visible at the as-of date. One fetch of a
current document cannot separate the two, so this is a hindsight channel that
stays open and is reported rather than closed.
"""
from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Iterable, Iterator

from gleipnir.claims import Claim, Predicate, at
from gleipnir.extract.cvr import extract_company
from gleipnir.predicates.core import Result, V

#: Computed from the field that defines the label. Reporting a ratio for these
#: would restate the cohort definition as a discovery.
LABEL_PREDICATES = frozenset({"insolvency_or_dissolution"})

#: Registry status values that are the red flag. Deliberately NOT every
#: non-NORMAL value: voluntary liquidation, merger and demerger are ordinary
#: ends to a company's life, and counting them as adverse outcomes would put
#: successful acquisitions in the positive cohort.
_ADVERSE_MARKERS = ("TVANGSOPLØS", "KONKURS")


def is_adverse(status: str | None) -> bool:
    return bool(status) and any(m in status.upper().replace(" ", "")
                                for m in _ADVERSE_MARKERS)


@dataclass
class Subject:
    """One company, its outcome, and its claims. Claims are parsed once.

    Every predicate is a pure function of (claims, as-of date), so a subject can
    be re-evaluated at any date without re-parsing, which is what makes the
    lead-time sweep affordable.
    """

    cvr: str
    name: str | None
    cohort: str
    legal_form: str
    founded: date | None
    claims: list[Claim] = field(repr=False, default_factory=list)
    #: First transition into forced dissolution or bankruptcy, and its label.
    event: date | None = None
    event_status: str | None = None
    #: (from, to) of every dated registry status, oldest first.
    status_history: list[tuple[date | None, date | None, str]] = field(
        repr=False, default_factory=list)

    def status_at(self, on: date) -> str | None:
        """Registry status in force at `on`, or None if none covers it.

        Reads the *dated* `virksomhedsstatus` history, never
        `sammensatStatus` — that one is filed with no period at all, so it is
        true of every as-of date and is the single largest hindsight leak in
        the document. `LABEL_PREDICATES` covers the one predicate that reads it.
        """
        best: tuple[date, str] | None = None
        for frm, to, value in self.status_history:
            if frm and frm > on:
                continue
            if to and to < on:
                continue
            key = frm or date.min
            if best is None or key >= best[0]:
                best = (key, value)
        return best[1] if best else None

    def owners_at(self, on: date) -> int:
        """Distinct holders in the ownership register at `on`."""
        return len({c.subject.key for c in at(self.claims, Predicate.OWNS, on)})

    def age_at(self, on: date) -> int | None:
        if not self.founded:
            return None
        return (on - self.founded).days // 365

    def existed_at(self, on: date) -> bool:
        return self.founded is not None and self.founded <= on


def _unwrap(hit: dict[str, Any]) -> dict[str, Any]:
    doc = hit.get("_source", hit)
    return doc.get("Vrvirksomhed", doc)


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except (ValueError, TypeError):
        return None


def subject_from_hit(hit: dict[str, Any], cohort: str, blob: str) -> Subject | None:
    doc = _unwrap(hit)
    cvr = doc.get("cvrNummer")
    if cvr is None:
        return None
    meta = doc.get("virksomhedMetadata") or {}
    history: list[tuple[date | None, date | None, str]] = []
    for entry in doc.get("virksomhedsstatus") or []:
        period = entry.get("periode") or {}
        history.append((_parse_date(period.get("gyldigFra")),
                        _parse_date(period.get("gyldigTil")),
                        str(entry.get("status") or "")))
    history.sort(key=lambda t: (t[0] or date.min, t[2]))

    event = event_status = None
    for frm, _to, value in history:
        if is_adverse(value) and frm:
            event, event_status = frm, value
            break

    subject = Subject(
        cvr=str(cvr).zfill(8),
        name=((meta.get("nyesteNavn") or {}).get("navn")),
        cohort=cohort,
        legal_form=str((meta.get("nyesteVirksomhedsform") or {}).get("langBeskrivelse") or ""),
        founded=_parse_date(meta.get("stiftelsesDato")),
        event=event,
        event_status=event_status,
        status_history=history,
    )
    subject.claims = extract_company(doc, raw_ref=blob, observed_at=str(date.min))
    return subject


def load_cohorts(store, tag: str) -> dict[str, list[Subject]]:
    """Read every stored page written under `tag`, deduplicated by CVR.

    Deduplicated because iteration 6 found 8,000 rows of the 49,000-company
    corpus were the same company fetched twice, and every rate published before
    that double-weighted the overlap.
    """
    out: dict[str, list[Subject]] = defaultdict(list)
    seen: set[str] = set()
    for fetch in sorted(store.fetches(), key=lambda f: f.resource_id):
        if fetch.resource_type != "bulk_page" or not fetch.resource_id.startswith(f"{tag}:"):
            continue
        cohort = (fetch.request_params or {}).get("cohort") or "unlabelled"
        for hit in store.get_json(fetch.content_hash):
            subject = subject_from_hit(hit, cohort, fetch.content_hash)
            if subject is None or subject.cvr in seen:
                continue
            seen.add(subject.cvr)
            out[cohort].append(subject)
    return dict(out)


# ── as-of directorship index ─────────────────────────────────────────────────

def role_intervals(store, tags: Iterable[str]) -> dict[str, list[tuple[str, date | None, date | None]]]:
    """person -> [(company, from, to)] for every management role, with periods.

    `calibration.build_directorship_index` counts roles without reading their
    periods, which is correct for a present-day base rate and wrong for a
    backtest: it would credit a 2024 appointment to a 2016 as-of date. That is
    hindsight, and it is the one leak in this module that CAN be closed, so it is.
    """
    prefixes = tuple(f"{t}:" for t in tags)
    out: dict[str, list[tuple[str, date | None, date | None]]] = defaultdict(list)
    seen_pages: set[str] = set()
    for fetch in store.fetches():
        if (fetch.resource_type != "bulk_page"
                or not fetch.resource_id.startswith(prefixes)
                or fetch.resource_id in seen_pages):
            continue
        seen_pages.add(fetch.resource_id)
        for hit in store.get_json(fetch.content_hash):
            doc = _unwrap(hit)
            cvr = str(doc.get("cvrNummer"))
            for relation in doc.get("deltagerRelation") or []:
                participant = relation.get("deltager") or {}
                if participant.get("enhedstype") != "PERSON":
                    continue
                key = str(participant.get("enhedsNummer"))
                for org in relation.get("organisationer") or []:
                    if org.get("hovedtype") != "LEDELSESORGAN":
                        continue
                    for member in org.get("medlemsData") or []:
                        for attribute in member.get("attributter") or []:
                            if attribute.get("type") != "FUNKTION":
                                continue
                            for value in attribute.get("vaerdier") or []:
                                period = value.get("periode") or {}
                                out[key].append((
                                    cvr,
                                    _parse_date(period.get("gyldigFra")),
                                    _parse_date(period.get("gyldigTil"))))
    return dict(out)


def directorships_at(intervals: dict[str, list[tuple[str, date | None, date | None]]],
                     on: date) -> dict[str, int]:
    """person -> distinct companies where a management role was in force at `on`.

    A lower bound, like the index it replaces: a person is counted only in the
    companies this corpus sampled. That direction is safe — it can understate
    nominee density, never invent it.
    """
    counts: dict[str, set[str]] = defaultdict(set)
    for person, rows in intervals.items():
        for cvr, frm, to in rows:
            if frm and frm > on:
                continue
            if to and to < on:
                continue
            counts[person].add(cvr)
    return {k: len(v) for k, v in counts.items()}


# ── matching ─────────────────────────────────────────────────────────────────

def age_band(years: int | None) -> str:
    if years is None:
        return "?"
    for upper, label in ((2, "0-2"), (5, "3-5"), (10, "6-10"), (20, "11-20")):
        if years <= upper:
            return label
    return "21+"


def owner_band(count: int) -> int:
    return min(count, 6)


@dataclass(frozen=True)
class Pair:
    case: Subject
    control: Subject
    as_of: date
    key: tuple


def eligible(subject: Subject, as_of: date) -> bool:
    """Alive, and not already flagged, at the as-of date.

    The second half is what makes the test a prediction. A company already in
    forced dissolution on the day we evaluate it is not being predicted.
    """
    return subject.existed_at(as_of) and subject.status_at(as_of) == "NORMAL"


#: Which structural variables a control must share with its case. Running the
#: same comparison at each level is the measurement, not a robustness check:
#: matching on owner count ALSO matches on whether an ownership register was
#: filed at all, so a signal that disappears between DATE and FULL was a fact
#: about cap-table size rather than about the outcome.
MATCH_LEVELS: dict[str, tuple[str, ...]] = {
    "date": (),
    "form+age": ("form", "age"),
    "full": ("form", "age", "owners"),
}


def _key(subject: Subject, as_of: date, keys: tuple[str, ...]) -> tuple:
    out = []
    for k in keys:
        if k == "form":
            out.append(subject.legal_form)
        elif k == "age":
            out.append(age_band(subject.age_at(as_of)))
        elif k == "owners":
            out.append(owner_band(subject.owners_at(as_of)))
    return tuple(out)


def clean_controls(controls: list[Subject]) -> tuple[list[Subject], int]:
    """Controls with no forced dissolution or bankruptcy anywhere in their history.

    `sammensatStatus = NORMAL` today is not the same as never having failed: a
    company struck off and later reinstated reads NORMAL now. Leaving those in
    puts genuine positives in the control arm and biases every ratio toward 1.
    """
    kept = [s for s in controls if s.event is None]
    return kept, len(controls) - len(kept)


def match_pairs(cases: list[Subject], controls: list[Subject], lead_days: int,
                window: tuple[date, date] | None = None,
                keys: tuple[str, ...] = ("form", "age", "owners"),
                ) -> tuple[list[Pair], dict[str, int]]:
    """1:1 matched pairs, drawn without replacement, deterministically.

    Iterating in CVR order and taking the first free control gives the same
    pairing on every run without a seed — `architecture.md` §9 requires a report
    to be reproducible from its inputs, and a random matcher would make the
    denominator itself depend on the wall clock.

    Every arm controls calendar time: the control is evaluated at the *case's*
    as-of date, so a rate difference can never come from the two cohorts being
    read in different years.
    """
    buckets: dict[tuple, list[Subject]] = defaultdict(list)
    for control in sorted(controls, key=lambda s: s.cvr):
        buckets[_key(control, date.max, ("form",)) if "form" in keys else ()].append(control)

    used: set[str] = set()
    pairs: list[Pair] = []
    dropped: dict[str, int] = defaultdict(int)
    for case in sorted(cases, key=lambda s: s.cvr):
        if case.event is None:
            dropped["no dated adverse transition"] += 1
            continue
        as_of = date.fromordinal(case.event.toordinal() - lead_days)
        if window and not (window[0] <= as_of <= window[1]):
            dropped["as-of outside window"] += 1
            continue
        if not eligible(case, as_of):
            dropped["case not alive and NORMAL at as-of"] += 1
            continue
        want = _key(case, as_of, keys)
        pool = buckets[(case.legal_form,) if "form" in keys else ()]
        chosen = None
        for control in pool:
            if control.cvr in used or not eligible(control, as_of):
                continue
            if _key(control, as_of, keys) != want:
                continue
            chosen = control
            break
        if chosen is None:
            dropped["no matching control"] += 1
            continue
        used.add(chosen.cvr)
        pairs.append(Pair(case=case, control=chosen, as_of=as_of, key=want))
    return pairs, dict(dropped)


# ── counting ─────────────────────────────────────────────────────────────────

@dataclass
class Tally:
    """TRUE over evaluable, keeping UNKNOWABLE out of the denominator.

    Iteration 1's bug B3: counting UNKNOWABLE as evaluated deflated every rate,
    and counting it as FALSE would report a coverage hole as a clean check.
    """

    true: int = 0
    false: int = 0
    unknown: int = 0
    unknowable: int = 0

    def add(self, value: V) -> None:
        setattr(self, value.value.lower(), getattr(self, value.value.lower()) + 1)

    @property
    def evaluable(self) -> int:
        return self.true + self.false + self.unknown

    @property
    def rate(self) -> float | None:
        return self.true / self.evaluable if self.evaluable else None


def katz_ci(a: int, n1: int, c: int, n2: int, z: float = 1.96) -> tuple[float, float] | None:
    """95% CI for a ratio of two proportions, log method.

    Undefined when either numerator is zero — reported as absent rather than
    smoothed, because iteration 1's bug B4 was a smoothed ratio of two zeros
    printed as 4.56.
    """
    if a == 0 or c == 0 or n1 == 0 or n2 == 0:
        return None
    log_ratio = math.log((a / n1) / (c / n2))
    se = math.sqrt(1 / a - 1 / n1 + 1 / c - 1 / n2)
    return math.exp(log_ratio - z * se), math.exp(log_ratio + z * se)


def evaluate(subject: Subject, as_of: date, predicates, directorships: dict[str, int]
             ) -> list[Result]:
    """Every predicate at one date. Label-defining predicates are never run."""
    from gleipnir.predicates.cvr_only import nominee_density

    out = [p(subject.claims, as_of) for p in predicates
           if p.__name__ not in _LABEL_FUNCS]
    out.append(nominee_density(subject.claims, directorships, as_of))
    return out


_LABEL_FUNCS = frozenset({"insolvency_status"})
