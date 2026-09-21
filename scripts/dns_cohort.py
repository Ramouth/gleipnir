"""Observe DNS for every cached company with a filed website, and measure.

    .venv/bin/python scripts/dns_cohort.py [workers]

Reads CVR from the raw store only — no registry calls. DNS is free.
"""
from __future__ import annotations

import concurrent.futures as cf
import json
import sys
from collections import Counter, defaultdict
from datetime import date

from gleipnir.adapters.dns import DnsClient, classify_provider, domain_of
from gleipnir.claims import Predicate, latest
from gleipnir.config import settings
from gleipnir.extract.cvr import extract_company
from gleipnir.rawstore import RawStore

AS_OF = date(2026, 8, 27)


def targets(store):
    seen, out = set(), []
    for f in store.fetches():
        if f.source != "cvr" or f.resource_type != "virksomhed": continue
        if f.resource_id in seen: continue
        seen.add(f.resource_id)
        cl = extract_company(store.get_json(f.content_hash), raw_ref=f.content_hash)
        if not cl: continue
        site = latest(cl, Predicate.HAS_WEBSITE, AS_OF)
        emp = latest(cl, Predicate.EMPLOYS, AS_OF)
        if site and site.object:
            d = domain_of(str(site.object))
            if d and "." in d:
                out.append((f.resource_id, d, int(emp.object) if emp else None))
    return out


def main() -> int:
    workers = int(sys.argv[1]) if len(sys.argv) > 1 else 12
    store = RawStore(settings.raw_store_path)
    rows = targets(store)
    print(f"{len(rows)} cached companies with a filed website\n", flush=True)

    client = DnsClient()
    obs: dict[str, object] = {}

    def work(item):
        cvr, domain, emp = item
        cached = store.latest("dns", "observation", domain)
        if cached:
            from gleipnir.adapters.dns import DnsObservation
            d = store.get_json(cached.content_hash)
            return cvr, domain, emp, DnsObservation(
                domain=d["domain"], observed_at=d["observed_at"],
                mx=tuple(d["mx"]), ns=tuple(d["ns"]), a=tuple(d["a"]),
                spf=d["spf"], dmarc=d["dmarc"], errors=tuple(d["errors"]))
        return cvr, domain, emp, client.observe(domain)

    with cf.ThreadPoolExecutor(max_workers=workers) as pool:
        for cvr, domain, emp, o in pool.map(work, rows):
            if not store.latest("dns", "observation", domain):
                store.put(payload=json.dumps(o.to_dict(), sort_keys=True).encode(),
                          source="dns", resource_type="observation",
                          resource_id=domain, http_status=200,
                          request_params={"types": ["MX", "NS", "A", "TXT", "_dmarc"]})
            obs[cvr] = (domain, emp, o)

    n = len(obs)
    live = {k: v for k, v in obs.items() if not any("NXDOMAIN" in e for e in v[2].errors)}
    print(f"{n} domains observed · {n - len(live)} do not resolve at all\n")

    prov = Counter(classify_provider(v[2].mx) for v in live.values())
    print(f"{'MAIL PROVIDER':<16}{'n':>6}{'share':>9}")
    print("-" * 32)
    for p, c in prov.most_common():
        print(f"{p:<16}{c:>6}{c/len(live):>9.1%}")

    no_mx = [k for k, v in live.items() if not v[2].mx]
    print(f"\nno MX at all:          {len(no_mx):>4}  {len(no_mx)/len(live):>6.1%}")
    spf = sum(1 for v in live.values() if v[2].spf)
    dmarc = sum(1 for v in live.values() if v[2].dmarc)
    print(f"no SPF:                {len(live)-spf:>4}  {(len(live)-spf)/len(live):>6.1%}")
    print(f"no DMARC:              {len(live)-dmarc:>4}  {(len(live)-dmarc)/len(live):>6.1%}")

    # shared infrastructure, conditioned on provider
    by_mx = defaultdict(list)
    for k, (d, e, o) in live.items():
        if o.mx and classify_provider(o.mx) == "other":
            by_mx[o.mx_hash].append((k, d, o.mx))
    shared = {h: v for h, v in by_mx.items() if len(v) > 1}
    print(f"\nSHARED MX, bulk providers excluded")
    print(f"  companies on a non-bulk mail host: "
          f"{sum(len(v) for v in by_mx.values())}")
    print(f"  clusters of 2+ sharing one host:   {len(shared)}")
    for h, members in sorted(shared.items(), key=lambda kv: -len(kv[1]))[:8]:
        host = ", ".join(sorted(members[0][2])[:2])
        print(f"    {len(members)}x  {host[:52]:<54}{[m[0] for m in members][:4]}")

    by_ns = defaultdict(list)
    for k, (d, e, o) in live.items():
        if o.ns:
            by_ns[o.ns_hash].append(k)
    ns_clusters = {h: v for h, v in by_ns.items() if len(v) > 1}
    print(f"\nshared nameservers: {len(ns_clusters)} cluster(s), "
          f"largest {max((len(v) for v in ns_clusters.values()), default=0)}")

    sized = [(k, v) for k, v in live.items() if v[1] is not None]
    big_no_mx = [k for k, v in sized if v[1] >= 10 and not v[2].mx]
    print(f"\nfiled 10+ employees AND no MX: {len(big_no_mx)} of "
          f"{sum(1 for _, v in sized if v[1] >= 10)} such companies")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
