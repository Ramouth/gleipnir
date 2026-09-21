"""Denominators from the bulk pages. Reads the raw store only — no API calls.

    .venv/bin/python scripts/calibrate.py
"""
from __future__ import annotations

from collections import Counter, defaultdict
from decimal import Decimal

from gleipnir.config import settings
from gleipnir.rawstore import RawStore

OWNERSHIP_ORGS = {"EJERREGISTER", "REELLEEJERE"}
THRESHOLDS = [Decimal(t) for t in ("0.25", "0.3333", "0.50", "0.6667")]


def pages(store: RawStore, tag: str):
    seen = set()
    for f in store.fetches():
        if f.source == "cvr" and f.resource_type == "bulk_page" \
                and f.resource_id.startswith(tag) and f.resource_id not in seen:
            seen.add(f.resource_id)
            yield store.get_json(f.content_hash)


def main() -> int:
    store = RawStore(settings.raw_store_path)
    shares: list[Decimal] = []
    votes_by_co: dict[str, dict[str, Decimal]] = {}
    equity_by_co: dict[str, dict[str, Decimal]] = {}
    roles_per_person: Counter = Counter()
    person_type: dict[str, str] = {}
    companies = 0
    cvr_min, cvr_max = None, None
    with_register = 0
    foreign_owner = 0

    for page in pages(store, "calib"):
        for hit in page:
            v = hit["_source"]["Vrvirksomhed"]
            cvr = str(v.get("cvrNummer")).zfill(8)
            companies += 1
            cvr_min = cvr if cvr_min is None else min(cvr_min, cvr)
            cvr_max = cvr if cvr_max is None else max(cvr_max, cvr)
            eq: dict[str, Decimal] = {}
            vt: dict[str, Decimal] = {}
            has_reg = False
            for rel in v.get("deltagerRelation") or []:
                d = rel.get("deltager") or {}
                key = str(d.get("enhedsNummer"))
                etype = d.get("enhedstype") or ""
                person_type[key] = etype
                if etype == "VIRKSOMHED" and not d.get("forretningsnoegle"):
                    foreign_owner += 1
                for org in rel.get("organisationer") or []:
                    names = {(n.get("navn") or "").upper().replace(" ", "")
                             for n in (org.get("organisationsNavn") or [])}
                    is_own = bool(names & OWNERSHIP_ORGS)
                    if org.get("hovedtype") == "LEDELSESORGAN" and etype == "PERSON":
                        roles_per_person[key] += 1
                    for md in org.get("medlemsData") or []:
                        for at_ in md.get("attributter") or []:
                            t = at_.get("type")
                            if t not in ("EJERANDEL_PROCENT", "EJERANDEL_STEMMERET_PROCENT"):
                                continue
                            vals = at_.get("vaerdier") or []
                            cur = [x for x in vals if not (x.get("periode") or {}).get("gyldigTil")]
                            if not cur:
                                continue
                            try:
                                dec = Decimal(str(cur[-1].get("vaerdi")))
                            except Exception:
                                continue
                            if t == "EJERANDEL_PROCENT":
                                has_reg = True
                                shares.append(dec)
                                eq[key] = max(eq.get(key, Decimal(0)), dec)
                            else:
                                vt[key] = max(vt.get(key, Decimal(0)), dec)
            if has_reg:
                with_register += 1
            equity_by_co[cvr] = eq
            votes_by_co[cvr] = vt

    print(f"{companies:,} companies · CVR range {cvr_min}–{cvr_max}")
    print(f"{with_register:,} ({with_register/companies:.0%}) have a filed ownership register")
    print(f"{len(shares):,} filed ownership percentages\n")

    # ── bunching ────────────────────────────────────────────────────────────
    print("BUNCHING — mass in each 1pp bucket below a legal threshold vs above")
    print(f"{'threshold':>10}{'[t-0.02,t)':>12}{'[t-0.01,t)':>12}{'exactly t':>11}"
          f"{'(t,t+0.01]':>12}{'ratio below/above':>19}")
    for t in THRESHOLDS:
        below2 = sum(1 for s in shares if t - Decimal("0.02") <= s < t)
        below1 = sum(1 for s in shares if t - Decimal("0.01") <= s < t)
        at_t = sum(1 for s in shares if s == t)
        above1 = sum(1 for s in shares if t < s <= t + Decimal("0.01"))
        ratio = (below1 / above1) if above1 else float("inf")
        print(f"{str(t):>10}{below2:>12,}{below1:>12,}{at_t:>11,}{above1:>12,}{ratio:>19.2f}")

    top = Counter(shares).most_common(12)
    print("\nMOST COMMON FILED PERCENTAGES")
    for val, n in top:
        print(f"   {str(val):>10}  {n:>7,}  {n/len(shares):>6.1%}")

    # ── voting vs equity ────────────────────────────────────────────────────
    div = 0
    considered = 0
    big = 0
    for cvr, eq in equity_by_co.items():
        vt = votes_by_co.get(cvr) or {}
        if not eq and not vt:
            continue
        considered += 1
        for k, v in vt.items():
            if v - eq.get(k, Decimal(0)) >= Decimal("0.0001"):
                div += 1
                if v - eq.get(k, Decimal(0)) >= Decimal("0.10"):
                    big += 1
                break
    print(f"\nVOTING vs EQUITY  ({considered:,} companies with a register)")
    print(f"   any divergence >= 0.0001 : {div:,}  ({div/considered:.2%})")
    print(f"   divergence >= 0.10       : {big:,}  ({big/considered:.2%})")

    # ── sub-threshold aggregation ───────────────────────────────────────────
    agg = sum(1 for eq in equity_by_co.values()
              if len([v for v in eq.values() if v < Decimal("0.50")]) >= 2
              and sum((v for v in eq.values() if v < Decimal("0.50")), Decimal(0)) > Decimal("0.50"))
    print(f"\nSUB-THRESHOLD AGGREGATION  {agg:,} / {considered:,} ({agg/considered:.2%})")

    # ── nominee density ─────────────────────────────────────────────────────
    counts = Counter(roles_per_person.values())
    total_people = len(roles_per_person)
    print(f"\nMANAGEMENT ROLES PER NATURAL PERSON  ({total_people:,} people in sample)")
    cum = 0
    for k in sorted(counts):
        cum += counts[k]
        if k <= 6 or counts[k] > 3:
            print(f"   {k:>3} role(s): {counts[k]:>7,}   cumulative {cum/total_people:>7.3%}")
    print(f"   max observed: {max(counts) if counts else 0}")
    print(f"\nCOMPANY PARTICIPANTS WITH NO CVR NUMBER (chain leaves DK): {foreign_owner:,}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
