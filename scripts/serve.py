"""The analyst workbench.

    .venv/bin/python scripts/serve.py
    .venv/bin/python scripts/serve.py --port=8080 --host=0.0.0.0

Reads the raw store and serves it at http://127.0.0.1:8000. Spends no registry
quota: the workbench holds a budget of zero, and no route raises it.

Binds to loopback by default. The store holds registry documents naming private
individuals in corporate capacity, and `docs/architecture.md` §11 governs where
those may go — putting this on 0.0.0.0 is a deliberate act, not a default.
"""
from __future__ import annotations

import sys

import uvicorn

from gleipnir.config import settings
from gleipnir.rawstore import RawStore
from gleipnir.web.app import create_app


def main() -> int:
    flags = {a.split("=", 1)[0]: a.split("=", 1)[-1]
             for a in sys.argv[1:] if a.startswith("--")}
    if "--help" in flags:
        print(__doc__)
        return 0
    host = flags.get("--host", "127.0.0.1")
    port = int(flags.get("--port", 8000))

    store = RawStore(settings.raw_store_path)
    app = create_app(store)

    # Warm the two slow indices here rather than inside the first request. The
    # directorship index is a nine-second scan of every officer in the store and
    # the designation list is a 2.5-second parse; paying for them at startup is
    # visible, and paying for them inside a page load looks like a broken page.
    bench = app.state.bench
    print(f"gleipnir workbench · raw store {settings.raw_store_path} · "
          f"registry budget 0")
    print("warming indices…", flush=True)
    print(f"  {len(bench.companies()):,} companies · "
          f"{len(bench.directorships or {}):,} directorships · "
          f"designation list {'loaded' if bench.sanctions else 'NOT LOADED'}",
          flush=True)
    # Flushed explicitly: uvicorn takes over stdout on the next line, and an
    # unflushed URL is one the reader never sees.
    print(f"\nhttp://{host}:{port}\n", flush=True)
    uvicorn.run(app, host=host, port=port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
