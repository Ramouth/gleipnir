"""Fetch an outcome-labelled cohort and a control cohort, with ONE field list.

    .venv/bin/python scripts/cohort_scan.py --strata=8 --size=500

`docs/loop-log.md` iteration 1 tested 8 hand-picked failures against 40 controls
and the entry records why the result was worthless: the cohorts were selected on
the outcome and the controls were unmatched. Two things fix that, and both are
mechanical.

**One field projection for every cohort.** The 41,000-company corpus already held
omits `Vrvirksomhed.attributter`, so `audit_waived` and `signing_rule_changed`
return `UNKNOWABLE` on it. Measuring cases fetched WITH that field against
controls fetched without it would report a missing field as a clean control —
the exact failure the four-valued logic exists to prevent. So controls are
re-fetched here under the same projection rather than reused.

**The event date comes from the register.** `virksomhedsstatus` is a dated
history: NORMAL until 2016-10-04, UNDER TVANGSOPLØSNING from 2016-10-05. That is
what lets a predicate be evaluated a year BEFORE the outcome instead of on a
document that already contains it.

Participant names are still excluded, as in `bulk_scan.py` — nothing measured
here reads them, and collecting thousands of people's names to compute a rate
would be gratuitous under `architecture.md` §11.
"""
from __future__ import annotations

import json
import sys

from gleipnir.adapters.cvr import COMPANY_INDEX, CvrClient
from gleipnir.config import settings
from gleipnir.rawstore import RawStore

#: Identical for every cohort. Anything added here must be re-fetched for ALL
#: cohorts or it becomes a coverage difference masquerading as a rate difference.
FIELDS = [
    "Vrvirksomhed.cvrNummer",
    "Vrvirksomhed.virksomhedMetadata.nyesteNavn.navn",
    "Vrvirksomhed.virksomhedMetadata.sammensatStatus",
    "Vrvirksomhed.virksomhedMetadata.stiftelsesDato",
    "Vrvirksomhed.virksomhedMetadata.nyesteVirksomhedsform.langBeskrivelse",
    "Vrvirksomhed.virksomhedMetadata.nyesteAarsbeskaeftigelse.antalAnsatte",
    "Vrvirksomhed.virksomhedsstatus",
    "Vrvirksomhed.livsforloeb",
    "Vrvirksomhed.attributter",
    "Vrvirksomhed.deltagerRelation.deltager.enhedsNummer",
    "Vrvirksomhed.deltagerRelation.deltager.enhedstype",
    "Vrvirksomhed.deltagerRelation.deltager.forretningsnoegle",
    "Vrvirksomhed.deltagerRelation.organisationer.hovedtype",
    "Vrvirksomhed.deltagerRelation.organisationer.organisationsNavn.navn",
    "Vrvirksomhed.deltagerRelation.organisationer.medlemsData.attributter",
]

#: label -> the `sammensatStatus` value that defines it.
#:
#: Two labels, kept apart deliberately. Forced dissolution is the registrar
#: striking a company off for not filing accounts or not having lawful
#: management or a valid address — an administrative red flag about opacity.
#: Bankruptcy is an economic outcome. A predicate that fires on the second and
#: not the first is measuring financial distress, which is not what this system
#: claims to detect, and separating them is the only way to see that.
COHORTS: dict[str, str] = {
    "forced-dissolution": "TVANGSOPLØST",
    "bankruptcy": "UNDERKONKURS",
    "control": "NORMAL",
}

#: Spread `search_after` starting points. CVR numbers are issued roughly
#: chronologically, so ascending-from-zero samples the oldest companies only —
#: iteration 4 biased every denominator that way. The SAME points are used for
#: every cohort so the incorporation-era mix is comparable by construction.
STRATA = [10_000_000, 15_000_000, 20_000_000, 25_000_000,
          28_000_000, 32_000_000, 36_000_000, 40_000_000]


def main() -> int:
    flags = {a.split("=", 1)[0]: a.split("=", 1)[-1] for a in sys.argv[1:] if a.startswith("--")}
    n_strata = int(flags.get("--strata", len(STRATA)))
    size = int(flags.get("--size", 500))
    tag = flags.get("--tag", "outcome")
    only = flags.get("--cohort")

    store = RawStore(settings.raw_store_path)
    calls = 0
    with CvrClient(settings.cvr_api_key, settings.cvr_base_url, timeout=180.0) as c:
        for label, status in COHORTS.items():
            if only and label != only:
                continue
            total = 0
            for s_i, after in enumerate(STRATA[:n_strata]):
                q = {
                    "query": {"match": {
                        "Vrvirksomhed.virksomhedMetadata.sammensatStatus": status}},
                    "_source": FIELDS,
                    "sort": [{"Vrvirksomhed.cvrNummer": "asc"}],
                    "size": size,
                    "search_after": [after],
                }
                hits = c.search(COMPANY_INDEX, q).hits()
                calls += 1
                if not hits:
                    print(f"  {label:<20} stratum {after}  EMPTY")
                    continue
                rec = store.put(
                    payload=json.dumps(hits, ensure_ascii=False).encode(),
                    source="cvr", resource_type="bulk_page",
                    resource_id=f"{tag}:{label}:{s_i:02d}", http_status=200,
                    request_params={"query": q, "cohort": label, "status": status,
                                    "stratum": after, "size": size, "fields": FIELDS},
                )
                total += len(hits)
                first = hits[0]["_source"]["Vrvirksomhed"]["cvrNummer"]
                print(f"  {label:<20} stratum {after:>9}  {len(hits):>4} hits  "
                      f"from CVR {first}  {rec.byte_len/1e6:>5.1f} MB")
            print(f"  {label:<20} {total:,} companies\n")
    print(f"{calls} API request(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
