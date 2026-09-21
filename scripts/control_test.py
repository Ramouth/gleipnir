"""Does infrastructure jurisdiction separate designated entities from ordinary ones?

Arm A: 251 ordinary Danish companies (cached).
Arm B: designated entities with a published domain.

    .venv/bin/python scripts/control_test.py [limit] [workers]
"""
from __future__ import annotations

import concurrent.futures as cf
import sys
from collections import Counter
from datetime import date

from gleipnir.adapters.dns import (
    AsnClient, DnsClient, DnsObservation, domain_of, jurisdictions,
)
from gleipnir.claims import Predicate, latest
from gleipnir.config import settings
from gleipnir.extract.cvr import extract_company
from gleipnir.rawstore import RawStore

AS_OF = date(2026, 8, 27)
WATCH = {"RU", "BY", "IR", "KP", "SY", "CN", "HK"}


def danish_arm(store, asn):
    obs = {}
    for f in store.fetches():
        if f.source == "dns" and f.resource_type == "observation":
            d = store.get_json(f.content_hash)
            obs[d["domain"]] = DnsObservation(
                domain=d["domain"], observed_at=d["observed_at"], mx=tuple(d["mx"]),
                ns=tuple(d["ns"]), a=tuple(d["a"]), spf=d["spf"], dmarc=d["dmarc"],
                errors=tuple(d["errors"]))
    keep, seen = set(), set()
    for f in store.fetches():
        if f.source != "cvr" or f.resource_type != "virksomhed" or f.resource_id in seen:
            continue
        seen.add(f.resource_id)
        cl = extract_company(store.get_json(f.content_hash), raw_ref=f.content_hash)
        if not cl: continue
        site = latest(cl, Predicate.HAS_WEBSITE, AS_OF)
        if site and site.object:
            d = domain_of(str(site.object))
            if d in obs: keep.add(d)
    return [obs[d] for d in sorted(keep)]


def measure(observations, asn, label):
    live = [o for o in observations if o.a or o.mx]
    web, mail, hits = Counter(), Counter(), []
    for o in live:
        j = jurisdictions(o, asn)
        for c in j["web_countries"]: web[c] += 1
        for c in j["mail_countries"]: mail[c] += 1
        h = (set(j["web_countries"]) | set(j["mail_countries"])) & WATCH
        if h: hits.append((o.domain, sorted(h), j["web_countries"], j["mail_countries"]))
    return {"label": label, "n": len(observations), "live": len(live),
            "web": web, "mail": mail, "hits": hits}


def main() -> int:
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 900
    workers = int(sys.argv[2]) if len(sys.argv) > 2 else 32
    store = RawStore(settings.raw_store_path)
    asn = AsnClient()

    rec = store.latest("opensanctions", "designated_domains", "sanctions")
    designated = store.get_json(rec.content_hash)[:limit]
    print(f"resolving {len(designated)} designated domains…", flush=True)
    dns = DnsClient(timeout=3.0, lifetime=5.0)
    with cf.ThreadPoolExecutor(max_workers=workers) as pool:
        b_obs = list(pool.map(lambda r: dns.observe(r["domain"]), designated))

    a = measure(danish_arm(store, asn), asn, "ordinary Danish companies")
    b = measure(b_obs, asn, "designated entities")

    print(f"\n{'':<34}{'ordinary DK':>16}{'designated':>16}")
    print("-" * 66)
    print(f"{'domains':<34}{a['n']:>16,}{b['n']:>16,}")
    print(f"{'resolve at all':<34}{a['live']:>16,}{b['live']:>16,}")
    for arm in (a, b):
        arm["rate"] = len(arm["hits"]) / arm["live"] if arm["live"] else 0
    print(f"{'IN A WATCHED JURISDICTION':<34}"
          f"{len(a['hits']):>10,} {a['rate']:>5.1%}{len(b['hits']):>10,} {b['rate']:>5.1%}")

    print(f"\n{'top mail jurisdictions':<34}")
    for arm in (a, b):
        top = ", ".join(f"{c} {n/max(sum(arm['mail'].values()),1):.0%}"
                        for c, n in arm["mail"].most_common(5))
        print(f"  {arm['label']:<32}{top}")

    print(f"\nfirst designated hits ({len(b['hits'])} total):")
    by = Counter(tuple(h[1]) for h in b["hits"])
    for k, n in by.most_common(8):
        print(f"   {n:>4}x  {list(k)}")
    for dom, h, w, m in b["hits"][:10]:
        print(f"   {dom[:38]:<40}hit={h} web={w} mail={m}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
