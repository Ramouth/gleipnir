"""Website vs registry, for one CVR number. Facts only.

    .venv/bin/python scripts/screen_web.py 99000147
"""
from __future__ import annotations

import sys
from datetime import date

from gleipnir.adapters.website import RobotsDisallowed, WebsiteClient, normalise_site
from gleipnir.claims import Predicate, latest
from gleipnir.config import settings
from gleipnir.contradict import Status, pair_claims
from gleipnir.extract.cvr import extract_company
from gleipnir.extract.website import extract_page
from gleipnir.rawstore import RawStore

AS_OF = date(2026, 8, 27)


def main() -> int:
    cvr = sys.argv[1]
    store = RawStore(settings.raw_store_path)
    rec = store.latest("cvr", "virksomhed", cvr)
    if not rec:
        print(f"{cvr} not in raw store"); return 1

    reg = extract_company(store.get_json(rec.content_hash),
                          raw_ref=rec.content_hash, observed_at=str(AS_OF))
    subject = reg[0].subject
    site_claim = latest(reg, Predicate.HAS_WEBSITE)
    if not site_claim:
        print(f"{cvr}  {subject.label}\n  no website filed in CVR -> UNKNOWABLE"); return 0
    url = normalise_site(str(site_claim.object))
    print(f"{cvr}  {subject.label}\n  site: {url}")

    cached = store.latest("website", "page", url)
    if cached:
        body, how = store.get(cached.content_hash), "cached"
    else:
        with WebsiteClient() as w:
            if not w.may_fetch(url):
                print("  robots.txt disallows -> UNKNOWABLE"); return 0
            try:
                resp = w.get(url)
            except (RobotsDisallowed, Exception) as e:
                print(f"  fetch failed: {type(e).__name__}: {e}"); return 1
        r = store.put(payload=resp.body, source="website", resource_type="page",
                      resource_id=url, http_status=resp.http_status,
                      request_params={"final_url": resp.url})
        body, how = resp.body, f"fetched HTTP {resp.http_status}"
    print(f"  {how}, {len(body):,} bytes\n")

    web = extract_page(body, url=url, subject=subject,
                       raw_ref=(cached or r).content_hash, observed_at=str(AS_OF))
    print(f"  EXTRACTED {len(web)} checkable statement(s)")
    for c in web:
        print(f"    {c.predicate:<20}{str(getattr(c.object,'label',c.object))[:38]:<40}"
              f"{c.qualifiers.get('role',''):<26}\"{c.qualifiers['source_text'][:52]}\"")

    pairs = pair_claims(web, reg, as_of=AS_OF)
    print(f"\n  PAIRED against registry ({len(pairs)})")
    print(f"    {'kind':<20}{'predicate':<20}{'declared':<32}{'registered':<28}{'stale':<8}note")
    for p in sorted(pairs, key=lambda p: p.status is Status.CORROBORATED):
        print("    " + p.line())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
