"""Record an analyst judgement about an entity.

    .venv/bin/python scripts/observe.py 99000236 green \
        --analyst="..." --basis="..." --suppress=majority_owner_unresolvable \
        --source=https://... --as-of=2026-08-27
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone

from gleipnir.analyst import (
    Observation, active_flag, current_hashes, observations, record)
from gleipnir.config import settings
from gleipnir.rawstore import RawStore


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags: dict[str, list[str]] = {}
    for a in sys.argv[1:]:
        if a.startswith("--"):
            k, _, v = a.partition("=")
            flags.setdefault(k, []).append(v)
    if len(args) < 2:
        print(__doc__); return 2
    subject, verdict = args[0], args[1]
    store = RawStore(settings.raw_store_path)

    # Pin the exact documents the judgement is being made against — the ones
    # in force now, not every payload ever fetched about the entity.
    pinned = tuple(sorted(current_hashes(store, subject)))
    obs = Observation(
        subject=subject, verdict=verdict,
        basis=flags.get("--basis", [""])[0],
        analyst=flags.get("--analyst", ["unknown"])[0],
        observed_at=datetime.now(timezone.utc).isoformat(),
        as_of=flags.get("--as-of", [str(datetime.now(timezone.utc).date())])[0],
        sources=tuple(flags.get("--source", [])),
        pinned=pinned,
        suppresses=tuple(flags.get("--suppress", [])),
    )
    h = record(store, obs)
    print(f"recorded {h[:12]}  {subject} -> {verdict}")
    print(f"  pinned {len(pinned)} document(s); suppresses {list(obs.suppresses) or 'nothing'}")
    _, status = active_flag(store, subject, current_hashes(store, subject))
    print(f"  status: {status}")
    print(f"  history: {len(observations(store, subject))} observation(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
