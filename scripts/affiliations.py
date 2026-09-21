"""Institutional connections for one named person — universities and companies.

    .venv/bin/python scripts/affiliations.py "Jane Doe"
    .venv/bin/python scripts/affiliations.py <openalex-author-id> --expand
    .venv/bin/python scripts/affiliations.py "Name" --against=CN

Research-security discovery: where a person said they worked when they
published, and who they published with. For a defence-contract need-to-know,
where a visiting appointment or a speaking invitation that became an affiliation
line is a matter of public record.

**A name gives candidates, not a person.** OpenAlex clusters authorships into
author records and a common name collects other people's papers, so this prints
every candidate with its works count and affiliations for a human to adjudicate.
Nothing here resolves an identity. `--expand` takes an OpenAlex author id, which
is the thing a human resolved to.

**Every rate is printed beside its fact.** `--against=CN` reports co-affiliation
with a country alongside the measured rate of DK-to-that-country collaboration,
because collaboration is ordinary and a bare count is not information.

**It records institutions, never people's origins.** The country on a line is
the institution's. This says nothing about anyone's nationality and cannot be
made to.
"""
from __future__ import annotations

import collections
import json
import sys
from datetime import datetime, timezone

from gleipnir.adapters.openalex import OpenAlexClient, paged
from gleipnir.claims import Predicate
from gleipnir.config import settings
from gleipnir.extract.openalex import (
    co_stated_countries, extract_author, extract_works,
)
from gleipnir.rawstore import RawStore

DENOMINATOR_SINCE = "2024-01-01"


def candidates(client, store, name: str, observed: str) -> int:
    resp = client.search_author(name)
    rec = store.put(payload=resp.body, source="openalex", resource_type="authors",
                    resource_id=f"search:{name}", http_status=resp.http_status,
                    request_params=resp.query)
    claims = extract_author(json.loads(resp.body), raw_ref=rec.content_hash,
                            observed_at=observed, query=name)
    if not claims:
        print(f"\nNo author record matches {name!r}.")
        print("  This is a coverage statement, not a clean result. Most company")
        print("  officers have no academic footprint at all — an empty answer")
        print("  here is the expected default and means nothing.")
        return 1

    by_author: dict = collections.OrderedDict()
    for c in claims:
        by_author.setdefault(c.subject.key, []).append(c)

    print(f"\n{len(by_author)} candidate author record(s) for {name!r} "
          f"— candidates, not an identity")
    print("  OpenAlex clusters authorships by name; a common name collects other")
    print("  people's papers. Resolve one by hand, then re-run with --expand.\n")
    for key, cs in by_author.items():
        first = cs[0]
        print(f"  {first.subject.label}   [{key.split(':')[-1]}]   "
              f"{first.qualifiers.get('works_count')} works"
              + (f"   ORCID {first.qualifiers['orcid']}"
                 if first.qualifiers.get("orcid") else ""))
        seen = set()
        for c in sorted(cs, key=lambda c: (c.valid_from or c.observed_at or "")):
            label = (c.object.label, c.qualifiers.get("country_code"))
            if label in seen:
                continue
            seen.add(label)
            years = sorted({str(x.valid_from.year) for x in cs
                            if x.object.key == c.object.key and x.valid_from})
            print(f"      {c.object.label[:44]:<46} "
                  f"{c.qualifiers.get('country_code',''):<3} "
                  f"{c.qualifiers.get('institution_type',''):<11} "
                  f"{', '.join(years[:6])}")
        print()
    return 0


def expand(client, store, author_id: str, observed: str, against: str) -> int:
    affiliations, collaborations = [], []
    for resp in paged(client, author_id, pages=2):
        rec = store.put(payload=resp.body, source="openalex",
                        resource_type="works", resource_id=author_id,
                        http_status=resp.http_status, request_params=resp.query)
        if resp.http_status != 200:
            continue
        claims = extract_works(json.loads(resp.body), subject_author_id=author_id,
                               raw_ref=rec.content_hash, observed_at=observed)
        affiliations += [c for c in claims
                         if c.predicate is Predicate.HAS_AFFILIATION]
        collaborations += [c for c in claims
                           if c.predicate is Predicate.CO_AUTHORED_WITH]

    if not affiliations and not collaborations:
        print(f"\nNo peer-reviewed works on file for {author_id}.")
        return 1

    print(f"\n{author_id}   {affiliations[0].subject.label if affiliations else ''}")
    print(f"{len(affiliations)} affiliation statements · "
          f"{len(collaborations)} co-authorships · peer-reviewed journal articles only")

    print("\n  OWN AFFILIATIONS, AS STATED ON PUBLICATIONS")
    own = collections.Counter()
    for c in affiliations:
        own[(c.object.label, c.qualifiers.get("country_code"),
             c.qualifiers.get("institution_type"))] += 1
    for (label, cc, kind), n in own.most_common():
        print(f"      {n:>4}  {label[:44]:<46} {cc:<3} {kind}")

    print("\n  CO-AUTHOR INSTITUTIONS  — collaboration, and nothing more")
    co = collections.Counter()
    for c in collaborations:
        for i in c.qualifiers.get("co_author_institutions") or []:
            if i.get("country_code"):
                co[(i["country_code"], i.get("type"))] += 1
    for (cc, kind), n in co.most_common(12):
        print(f"      {n:>4}  {cc:<3} {kind}")

    dual = co_stated_countries(affiliations, f"openalex:{author_id}")
    print(f"\n  SIMULTANEOUS AFFILIATIONS  ({len(dual)} publication(s))")
    if not dual:
        print("      none — no publication lists institutions in more than one country")
    for work_id, entries in list(dual.items())[:12]:
        e = entries[0]
        print(f"      {e['date']}  {'+'.join(e['countries'])}  {e['doi'] or work_id}")

    if against:
        home = "dk"
        resp = client.co_affiliation_count(home, against, DENOMINATOR_SINCE)
        total = json.loads(resp.body).get("meta", {}).get("count")
        hits = sum(1 for entries in dual.values()
                   for e in entries if against.upper() in e["countries"])
        print(f"\n  AGAINST {against.upper()}")
        print(f"      this person: {hits} publication(s) stating both "
              f"{home.upper()} and {against.upper()}")
        print(f"      the population: {total:,} works with both a {home.upper()} and a "
              f"{against.upper()} institution, published since {DENOMINATOR_SINCE}")
        print(f"      {home.upper()}-{against.upper()} collaboration is ordinary. This is a chain")
        print( "      fact for a human to weigh, not a finding and not a colour.")
    return 0


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = {a.split("=", 1)[0]: a.split("=", 1)[-1]
             for a in sys.argv[1:] if a.startswith("--")}
    if not args:
        print(__doc__)
        return 2

    observed = datetime.now(timezone.utc).isoformat()
    store = RawStore(settings.raw_store_path)
    client = OpenAlexClient(mailto=flags.get("--mailto", ""))
    try:
        if "--expand" in flags:
            return expand(client, store, args[0], observed,
                          flags.get("--against", ""))
        return candidates(client, store, args[0], observed)
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
