"""Page the CVR register to build denominators.

    .venv/bin/python scripts/bulk_scan.py --pages=20 --size=1000

One page is one API request. This is what makes calibration affordable: at 1000
records per call the statistics cost ~20 requests, not ~20,000.

**Field-filtered, unlike a screen.** A screen fetches whole documents so a parser
fix is a reparse; this fetches only the fields the statistics read, because
storing 20,000 complete documents to compute one distribution is ~1.2GB for
nothing. Each page is still stored as a content-hashed blob with its exact query,
so the aggregate remains reproducible and reparseable within those fields — and
the field list is recorded so what was NOT collected is visible.
"""
from __future__ import annotations

import json
import sys

from gleipnir.adapters.cvr import COMPANY_INDEX, CvrClient
from gleipnir.config import settings
from gleipnir.rawstore import RawStore

#: Participant *addresses* are ~80% of a filtered document's bytes and no
#: statistic here reads them, so they are excluded by path. Participant names are
#: excluded too: nominee density keys on `enhedsNummer`, and collecting 20,000
#: companies' worth of personal names to compute a distribution would be
#: gratuitous under §11's minimisation rule.
FIELDS = [
    "Vrvirksomhed.cvrNummer",
    "Vrvirksomhed.virksomhedMetadata.sammensatStatus",
    "Vrvirksomhed.virksomhedMetadata.stiftelsesDato",
    "Vrvirksomhed.virksomhedMetadata.nyesteVirksomhedsform.langBeskrivelse",
    "Vrvirksomhed.virksomhedMetadata.nyesteAarsbeskaeftigelse.antalAnsatte",
    "Vrvirksomhed.deltagerRelation.deltager.enhedsNummer",
    "Vrvirksomhed.deltagerRelation.deltager.enhedstype",
    "Vrvirksomhed.deltagerRelation.deltager.forretningsnoegle",
    "Vrvirksomhed.deltagerRelation.organisationer.hovedtype",
    "Vrvirksomhed.deltagerRelation.organisationer.organisationsNavn.navn",
    "Vrvirksomhed.deltagerRelation.organisationer.medlemsData.attributter",
]


def main() -> int:
    flags = {a.split("=", 1)[0]: a.split("=", 1)[-1] for a in sys.argv[1:] if a.startswith("--")}
    pages = int(flags.get("--pages", 10))
    # Strata: CVR numbers are issued roughly chronologically, so sorting
    # ascending and taking the first N pages samples the OLDEST companies —
    # which is what the first calibration did, and it biased every denominator.
    # Starting `search_after` at spread points across the range stratifies by
    # incorporation era instead.
    strata = [int(x) for x in flags["--strata"].split(",")] if "--strata" in flags else [None]
    size = int(flags.get("--size", 1000))
    tag = flags.get("--tag", "bulk")

    store = RawStore(settings.raw_store_path)
    query = {
        "query": {"match": {"Vrvirksomhed.virksomhedMetadata.sammensatStatus": "NORMAL"}},
        "_source": FIELDS,
        "sort": [{"Vrvirksomhed.cvrNummer": "asc"}],
    }
    stored = 0
    calls = 0
    with CvrClient(settings.cvr_api_key, settings.cvr_base_url, timeout=180.0) as c:
        for s_i, after in enumerate(strata):
            q = dict(query)
            if after is not None:
                q["search_after"] = [after]
            for i, hits in enumerate(c.scan(COMPANY_INDEX, q, page_size=size)):
                rec = store.put(
                    payload=json.dumps(hits, ensure_ascii=False).encode(),
                    source="cvr", resource_type="bulk_page",
                    resource_id=f"{tag}:{s_i:02d}:{i:04d}", http_status=200,
                    request_params={"query": q, "stratum": after, "page": i,
                                    "size": size, "fields": FIELDS},
                )
                stored += len(hits)
                calls += 1
                first = str(hits[0]["_source"]["Vrvirksomhed"]["cvrNummer"])
                print(f"  stratum {after or 'start':>9}  page {i}  {len(hits):>5} "
                      f"from CVR {first}  {rec.byte_len/1e6:>5.1f} MB")
                if i + 1 >= pages:
                    break
    print(f"\n{stored:,} companies across {calls} request(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
