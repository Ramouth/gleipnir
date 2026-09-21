"""Did the booleans fire before the red flag, or only after it?

    .venv/bin/python scripts/backtest.py --lead=365
    .venv/bin/python scripts/backtest.py --sweep

Reads only the raw store — no API calls. Prints facts: 2x2 counts, rates over
evaluable subjects, the ratio and its interval, and what could not be evaluated.
No score, no narrative, no adjectives.

Every predicate is run at three matching levels, and the three columns ARE the
result. `date` controls only calendar time; `full` also matches legal form, age
band and owner count. A ratio that survives to `full` is about the outcome; one
that collapses was about how big the cap table is — which iteration 6 measured
as the variable driving most of this set.
"""
from __future__ import annotations

import sys
from collections import Counter, defaultdict
from datetime import date

from gleipnir.backtest import (LABEL_PREDICATES, MATCH_LEVELS, Tally,
                               clean_controls, directorships_at, evaluate,
                               katz_ci, load_cohorts, match_pairs, role_intervals)
from gleipnir.calibration import STRUCTURAL_ONLY
from gleipnir.config import settings
from gleipnir.predicates.cvr_only import ALL_CVR_ONLY
from gleipnir.rawstore import RawStore

#: Pre-registered before these numbers were computed, and taken unchanged from
#: `docs/loop-log.md` iteration 4, where it rejected `subthreshold_aggregate`.
#: Fixing it in advance is what stops it becoming whatever the data supports.
LR_REJECT_BELOW = 1.5

WINDOW = (date(2005, 1, 1), date(2025, 8, 27))


def tallies(pairs, predicates, index_by_year):
    case_t: dict[str, Tally] = defaultdict(Tally)
    ctrl_t: dict[str, Tally] = defaultdict(Tally)
    case_stack, ctrl_stack = Counter(), Counter()
    for pair in pairs:
        directorships = index_by_year[pair.as_of.year]
        for subject, table, stack in ((pair.case, case_t, case_stack),
                                      (pair.control, ctrl_t, ctrl_stack)):
            fired = 0
            for result in evaluate(subject, pair.as_of, predicates, directorships):
                table[result.predicate].add(result.value)
                if (result.value.value == "TRUE"
                        and result.predicate not in STRUCTURAL_ONLY
                        and result.predicate not in LABEL_PREDICATES):
                    fired += 1
            stack[fired] += 1
    return case_t, ctrl_t, case_stack, ctrl_stack


def note_for(predicate, ratio, ci):
    if predicate in LABEL_PREDICATES:
        return "LABEL — defines the cohort"
    if predicate in STRUCTURAL_ONLY:
        return "structural"
    if ci is None:
        return ""
    if ci[0] <= 1.0 <= ci[1]:
        return "interval spans 1"
    if ratio < LR_REJECT_BELOW:
        return f"below LR floor {LR_REJECT_BELOW}"
    return ""


def arm_row(case_t, ctrl_t, predicate):
    a, n1 = case_t[predicate].true, case_t[predicate].evaluable
    c, n2 = ctrl_t[predicate].true, ctrl_t[predicate].evaluable
    if n1 == 0 or n2 == 0:
        return a, n1, c, n2, None, None, "not evaluable"
    if a == 0 and c == 0:
        return a, n1, c, n2, None, None, "never fired"
    ratio = (a / n1) / (c / n2) if c else None
    ci = katz_ci(a, n1, c, n2)
    return a, n1, c, n2, ratio, ci, note_for(predicate, ratio or 0.0, ci)


