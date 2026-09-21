"""Predicate vectors over the bulk scans, deduplicated and stratified.

Two defects this exists to prevent, both found by measurement:

**Duplicates.** The stratified scan's first four starting points fell inside the
ascending scan's range, so 8,000 of 49,000 rows were the same company twice.
Every rate computed over the raw rows double-weighted them. Dedupe by CVR first.

**Owner count is a confounder, and a large one.** Mean predicates firing, by
number of filed owners: 1 → 0.08, 2 → 0.49, 3 → 1.45, 6+ → 1.88. Within the
3-owner stratum, `subthreshold_aggregate_over_50` fires 51.3% of the time and
`ownership_residual_unaccounted` 72.0% — both are near-automatic consequences of
having three owners rather than independent evidence. An unconditioned base rate
for either is a measurement of cap-table size.

So base rates are computed **per owner-count stratum**, and a predicate must be
compared against the stratum its subject falls in.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal

OWNERSHIP_ORGS = {"EJERREGISTER", "REELLEEJERE"}
ROUND_SHARES = {Decimal(x) for x in
                ("1.0", "0.5", "0.3333", "0.1", "0.25", "0.05", "0.2",
                 "0.6667", "0.15", "0.9", "0.0")}
INFRASTRUCTURE_MIN = 200
NOMINEE_MIN = 100
PREDICATES = ("nominee_density", "voting_exceeds_equity",
              "majority_owner_unresolvable", "unusual_ownership_percentage",
              "subthreshold_aggregate_over_50", "ownership_residual_unaccounted")


@dataclass
class Row:
    cvr: str
    owners: int
    fired: dict[str, bool]

    @property
    def n(self) -> int:
        return sum(self.fired.values())


def _bulk_pages(store, tags=("calib", "strat")):
    seen: set[str] = set()
    for f in store.fetches():
        if (f.source != "cvr" or f.resource_type != "bulk_page"
                or not f.resource_id.startswith(tags) or f.resource_id in seen):
            continue
        seen.add(f.resource_id)
        yield store.get_json(f.content_hash)


def directorship_counts(store) -> dict[str, int]:
    """person -> distinct companies with a management role. Deduplicated."""
    per: dict[str, set[str]] = defaultdict(set)
    for page in _bulk_pages(store):
        for hit in page:
            v = hit["_source"]["Vrvirksomhed"]
            cvr = str(v.get("cvrNummer"))
            for rel in v.get("deltagerRelation") or []:
                d = rel.get("deltager") or {}
                if d.get("enhedstype") != "PERSON":
                    continue
                for org in rel.get("organisationer") or []:
                    if org.get("hovedtype") == "LEDELSESORGAN":
                        per[str(d.get("enhedsNummer"))].add(cvr)
    return {k: len(v) for k, v in per.items()}


def build_rows(store) -> list[Row]:
    dirs = directorship_counts(store)
    infra = {k for k, n in dirs.items() if n >= INFRASTRUCTURE_MIN}
    out: dict[str, Row] = {}
    for page in _bulk_pages(store):
        for hit in page:
            v = hit["_source"]["Vrvirksomhed"]
            cvr = str(v.get("cvrNummer"))
            if cvr in out:
                continue                       # dedupe: first occurrence wins
            eq: dict[str, Decimal] = {}
            vt: dict[str, Decimal] = {}
            has = False
            anden_max = Decimal(0)
            unusual = False
            max_dir = 0
            for rel in v.get("deltagerRelation") or []:
                d = rel.get("deltager") or {}
                et = d.get("enhedstype")
                k = str(d.get("enhedsNummer"))
                for org in rel.get("organisationer") or []:
                    names = {(n.get("navn") or "").upper().replace(" ", "")
                             for n in (org.get("organisationsNavn") or [])}
                    if (org.get("hovedtype") == "LEDELSESORGAN"
                            and et == "PERSON" and k not in infra):
                        max_dir = max(max_dir, dirs.get(k, 0))
                    if not (names & OWNERSHIP_ORGS):
                        continue
                    for md in org.get("medlemsData") or []:
                        for a in md.get("attributter") or []:
                            t = a.get("type")
                            if t not in ("EJERANDEL_PROCENT",
                                         "EJERANDEL_STEMMERET_PROCENT"):
                                continue
                            cur = [x for x in (a.get("vaerdier") or [])
                                   if not (x.get("periode") or {}).get("gyldigTil")]
                            if not cur:
                                continue
                            try:
                                dec = Decimal(str(cur[-1]["vaerdi"]))
                            except Exception:
                                continue
                            if t == "EJERANDEL_PROCENT":
                                has = True
                                eq[k] = max(eq.get(k, Decimal(0)), dec)
                                if dec not in ROUND_SHARES:
                                    unusual = True
                                if et == "ANDEN_DELTAGER":
                                    anden_max = max(anden_max, dec)
                            else:
                                vt[k] = max(vt.get(k, Decimal(0)), dec)
            if not has:
                continue
            under = [x for x in eq.values() if x < Decimal("0.50")]
            out[cvr] = Row(cvr=cvr, owners=len(eq), fired={
                "nominee_density": max_dir >= NOMINEE_MIN,
                "voting_exceeds_equity": any(
                    vt.get(k, Decimal(0)) - eq.get(k, Decimal(0)) >= Decimal("0.0001")
                    for k in vt),
                "majority_owner_unresolvable": anden_max > Decimal("0.50"),
                "unusual_ownership_percentage": unusual,
                "subthreshold_aggregate_over_50":
                    len(under) >= 2 and sum(under, Decimal(0)) > Decimal("0.50"),
                "ownership_residual_unaccounted":
                    sum(eq.values(), Decimal(0)) < Decimal("0.9999"),
            })
    return list(out.values())


def stratified_rates(rows: list[Row]) -> dict[int, dict[str, float]]:
    """{owner_count_bucket: {predicate: rate}}. Bucket 6 means 6 or more."""
    buckets: dict[int, list[Row]] = defaultdict(list)
    for r in rows:
        buckets[min(r.owners, 6)].append(r)
    return {b: {p: sum(1 for r in g if r.fired[p]) / len(g) for p in PREDICATES}
            | {"_n": len(g)} for b, g in sorted(buckets.items())}
