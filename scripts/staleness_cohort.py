"""Base rate for the discriminator architecture.md §10 calls the best free one.

    .venv/bin/python scripts/staleness_cohort.py [limit]

A stale page three weeks after a registry change is expected; four years after,
it is telling a different story deliberately. That needs the date the page last
changed — one Wayback CDX request per domain — against the date the registry
last moved. Neither number has ever been measured at cohort scale.

Internet Archive only. No registry calls.
"""
from __future__ import annotations

import json
import sys
import time
from collections import Counter
from datetime import date

import httpx

from gleipnir.adapters.dns import domain_of
from gleipnir.adapters.wayback import History, Capture, WaybackClient
from gleipnir.claims import Predicate, latest
from gleipnir.config import settings
from gleipnir.extract.cvr import extract_company
from gleipnir.rawstore import RawStore

AS_OF = date(2026, 8, 27)
#: Registry changes a website could plausibly be expected to reflect.
WATCHED = (Predicate.HAS_NAME, Predicate.REGISTERED_AT, Predicate.HAS_ROLE,
           Predicate.OWNS, Predicate.HAS_LEGAL_FORM)


def registry_last_changed(claims) -> date | None:
    dates = [c.valid_from for c in claims
             if c.predicate in WATCHED and c.valid_from and c.valid_from <= AS_OF]
    return max(dates) if dates else None


def main() -> int:
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 200
    store = RawStore(settings.raw_store_path)
    rows, seen = [], set()
    for f in store.fetches():
        if f.source != "cvr" or f.resource_type != "virksomhed" or f.resource_id in seen:
            continue
        seen.add(f.resource_id)
        cl = extract_company(store.get_json(f.content_hash), raw_ref=f.content_hash,
                             observed_at=str(AS_OF))
        if not cl:
            continue
        site = latest(cl, Predicate.HAS_WEBSITE, AS_OF)
        if not (site and site.object):
            continue
        d = domain_of(str(site.object))
        rc = registry_last_changed(cl)
        nm = latest(cl, Predicate.HAS_NAME, AS_OF)
        if d and rc:
            rows.append((f.resource_id, str(nm.object)[:30] if nm else "?", d, rc))
    rows = rows[:limit]
    print(f"{len(rows)} companies with a domain and a dated registry change\n", flush=True)

    http = httpx.Client(follow_redirects=True, timeout=90.0)
    c = WaybackClient(http)
    out, archived, empty = [], 0, 0
    for i, (cvr, name, dom, rc) in enumerate(rows):
        cached = store.latest("wayback", "history", dom)
        if cached:
            d = store.get_json(cached.content_hash)
            h = History(url=d["url"], observed_at=d["observed_at"],
                        captures=tuple(Capture(**x) for x in d["captures"]))
        else:
            try:
                h = c.history(dom)
            except Exception:
                time.sleep(1.0)
                continue
            store.put(payload=json.dumps(h.to_dict(), sort_keys=True).encode(),
                      source="wayback", resource_type="history", resource_id=dom,
                      http_status=200, request_params={"collapse": "digest"})
            time.sleep(0.4)
        if not h.versions:
            empty += 1
            continue
        archived += 1
        out.append((cvr, name, dom, rc, h.last_changed, h.stale_days(rc, AS_OF),
                    len(h.versions)))
        if (i + 1) % 40 == 0:
            print(f"  {i+1}/{len(rows)}", flush=True)

    print(f"\n{archived} archived · {empty} with no capture the archive kept\n")
    if not out:
        return 0
    stale = [r for r in out if r[5] is not None and r[5] > 0]
    fresh = [r for r in out if r[5] is not None and r[5] <= 0]
    print(f"site changed AFTER the registry did (ordinary): {len(fresh):>4}  "
          f"{len(fresh)/len(out):>6.1%}")
    print(f"site UNCHANGED since before the registry moved:  {len(stale):>4}  "
          f"{len(stale)/len(out):>6.1%}")
    if stale:
        buckets = Counter()
        for r in stale:
            d = r[5]
            buckets["under 90 days" if d < 90 else
                    "90 days – 1 year" if d < 365 else
                    "1 – 2 years" if d < 730 else
                    "2 – 4 years" if d < 1460 else "over 4 years"] += 1
        print("\nHOW STALE, among those that are")
        for k in ("under 90 days", "90 days – 1 year", "1 – 2 years",
                  "2 – 4 years", "over 4 years"):
            if buckets[k]:
                print(f"   {k:<20}{buckets[k]:>4}  {buckets[k]/len(out):>6.1%} of all archived")
        print("\nstalest — the page has not moved since well before the register did")
        for cvr, name, dom, rc, lc, days, v in sorted(stale, key=lambda r: -r[5])[:10]:
            print(f"   {cvr}  {name:<32}{dom:<26}registry {rc}  page {lc}  {days//365}y")
    med = sorted(r[6] for r in out)[len(out)//2]
    print(f"\nmedian archived versions per domain: {med}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