def report(label, arms):
    """arms: [(name, pairs, case_t, ctrl_t, case_stack, ctrl_stack)]"""
    print(f"\n{'=' * 108}")
    print(f"{label}")
    print(f"{'=' * 108}")
    for name, pairs, *_ in arms:
        if pairs:
            years = Counter(p.as_of.year for p in pairs)
            print(f"  {name:<10}{len(pairs):>6} pairs   as-of {min(years)}-{max(years)}")
        else:
            print(f"  {name:<10}     0 pairs")

    predicates = sorted({p for _, _, ct, kt, *_ in arms for p in set(ct) | set(kt)})
    print(f"\n{'predicate':<38}{'arm':<10}{'cases':>15}{'controls':>15}"
          f"{'ratio':>8}{'95% CI':>16}   note")
    print("-" * 108)
    for predicate in predicates:
        for i, (name, pairs, case_t, ctrl_t, *_) in enumerate(arms):
            a, n1, c, n2, ratio, ci, note = arm_row(case_t, ctrl_t, predicate)
            cases = f"{a}/{n1}" + (f" {a / n1:5.1%}" if n1 else "")
            controls = f"{c}/{n2}" + (f" {c / n2:5.1%}" if n2 else "")
            shown = f"{ratio:.2f}" if ratio else "-"
            interval = f"[{ci[0]:.2f}, {ci[1]:.2f}]" if ci else "-"
            print(f"{predicate if i == 0 else '':<38}{name:<10}{cases:>15}"
                  f"{controls:>15}{shown:>8}{interval:>16}   {note}")
        print()

    name, pairs, case_t, ctrl_t, case_stack, ctrl_stack = arms[-1]
    print(f"NOT EVALUABLE, and NOT counted as clean — {name} arm")
    print(f"{'predicate':<38}{'UNKNOWN c/k':>18}{'UNKNOWABLE c/k':>20}")
    for predicate in predicates:
        cu, ku = case_t[predicate].unknown, ctrl_t[predicate].unknown
        ca, ka = case_t[predicate].unknowable, ctrl_t[predicate].unknowable
        if cu or ku or ca or ka:
            print(f"{predicate:<38}{f'{cu} / {ku}':>18}{f'{ca} / {ka}':>20}")

    print(f"\nABSENT FILING — UNKNOWABLE as its own measured quantity, {name} arm")
    print("the register holds no such filing at the as-of date. Four-valued logic is")
    print("what keeps this visible: as a boolean it would read FALSE, i.e. clean.")
    print(f"{'predicate':<38}{'cases':>15}{'controls':>15}{'ratio':>8}{'95% CI':>16}")
    n = len(pairs) or 1
    for predicate in predicates:
        a, c = case_t[predicate].unknowable, ctrl_t[predicate].unknowable
        if not (a or c):
            continue
        ci = katz_ci(a, n, c, n)
        ratio = (a / n) / (c / n) if c else None
        print(f"{predicate:<38}{f'{a}/{n} {a / n:5.1%}':>15}{f'{c}/{n} {c / n:5.1%}':>15}"
              f"{f'{ratio:.2f}' if ratio else '-':>8}"
              f"{f'[{ci[0]:.2f}, {ci[1]:.2f}]' if ci else '-':>16}")

    print(f"\nSTACK — non-structural predicates firing at once, {name} arm")
    total = len(pairs) or 1
    print(f"  {'fired':>6}{'cases':>16}{'controls':>16}{'ratio':>8}")
    for n in sorted(set(case_stack) | set(ctrl_stack)):
        a, c = case_stack[n], ctrl_stack[n]
        r = f"{a / c:.2f}" if c else "-"
        print(f"  {n:>6}{f'{a} {a / total:5.1%}':>16}{f'{c} {c / total:5.1%}':>16}{r:>8}")


def build(store, tag):
    cohorts = load_cohorts(store, tag)
    controls, dropped = clean_controls(cohorts.get("control", []))
    print(f"corpus tag {tag!r}   as-of window {WINDOW[0]}..{WINDOW[1]}")
    for name, subjects in sorted(cohorts.items()):
        dated = sum(1 for s in subjects if s.event)
        print(f"  {name:<22}{len(subjects):>6} companies, {dated:>6} with a dated "
              f"forced-dissolution or bankruptcy transition")
    print(f"  {dropped} control(s) dropped: NORMAL today but struck off or bankrupt "
          f"at some point — a recovered failure is not a control")

    intervals = role_intervals(store, ("calib", "strat", "outcome", "probe"))
    index_by_year = {y: directorships_at(intervals, date(y, 7, 1))
                     for y in range(WINDOW[0].year, WINDOW[1].year + 1)}
    print(f"  as-of directorship index: {len(intervals):,} people over "
          f"{len(index_by_year)} yearly snapshots, role periods honoured; counts "
          f"are lower bounds — a person is counted only where this corpus sampled")
    return cohorts, controls, index_by_year


def main() -> int:
    flags = {a.split("=", 1)[0]: a.split("=", 1)[-1] for a in sys.argv[1:] if a.startswith("--")}
    tag = flags.get("--tag", "outcome")
    sweep = flags.get("--sweep")
    leads = ([int(x) for x in sweep.split(",")] if sweep and sweep != "--sweep"
             else [0, 365, 1095, 1825] if "--sweep" in flags
             else [int(flags.get("--lead", 365))])

    store = RawStore(settings.raw_store_path)
    cohorts, controls, index_by_year = build(store, tag)
    predicates = list(ALL_CVR_ONLY)

    for lead in leads:
        print(f"\n\n########  LEAD {lead} DAYS — register as it stood {lead} days "
              f"before the transition  ########")
        for name, cases in sorted(cohorts.items()):
            if name == "control":
                continue
            arms = []
            for arm, keys in MATCH_LEVELS.items():
                pairs, dropped = match_pairs(cases, controls, lead, WINDOW, keys)
                if arm == "full":
                    drop_note = "; ".join(f"{n} {r}" for r, n in
                                          sorted(dropped.items(), key=lambda kv: -kv[1]))
                arms.append((arm, pairs, *tallies(pairs, predicates, index_by_year)))
            report(f"{name}  ·  lead {lead}d", arms)
            print(f"\ndropped from the full arm: {drop_note}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
