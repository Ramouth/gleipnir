"""What the .dk registry says about cached companies' domains.

    .venv/bin/python scripts/dk_domains.py [limit]

Anonymous WHOIS only — no credential, no holder. Polite: one query at a time.
"""
from __future__ import annotations

import json
import sys
import time
from collections import Counter
from datetime import date

from gleipnir.adapters.dkhostmaster import WhoisClient
from gleipnir.adapters.dns import domain_of
from gleipnir.claims import Predicate, latest
from gleipnir.config import settings
from gleipnir.extract.cvr import extract_company
from gleipnir.rawstore import RawStore

AS_OF = date(2026, 8, 27)


def main() -> int:
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 250
    store = RawStore(settings.raw_store_path)
    rows, seen = [], set()
    for f in store.fetches():
        if f.source != "cvr" or f.resource_type != "virksomhed" or f.resource_id in seen:
            continue
        seen.add(f.resource_id)
        cl = extract_company(store.get_json(f.content_hash), raw_ref=f.content_hash)
        if not cl:
            continue
        site = latest(cl, Predicate.HAS_WEBSITE, AS_OF)
        if not (site and site.object):
            continue
        d = domain_of(str(site.object))
        if not d.endswith(".dk"):
            continue
        nm = latest(cl, Predicate.HAS_NAME, AS_OF)
        fnd = latest(cl, Predicate.FOUNDED_ON, AS_OF)
        rows.append((f.resource_id, str(nm.object)[:32] if nm else "?", d,
                     fnd.object if fnd else None))
    rows = rows[:limit]
    print(f"{len(rows)} cached companies on a .dk domain\n", flush=True)

    c = WhoisClient()
    recs = {}
    for i, (cvr, name, dom, founded) in enumerate(rows):
        cached = store.latest("dkhm", "domain", dom)
        if cached:
            recs[dom] = store.get_json(cached.content_hash)
            continue
        r = c.lookup(dom)
        time.sleep(0.7)                       # polite; the registry logs sessions
        if r is None:
            continue
        store.put(payload=json.dumps(r.to_dict(), sort_keys=True).encode(),
                  source="dkhm", resource_type="domain", resource_id=dom,
                  http_status=200, request_params={"service": "whois-43"})
        recs[dom] = r.to_dict()
        if (i + 1) % 25 == 0:
            print(f"  {i+1}/{len(rows)}", flush=True)

    print(f"\n{len(recs)} .dk records retrieved\n")
    reg = Counter(r.get("registrar") or "(not disclosed)" for r in recs.values())
    print("REGISTRAR")
    for k, n in reg.most_common(10):
        print(f"   {n:>4}  {n/len(recs):>6.1%}  {k[:52]}")

    dnssec = Counter((r.get("dnssec") or "?").split()[0] for r in recs.values())
    print(f"\nDNSSEC: {dict(dnssec)}")

    before, after, same, unknown = [], 0, 0, 0
    for cvr, name, dom, founded in rows:
        r = recs.get(dom)
        if not r or not r.get("registered") or not founded:
            unknown += 1
            continue
        dreg = date.fromisoformat(r["registered"])
        if dreg < founded:
            before.append((cvr, name, dom, dreg, founded, (founded - dreg).days // 365))
        elif dreg > founded:
            after += 1
        else:
            same += 1
    total = len(before) + after + same
    print(f"\nDOMAIN AGE vs COMPANY AGE  (n={total})")
    print(f"   domain registered BEFORE the company existed: {len(before):>4}  {len(before)/total:>6.1%}")
    print(f"   domain registered after incorporation:        {after:>4}  {after/total:>6.1%}")
    print(f"   same day:                                     {same:>4}  {same/total:>6.1%}")
    print(f"   not determinable:                             {unknown:>4}")
    print("\n   largest gaps (domain predates the company):")
    for cvr, name, dom, dreg, founded, yrs in sorted(before, key=lambda r: -r[5])[:10]:
        print(f"      {cvr}  {name:<34}{dom:<26}domain {dreg}  company {founded}  {yrs}y")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
