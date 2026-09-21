"""Probe every registered source and print what is actually reachable.

Run after changing credentials, and before believing any coverage claim:

    .venv/bin/python scripts/check_sources.py
"""
from __future__ import annotations

import concurrent.futures as cf

import httpx

from gleipnir.config import settings
from gleipnir.sources import REGISTRY, AuthMode, SourceSpec

_AUTH_HEADERS = {
    "cvr": lambda: {"Authorization": f"Basic {settings.cvr_api_key}"}
    if settings.cvr_api_key else {},
}


def probe(spec: SourceSpec) -> tuple[SourceSpec, str, str]:
    if not spec.health_path:
        return spec, "-", "no health check defined"
    headers = {"Content-Type": "application/json"}
    headers.update(_AUTH_HEADERS.get(spec.id, dict)())
    url = spec.base_url.rstrip("/") + spec.health_path
    try:
        with httpx.Client(timeout=20.0, follow_redirects=True) as c:
            if spec.health_method == "POST":
                r = c.post(url, headers=headers, json={"size": 0})
            else:
                r = c.get(url, headers=headers)
    except httpx.HTTPError as e:
        return spec, "ERR", type(e).__name__
    ok = r.status_code == 200
    if ok:
        return spec, "OK", f"{len(r.content):,}b"
    # A 401/403 on a source we have not registered for is expected, not broken.
    expected = r.status_code in (401, 403) and spec.auth is not AuthMode.NONE
    return spec, "AUTH" if expected else "FAIL", f"HTTP {r.status_code}"


def main() -> int:
    with cf.ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(probe, REGISTRY.values()))
    results.sort(key=lambda r: ({"OK": 0, "AUTH": 1, "FAIL": 2, "ERR": 3}.get(r[1], 4), r[0].id))

    print(f"{'status':<7}{'source':<26}{'auth':<11}{'licence':<16}detail")
    print("-" * 84)
    for spec, status, detail in results:
        print(f"{status:<7}{spec.id:<26}{spec.auth:<11}{spec.licence:<16}{detail}")

    need = [s for s, st, _ in results if st == "AUTH"]
    if need:
        print("\nNeeds registration before use:")
        for s in need:
            print(f"  {s.id:<22} {s.base_url}")
            print(f"  {'':<22} {s.notes}")
    broken = [s.id for s, st, _ in results if st in ("FAIL", "ERR")]
    return 1 if broken else 0


if __name__ == "__main__":
    raise SystemExit(main())
