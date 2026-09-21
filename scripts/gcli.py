"""gleipnir screen <cvr> [<cvr>...]

    .venv/bin/python scripts/gcli.py screen 99000147 --as-of=2026-08-27
    .venv/bin/python scripts/gcli.py screen 99000457 --budget=8

Reads the raw store. Spends registry quota only up to --budget (default 0).
"""
from __future__ import annotations

import sys
from datetime import date, datetime, timezone

from gleipnir.adapters.cvr import CvrClient
from gleipnir.adapters.opensanctions import SanctionsIndex
from gleipnir.calibration import build_directorship_index
from gleipnir.config import settings
from gleipnir.rawstore import RawStore
from gleipnir.screen import Sources, render, run


def main() -> int:
    argv = sys.argv[1:]
    if not argv or argv[0] != "screen":
        print(__doc__); return 2
    args = [a for a in argv[1:] if not a.startswith("--")]
    flags = {a.split("=", 1)[0]: a.split("=", 1)[-1] for a in argv[1:] if a.startswith("--")}
    if not args:
        print(__doc__); return 2

    as_of = (date.fromisoformat(flags["--as-of"]) if "--as-of" in flags
             else datetime.now(timezone.utc).date())
    budget = int(flags.get("--budget", 0))

    store = RawStore(settings.raw_store_path)
    srec = store.latest("opensanctions", "targets.simple.csv", "sanctions")
    sanctions = SanctionsIndex.from_csv(store.path_of(srec.content_hash)) if srec else None
    dirs = build_directorship_index(store) if "--no-nominee" not in flags else None
    client = (CvrClient(settings.cvr_api_key, settings.cvr_base_url)
              if budget and settings.cvr_configured else None)

    print(f"as-of {as_of}  ·  sanctions {srec.content_hash[:12] if srec else 'NONE'}"
          f"  ·  directorship index {len(dirs or {}):,}"
          f"  ·  registry budget {budget}\n")
    try:
        for cvr in args:
            s = run(cvr, Sources(store=store, cvr_client=client, sanctions=sanctions,
                                 directorships=dirs, budget=budget), as_of=as_of)
            print("=" * 100)
            print(render(s))
            print()
    finally:
        if client:
            client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
