"""Where does Danish corporate infrastructure actually sit?

    .venv/bin/python scripts/geo_cohort.py [workers]

Reads cached DNS observations, resolves ASN and country via Team Cymru.
No registry calls, no credentials.
"""
from __future__ import annotations

import concurrent.futures as cf
import sys
from collections import Counter, defaultdict
from datetime import date

from gleipnir.adapters.dns import AsnClient, DnsObservation, jurisdictions
from gleipnir.claims import Predicate, latest
from gleipnir.config import settings
from gleipnir.extract.cvr import extract_company
from gleipnir.rawstore import RawStore

AS_OF = date(2026, 8, 27)
#: Jurisdictions the threat model treats as designation-adjacent or
#: sanctions-relevant for INFRASTRUCTURE. Versioned reference data, not a
#: hardcoded truth — and about where servers sit, never about people.
WATCH = {"RU", "BY", "IR", "KP", "SY", "CN", "HK"}


def main() -> int:
    workers = int(sys.argv[1]) if len(sys.argv) > 1 else 16
    store = RawStore(settings.raw_store_path)

    names, seen = {}, set()
    for f in store.fetches():
        if f.source != "cvr" or f.resource_type != "virksomhed" or f.resource_id in seen:
            continue
        seen.add(f.resource_id)
        cl = extract_company(store.get_json(f.content_hash), raw_ref=f.content_hash)
        if not cl: continue
        nm = latest(cl, Predicate.HAS_NAME, AS_OF)
        emp = latest(cl, Predicate.EMPLOYS, AS_OF)
        site = latest(cl, Predicate.HAS_WEBSITE, AS_OF)
        if site and site.object:
            from gleipnir.adapters.dns import domain_of
            d = domain_of(str(site.object))
            if d:
                names[d] = (f.resource_id, str(nm.object)[:32] if nm else "?",
                            int(emp.object) if emp else None)

    obs = {}
    for f in store.fetches():
        if f.source == "dns" and f.resource_type == "observation":
            d = store.get_json(f.content_hash)
            if d["domain"] in names:
                obs[d["domain"]] = DnsObservation(
                    domain=d["domain"], observed_at=d["observed_at"],
                    mx=tuple(d["mx"]), ns=tuple(d["ns"]), a=tuple(d["a"]),
                    spf=d["spf"], dmarc=d["dmarc"], errors=tuple(d["errors"]))

    print(f"{len(obs)} domains with cached DNS\n", flush=True)
    asn = AsnClient()

    def work(item):
        dom, o = item
        return dom, jurisdictions(o, asn)

    results = {}
    with cf.ThreadPoolExecutor(max_workers=workers) as pool:
        for dom, j in pool.map(work, obs.items()):
            results[dom] = j

    web = Counter(); mail = Counter(); cdn = Counter()
    web_any = 0; mail_any = 0; behind = 0
    flagged = []
    for dom, j in results.items():
        for c in j["web_countries"]: web[c] += 1
        for c in j["mail_countries"]: mail[c] += 1
        for p in j["web_cdn"]: cdn[p] += 1
        web_any += bool(j["web_countries"])
        mail_any += bool(j["mail_countries"])
        behind += bool(j["web_behind_cdn"])
        hit = (set(j["web_countries"]) | set(j["mail_countries"])) & WATCH
        if hit:
            cvr, nm, emp = names[dom]
            flagged.append((cvr, nm, emp, dom, sorted(hit),
                            j["web_countries"], j["mail_countries"], j["web_cdn"]))

    print(f"{'WEB (A record)':<22}{'n':>5}{'share':>9}   {'MAIL (MX)':<22}{'n':>5}{'share':>9}")
    print("-" * 76)
    wl, ml = web.most_common(12), mail.most_common(12)
    for i in range(max(len(wl), len(ml))):
        a = f"{wl[i][0]:<22}{wl[i][1]:>5}{wl[i][1]/web_any:>9.1%}" if i < len(wl) else " " * 36
        b = f"{ml[i][0]:<22}{ml[i][1]:>5}{ml[i][1]/mail_any:>9.1%}" if i < len(ml) else ""
        print(f"{a}   {b}")

    print(f"\nweb served entirely from behind a CDN: {behind}/{web_any} "
          f"({behind/web_any:.1%}) — country unreadable for these")
    print(f"CDN edges seen: {dict(cdn.most_common(6))}")

    print(f"\n=== INFRASTRUCTURE IN A WATCHED JURISDICTION  ({len(flagged)}) ===")
    if not flagged:
        print("   none")
    for cvr, nm, emp, dom, hit, w, m, c in sorted(flagged):
        print(f"   {cvr:<11}{nm:<34}{str(emp):>4} emp  {dom:<26}"
              f"hit={hit} web={w}{'/'+','.join(c) if c else ''} mail={m}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
