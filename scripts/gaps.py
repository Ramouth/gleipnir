"""Gaps in the story, across every cached company.

    .venv/bin/python scripts/gaps.py [limit]

An expectation that fires on most companies is measuring the population, not
the company. That is the first thing to check about any of them.
"""
from __future__ import annotations

import sys
from collections import Counter, defaultdict
from datetime import date

from gleipnir.adapters.dkhostmaster import DomainRecord
from gleipnir.adapters.dns import domain_of
from gleipnir.adapters.wayback import Capture, History
from gleipnir.chain import expand
from gleipnir.claims import Predicate, latest
from gleipnir.config import settings
from gleipnir.extract.cvr import extract_company
from gleipnir.extract.website import extract_page
from gleipnir.narrative import Context, gaps
from gleipnir.rawstore import RawStore

AS_OF = date(2026, 8, 27)


def main() -> int:
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 400
    store = RawStore(settings.raw_store_path)

    def load(cvr):
        r = store.latest("cvr", "virksomhed", cvr)
        return extract_company(store.get_json(r.content_hash), raw_ref=r.content_hash,
                               observed_at=str(AS_OF)) if r else None

    seen, rows = set(), []
    for f in store.fetches():
        if f.source != "cvr" or f.resource_type != "virksomhed" or f.resource_id in seen:
            continue
        seen.add(f.resource_id)
        rows.append(f.resource_id)
    rows = rows[:limit]

    fired = Counter(); examples = defaultdict(list); per_company = Counter()
    agenda = Counter(); n = 0
    for cvr in rows:
        reg = load(cvr)
        if not reg:
            continue
        n += 1
        asked = {"cvr"}
        ctx = Context(cvr=cvr, as_of=AS_OF, registry=reg)

        site = latest(reg, Predicate.HAS_WEBSITE, AS_OF)
        dom = domain_of(str(site.object)) if site and site.object else ""
        if dom:
            url = "https://" + dom
            page = store.latest("website", "page", url) or store.latest(
                "website", "page", f"https://www.{dom}")
            if page:
                asked.add("website")
                ctx.website = extract_page(store.get(page.content_hash), url=url,
                                           subject=reg[0].subject,
                                           raw_ref=page.content_hash,
                                           observed_at=str(AS_OF))
            arc = store.latest("wayback", "history", dom)
            if arc:
                asked.add("wayback")
                d = store.get_json(arc.content_hash)
                ctx.archive = History(url=d["url"], observed_at=d["observed_at"],
                                      captures=tuple(Capture(**c) for c in d["captures"]))
            dk = store.latest("dkhm", "domain", dom)
            if dk:
                asked.add("dkhm")
                d = store.get_json(dk.content_hash)
                ctx.domain = DomainRecord(
                    domain=d["domain"], observed_at=d["observed_at"], source=d["source"],
                    registered=date.fromisoformat(d["registered"]) if d["registered"] else None,
                    registrar=d.get("registrar"), nameservers=tuple(d.get("nameservers") or []),
                    raw=d.get("raw", ""))
        ctx.chain = expand(cvr, as_of=AS_OF, load_claims=load, max_depth=2, max_nodes=8)
        ctx.asked = asked

        g = gaps(ctx)
        per_company[len(g)] += 1
        for x in g:
            key = f"{x.strand}/{x.expected[:34]}"
            (agenda if x.is_agenda else fired)[key] += 1
            if len(examples[key]) < 3:
                nm = latest(reg, Predicate.HAS_NAME, AS_OF)
                examples[key].append((cvr, str(nm.object)[:30] if nm else "?", x.found[:64]))

    print(f"{n} companies\n")
    print(f"{'GAPS IN THE ACCOUNT':<52}{'n':>5}{'rate':>8}")
    print("-" * 66)
    for k, c in fired.most_common():
        print(f"{k:<52}{c:>5}{c/n:>8.1%}")
    if agenda:
        print(f"\n{'ANSWERABLE BY SPENDING (not gaps)':<52}{'n':>5}{'rate':>8}")
        for k, c in agenda.most_common():
            print(f"{k:<52}{c:>5}{c/n:>8.1%}")
    print(f"\ngaps per company: " + ", ".join(
        f"{k}: {v} ({v/n:.0%})" for k, v in sorted(per_company.items())))
    print("\nEXAMPLES")
    for k, rows_ in list(examples.items())[:6]:
        print(f"  {k}")
        for cvr, name, found in rows_:
            print(f"     {cvr}  {name:<32}{found}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
