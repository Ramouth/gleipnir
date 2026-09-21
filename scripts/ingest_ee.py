"""Ingest Estonia's open company register into the raw store.

    .venv/bin/python scripts/ingest_ee.py                 # identity + ownership
    .venv/bin/python scripts/ingest_ee.py --all           # adds beneficial owners
    .venv/bin/python scripts/ingest_ee.py --list

Free, no credential, no rate limit. Roughly 52 MB for the two datasets a chain
needs, published daily. Streamed to disk under their content hashes, so
re-running when nothing has changed costs one log line and no new blob.

Why bulk rather than lookup: there is no free per-company endpoint. The whole
dataset is the unit, and `adapters/ee_ariregister.py` reads it with a targeted
streaming pass rather than holding it in memory.
"""
from __future__ import annotations

import sys

from gleipnir.adapters.ee_ariregister import DATASETS, SOURCE_ID, EeClient, EeError
from gleipnir.config import settings
from gleipnir.rawstore import RawStore

#: What a chain actually needs. `kasusaajad` is behind `--all` because its
#: identifiers were stripped in 2025 and it cannot identify anybody.
DEFAULT = ("lihtandmed", "osanikud")


def main() -> int:
    flags = {a for a in sys.argv[1:] if a.startswith("--")}
    if "--help" in flags:
        print(__doc__)
        return 0
    if "--list" in flags:
        for name, fmt in DATASETS.items():
            print(f"  {name:<14}{fmt}")
        return 0

    wanted = tuple(DATASETS) if "--all" in flags else DEFAULT
    store = RawStore(settings.raw_store_path)
    status = 0
    with EeClient() as client:
        for dataset in wanted:
            print(f"{dataset} … ", end="", flush=True)
            try:
                record = store.put_stream(
                    client.stream(dataset), source=SOURCE_ID,
                    resource_type=dataset, resource_id=dataset,
                    http_status=200, request_params={"dataset": dataset})
            except EeError as exc:
                print(f"FAILED — {exc}")
                status = 1
                continue
            print(f"{record.byte_len:,} bytes  {record.content_hash[:12]}")
    print("\nStored. Lookups stream from these files; nothing else is resident.")
    return status


if __name__ == "__main__":
    raise SystemExit(main())
