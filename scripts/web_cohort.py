"""Base rates for website-vs-registry contradictions.

`architecture.md` §10: "a system that cries fraud at every LinkedIn/CVR mismatch
will be worse than useless". This measures how often each mismatch type occurs
among ordinary Danish companies, which is the number that decides whether any of
them can carry a finding.
"""
from __future__ import annotations

import sys
from collections import Counter, defaultdict
from datetime import date

from gleipnir.adapters.website import WebsiteClient, normalise_site
from gleipnir.claims import Predicate, latest
from gleipnir.config import settings
from gleipnir.contradict import Status, pair_claims
from gleipnir.extract.cvr import extract_company
from gleipnir.extract.website import extract_page
from gleipnir.rawstore import RawStore

AS_OF = date(2026, 8, 27)


def main() -> int:
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 120
    store = RawStore(settings.raw_store_path)
    targets = []
    seen = set()
    for f in store.fetches():
        if f.source != "cvr" or f.resource_type != "virksomhed": continue
        if (f.request_params or {}).get("batch") != "websites": continue
        if f.resource_id in seen: continue
        seen.add(f.resource_id); targets.append((f.resource_id, f.content_hash))
    targets = targets[:limit]

    kinds = Counter()          # total conflicts, one per pair
    companies = Counter()      # companies exhibiting each type, one per company
    per_company = Counter(); status = Counter()
    stmt = Counter(); fetched = 0; failed = 0; nosite = 0
    examples = defaultdict(list)

    with WebsiteClient(delay=0.5) as w:
        for cvr, h in targets:
            reg = extract_company(store.get_json(h), raw_ref=h, observed_at=str(AS_OF))
            if not reg: continue
            site = latest(reg, Predicate.HAS_WEBSITE, AS_OF)
            if not site or not site.object:
                nosite += 1; continue
            url = normalise_site(str(site.object))
            cached = store.latest("website", "page", url)
            if cached:
                body = store.get(cached.content_hash); ch = cached.content_hash
            else:
                try:
                    if not w.may_fetch(url):
                        failed += 1; continue
                    resp = w.get(url)
                    if resp.http_status != 200 or not resp.body:
                        failed += 1; continue
                except Exception:
                    failed += 1; continue
                r = store.put(payload=resp.body, source="website", resource_type="page",
                              resource_id=url, http_status=resp.http_status,
                              request_params={"final_url": resp.url})
                body = resp.body; ch = r.content_hash
            fetched += 1
            wc = extract_page(body, url=url, subject=reg[0].subject, raw_ref=ch,
                              observed_at=str(AS_OF))
            for c in wc: stmt[str(c.predicate)] += 1
            pairs = pair_claims(wc, reg, as_of=AS_OF)
            hits = set()
            for p in pairs:
                status[str(p.status)] += 1
                if p.kind:
                    key = f"{p.predicate}:{p.kind}"
                    kinds[key] += 1; hits.add(key)
                    if len(examples[key]) < 3:
                        examples[key].append((cvr, str(p.declared)[:34], str(p.registered)[:26]))
            for k in hits:
                companies[k] += 1
            per_company[len(hits)] += 1

    print(f"\n{fetched} sites fetched · {failed} unreachable · {nosite} no site filed\n")
    print("CHECKABLE STATEMENTS EXTRACTED")
    for k, n in stmt.most_common():
        print(f"   {k:<24}{n:>6}   {n/fetched:>6.2f} per site")
    print(f"\nPAIR OUTCOMES  {dict(status)}")
    # Two different numerators, and conflating them produced a "221%" rate:
    # 438 address conflicts across 198 sites is 2.2 per site, not 221% of them.
    print(f"\n{'CONTRADICTION TYPE':<42}{'companies':>10}{'rate':>7}{'total':>8}{'per co':>8}")
    print("-" * 76)
    for k, n in kinds.most_common():
        c = companies[k]
        print(f"   {k:<39}{c:>10}{c/fetched:>7.1%}{n:>8}{n/max(c,1):>8.1f}")
    print(f"\nDISTINCT CONTRADICTION TYPES PER COMPANY")
    for k in sorted(per_company):
        print(f"   {k}: {per_company[k]:>4}  {per_company[k]/fetched:>6.1%}")
    print("\nEXAMPLES")
    for k, rows in list(examples.items())[:6]:
        print(f"   {k}")
        for cvr, d, r in rows:
            print(f"      {cvr}  declared={d!r:<36} registered={r!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
