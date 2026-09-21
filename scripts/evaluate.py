"""Firing rates per predicate, known-bad vs control. The LR in miniature.

    .venv/bin/python scripts/evaluate.py

Reads only from the raw store — no API calls, no quota.
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from datetime import date

from gleipnir.adapters.opensanctions import SanctionsIndex
from gleipnir.config import settings
from gleipnir.extract.cvr import extract_company
from gleipnir.predicates.core import V
from gleipnir.predicates.cvr_only import ALL_CVR_ONLY, designated_holder
from gleipnir.rawstore import RawStore

AS_OF = date(2026, 8, 27)  # pinned: verdicts must not move with the wall clock

#: Predicates whose value was used, directly or indirectly, to SELECT a cohort.
#: Their ratio is circular and must never be read as discriminating power.
#: The known-bad cohort here was chosen because these companies failed, and the
#: control query filtered on sammensatStatus=NORMAL — so anything measuring
#: "did this company fail" scores perfectly by construction.
CIRCULAR = {"insolvency_or_dissolution"}


def cohorts(store: RawStore) -> dict[str, list[str]]:
    out = defaultdict(list)
    for f in store.fetches():
        if f.source != "cvr" or f.http_status != 200:
            continue
        tag = (f.request_params or {}).get("batch", "other")
        if f.resource_id not in out[tag]:
            out[tag].append(f.resource_id)
    return out


def evaluate(store, index, cvrs):
    """-> {predicate: {V: count}}, plus per-company results."""
    counts = defaultdict(lambda: defaultdict(int))
    per_company = {}
    for cvr in cvrs:
        rec = store.latest("cvr", "virksomhed", cvr)
        claims = extract_company(store.get_json(rec.content_hash),
                                 raw_ref=rec.content_hash, observed_at=str(AS_OF))
        if not claims:
            continue
        rs = [p(claims, AS_OF) for p in ALL_CVR_ONLY]
        if index:
            rs.append(designated_holder(claims, index, AS_OF))
        per_company[cvr] = rs
        for r in rs:
            counts[r.predicate][r.value] += 1
    return counts, per_company


def main() -> int:
    store = RawStore(settings.raw_store_path)
    rec = store.latest("opensanctions", "targets.simple.csv", "sanctions")
    index = SanctionsIndex.from_csv(store.path_of(rec.content_hash)) if rec else None

    groups = cohorts(store)
    bad, ctrl = groups.get("known-bad", []), groups.get("control", [])
    cb, _ = evaluate(store, index, bad)
    cc, _ = evaluate(store, index, ctrl)

    print(f"known-bad n={len(bad)}   control n={len(ctrl)}   as-of {AS_OF}\n")
    print("Rates are over EVALUABLE companies only (TRUE+FALSE+UNKNOWN).")
    print("UNKNOWABLE is excluded from the denominator: a predicate that could not be")
    print("computed is not a predicate that came back clean.\n")
    print(f"{'predicate':<38}{'bad':>12}{'control':>12}{'ratio':>9}")
    print("-" * 71)

    def rate(counts, pred):
        """TRUE over evaluable. UNKNOWABLE never counts as FALSE."""
        c = counts[pred]
        evaluable = c[V.TRUE] + c[V.FALSE] + c[V.UNKNOWN]
        return c[V.TRUE], evaluable

    rows = []
    for pred in sorted(set(cb) | set(cc)):
        b_t, b_n = rate(cb, pred)
        c_t, c_n = rate(cc, pred)
        if b_n == 0 or c_n == 0:
            rows.append((-2.0, pred, b_t, b_n, c_t, c_n, "not evaluable in one cohort"))
            continue
        if b_t == 0 and c_t == 0:
            # A ratio computed from two zeros is smoothing, not evidence.
            rows.append((-1.0, pred, b_t, b_n, c_t, c_n, "never fired — no signal"))
            continue
        ratio = ((b_t + 0.5) / (b_n + 1)) / ((c_t + 0.5) / (c_n + 1))
        if pred in CIRCULAR:
            rows.append((-3.0, pred, b_t, b_n, c_t, c_n,
                         f"CIRCULAR — cohort selected on this; ratio {ratio:.0f} is meaningless"))
        else:
            rows.append((ratio, pred, b_t, b_n, c_t, c_n, ""))

    for ratio, pred, b_t, b_n, c_t, c_n, note in sorted(rows, key=lambda r: -r[0]):
        b = f"{b_t}/{b_n}" + (f" {b_t/b_n:.0%}" if b_n else "")
        c = f"{c_t}/{c_n}" + (f" {c_t/c_n:.0%}" if c_n else "")
        r = f"{ratio:.1f}" if ratio > 0 else "-"
        print(f"{pred:<38}{b:>12}{c:>12}{r:>9}  {note}")

    print("\nCOVERAGE — predicates that could not be evaluated at all")
    for pred in sorted(set(cb) | set(cc)):
        b_u = cb[pred][V.UNKNOWABLE]; c_u = cc[pred][V.UNKNOWABLE]
        b_n = sum(cb[pred].values()) or 1; c_n = sum(cc[pred].values()) or 1
        if b_u or c_u:
            print(f"  {pred:<38} bad {b_u}/{b_n}   control {c_u}/{c_n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
