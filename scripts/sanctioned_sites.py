"""Positive control: where do DESIGNATED entities host their infrastructure?

The jurisdiction detector returned 0 of 251 on ordinary Danish companies. That
bounds the base rate but never shows the signal firing, so the rate is untested
in the only direction that matters. This builds the other arm.

    .venv/bin/python scripts/sanctioned_sites.py
"""
from __future__ import annotations

import json
import sys

import httpx

from gleipnir.adapters.dns import domain_of
from gleipnir.adapters.opensanctions import resolve_resource_url
from gleipnir.config import settings
from gleipnir.rawstore import RawStore

INDEX = "https://data.opensanctions.org/datasets/latest/sanctions/index.json"


def artifact_url() -> tuple[str, str]:
    idx = httpx.get(INDEX, timeout=60, follow_redirects=True).raise_for_status().json()
    url = next(r["url"] for r in idx["resources"] if r["name"] == "entities.ftm.json")
    return url, idx.get("last_change", "")


def main() -> int:
    store = RawStore(settings.raw_store_path)
    url, version = artifact_url()
    print(f"artifact {url.rsplit('/', 2)[-2]}  version {version}", flush=True)

    rows, seen, n = [], set(), 0
    with httpx.Client(timeout=600.0, follow_redirects=True) as c:
        with c.stream("GET", url) as r:
            r.raise_for_status()
            buf = b""
            for chunk in r.iter_bytes(1 << 20):
                buf += chunk
                *lines, buf = buf.split(b"\n")
                for line in lines:
                    if not line.strip():
                        continue
                    n += 1
                    try:
                        e = json.loads(line)
                    except Exception:
                        continue
                    p = e.get("properties", {})
                    sites = p.get("website") or []
                    if not sites or e.get("schema") not in ("Company", "Organization", "LegalEntity"):
                        continue
                    for s in sites:
                        d = domain_of(s)
                        if d and d not in seen:
                            seen.add(d)
                            rows.append({
                                "domain": d, "id": e.get("id"),
                                "schema": e.get("schema"),
                                "name": (p.get("name") or ["?"])[0][:70],
                                "countries": p.get("country") or [],
                                "topics": p.get("topics") or [],
                                "programs": (p.get("program") or [])[:4],
                            })
    print(f"{n:,} entities streamed · {len(rows):,} designated entities with a domain")

    rec = store.put(
        payload=json.dumps(rows, ensure_ascii=False, sort_keys=True).encode(),
        source="opensanctions", resource_type="designated_domains",
        resource_id="sanctions", http_status=200,
        request_params={"url": url, "version": version, "entities_scanned": n},
    )
    print(f"stored {rec.byte_len/1e6:.1f} MB as {rec.content_hash[:12]}")
    from collections import Counter
    print("country tags:", Counter(c for r in rows for c in r["countries"]).most_common(12))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
