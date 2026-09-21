"""Run the boolean scaffolding over companies and print facts.

    .venv/bin/python scripts/detect.py 99000147 [cvr...]

Output is a fact table: verdict, tier, predicate, raw quantity, evidence.
No narrative, no score, no adjectives.
"""
from __future__ import annotations

import sys
from datetime import date, datetime, timezone

from gleipnir.adapters.cvr import CvrClient
from gleipnir.adapters.opensanctions import SanctionsIndex
from gleipnir.config import settings
from gleipnir.extract.cvr import extract_company
from gleipnir.predicates.core import V
from gleipnir.predicates.cvr_only import ALL_CVR_ONLY, designated_holder
from gleipnir.rawstore import RawStore


def load_index(store: RawStore):
    """Return (index, blob_hash). The hash goes in the report header.

    Which designation corpus a screen ran against is part of the verdict — the
    artifact URL carries a build stamp and the list changes daily. A report that
    does not name the corpus cannot be reproduced.
    """
    rec = store.latest("opensanctions", "targets.simple.csv", "sanctions")
    if not rec:
        return None, None
    return SanctionsIndex.from_csv(store.path_of(rec.content_hash)), rec.content_hash


def fetch(store: RawStore, client: CvrClient | None, cvr: str) -> tuple[dict, str, str]:
    """Read-through cache: never spend quota on a company already stored."""
    rec = store.latest("cvr", "virksomhed", cvr)
    if rec:
        return store.get_json(rec.content_hash), rec.content_hash, "cached"
    if client is None:
        raise RuntimeError(f"{cvr} not cached and no CVR credential configured")
    resp = client.company(cvr)
    rec = store.put(payload=resp.body, source="cvr", resource_type="virksomhed",
                    resource_id=cvr, http_status=resp.http_status,
                    request_params=resp.query)
    return store.get_json(rec.content_hash), rec.content_hash, "fetched"


def run(cvr: str, store: RawStore, client, index, as_of: date):
    doc, h, how = fetch(store, client, cvr)
    claims = extract_company(doc, raw_ref=h, observed_at=str(as_of))
    if not claims:
        print(f"\n{cvr}  NOT FOUND ({how})")
        return []
    name = next((c.object for c in claims if c.predicate == "has_name"), "?")
    print(f"\n{cvr}  {name}   [{how}, {len(claims)} claims, blob {h[:12]}]")
    print(f"{'value':<11}{'tier':<5}{"predicate":<38}{'raw':<22}evidence")
    print("-" * 118)
    results = [p(claims, as_of) for p in ALL_CVR_ONLY]
    if index is not None:
        results.append(designated_holder(claims, index, as_of))
    ordered = sorted(results, key=lambda r: (
        {V.TRUE: 0, V.UNKNOWN: 1, V.FALSE: 2, V.UNKNOWABLE: 3}[r.value], r.predicate))
    for r in ordered:
        print(r.line())
    outcome_denominators([r for r in ordered if r.value is V.TRUE])
    return results


def outcome_denominators(fired: list) -> None:
    """What each fired predicate was measured to separate, and what it was not.

    A predicate that fired with no measured comparator prints that it has none.
    `README.md` states the rule: a fact with no denominator is not information,
    and implying rarity without one is the failure mode this whole layer exists
    to prevent.
    """
    from gleipnir.backtest import LABEL_PREDICATES
    from gleipnir.calibration import (OUTCOME_LEAD_SWEEP, OUTCOME_LR_365,
                                      OUTCOME_REJECTED)

    if not fired:
        return
    print("\nMEASURED AGAINST OUTCOMES — 1:1 matched on legal form, age band and")
    print("owner count, evaluated 365 days before the transition (calibration.py)")
    for r in fired:
        if r.predicate in LABEL_PREDICATES:
            print(f"  {r.predicate:<38}not rated — this predicate DEFINES the outcome "
                  f"cohorts, so a ratio for it would be circular")
            continue
        rows = OUTCOME_LR_365.get(r.predicate)
        if not rows:
            reason = OUTCOME_REJECTED.get(r.predicate)
            print(f"  {r.predicate:<38}no measured separation from either outcome cohort"
                  + (f" — {reason}" if reason else ""))
            continue
        for outcome in sorted(rows):
            a, n1, c, n2, ratio, lo, hi = rows[outcome]
            sweep = OUTCOME_LEAD_SWEEP.get(r.predicate, {}).get(outcome, {})
            trail = "  ".join(f"{d // 365}y {v:.2f}x" for d, v in sorted(sweep.items()))
            print(f"  {r.predicate:<38}{outcome:<20}{a}/{n1} {a / n1:5.1%} vs "
                  f"{c}/{n2} {c / n2:5.1%} control   {ratio:.2f}x [{lo:.2f}, {hi:.2f}]"
                  + (f"   lead {trail}" if trail else ""))


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    # Explicit as-of, UTC by default. `date.today()` is local-time, so two
    # analysts one second apart in different timezones got different verdicts —
    # and the window disagreed with the raw store's UTC fetch log by a day.
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = {a.split("=", 1)[0]: a.split("=", 1)[-1] for a in sys.argv[1:] if a.startswith("--")}
    as_of = (date.fromisoformat(flags["--as-of"]) if "--as-of" in flags
             else datetime.now(timezone.utc).date())
    store = RawStore(settings.raw_store_path)
    index, sanctions_hash = load_index(store)
    print(f"as-of {as_of}  ·  sanctions blob "
          f"{sanctions_hash[:12] if sanctions_hash else 'NONE'}")
    client = CvrClient(settings.cvr_api_key, settings.cvr_base_url) if settings.cvr_configured else None
    try:
        for cvr in args:
            run(cvr, store, client, index, as_of)
    finally:
        if client:
            client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
