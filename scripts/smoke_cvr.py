"""One live CVR request, to prove credentials and show what a document holds.

Costs exactly one call against the monthly quota. Run:

    .venv/bin/python scripts/smoke_cvr.py 99000147
"""
from __future__ import annotations

import json
import sys

from gleipnir.adapters.cvr import CvrClient
from gleipnir.config import settings
from gleipnir.rawstore import RawStore


def main() -> int:
    cvr = sys.argv[1] if len(sys.argv) > 1 else "99000147"
    if not settings.cvr_configured:
        print("CVR_API_KEY is not set — copy it into .env first.", file=sys.stderr)
        return 2

    store = RawStore(settings.raw_store_path)
    with CvrClient(settings.cvr_api_key, settings.cvr_base_url) as client:
        resp = client.company(cvr)

    rec = store.put(
        payload=resp.body,
        source="cvr",
        resource_type="virksomhed",
        resource_id=cvr,
        http_status=resp.http_status,
        request_params=resp.query,
    )
    print(f"stored {rec.byte_len:,} bytes as {rec.content_hash[:16]}…")

    hits = resp.hits()
    if not hits:
        print("no hits — the CVR number may not exist")
        return 1

    doc = hits[0]["_source"].get("Vrvirksomhed", {})
    print(f"\ntop-level keys ({len(doc)}):")
    for k in sorted(doc):
        v = doc[k]
        shape = f"list[{len(v)}]" if isinstance(v, list) else type(v).__name__
        print(f"  {k:<40} {shape}")

    # The fields Gleipnir actually needs, and whether they are present.
    print("\nfields this design depends on:")
    for path in ("deltagerRelation", "attributter", "virksomhedsstatus",
                 "navne", "beliggenhedsadresse", "livsforloeb", "hovedbranche"):
        present = path in doc
        n = len(doc[path]) if present and isinstance(doc[path], list) else ""
        print(f"  {'ok ' if present else 'MISSING'} {path:<28} {n}")

    rels = doc.get("deltagerRelation") or []
    if rels:
        print(f"\nfirst deltagerRelation (of {len(rels)}):")
        print(json.dumps(rels[0], ensure_ascii=False, indent=2)[:1500])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
