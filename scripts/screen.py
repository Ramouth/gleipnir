"""Full screen: expand the ownership chain, run every predicate, print facts.

    .venv/bin/python scripts/screen.py 99000147 [--as-of=YYYY-MM-DD] [--budget=N]

Registry calls are read-through cached, so a re-run of the same company costs
nothing. The header states the as-of date and both pinned blob hashes, because a
verdict is only reproducible if the corpus it ran against is named.
"""
from __future__ import annotations

import sys
from datetime import date, datetime, timezone

from gleipnir.adapters.cvr import CvrClient
from gleipnir.adapters.opensanctions import SanctionsIndex
from gleipnir.chain import Stop, effective_ownership, expand
from gleipnir.claims import Predicate, latest
from gleipnir.config import settings
from gleipnir.extract.cvr import extract_company
from gleipnir.predicates.chain_preds import ALL_CHAIN, designated_in_chain
from gleipnir.predicates.core import V
from gleipnir.predicates.cvr_only import ALL_CVR_ONLY, designated_holder
from gleipnir.rawstore import RawStore

ORDER = {V.TRUE: 0, V.UNKNOWN: 1, V.FALSE: 2, V.UNKNOWABLE: 3}


def make_loader(store: RawStore, client, as_of, budget: list[int]):
    def load(cvr: str):
        rec = store.latest("cvr", "virksomhed", cvr)
        if not rec:
            if client is None or budget[0] <= 0:
                return None
            try:
                resp = client.company(cvr)
            except Exception:
                return None
            budget[0] -= 1
            rec = store.put(payload=resp.body, source="cvr",
                            resource_type="virksomhed", resource_id=cvr,
                            http_status=resp.http_status, request_params=resp.query)
        claims = extract_company(store.get_json(rec.content_hash),
                                 raw_ref=rec.content_hash, observed_at=str(as_of))
        return claims or None
    return load


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = {a.split("=", 1)[0]: a.split("=", 1)[-1] for a in sys.argv[1:] if a.startswith("--")}
    if not args:
        print(__doc__); return 2
    as_of = (date.fromisoformat(flags["--as-of"]) if "--as-of" in flags
             else datetime.now(timezone.utc).date())
    budget = [int(flags.get("--budget", 20))]

    store = RawStore(settings.raw_store_path)
    srec = store.latest("opensanctions", "targets.simple.csv", "sanctions")
    index = SanctionsIndex.from_csv(store.path_of(srec.content_hash)) if srec else None
    client = CvrClient(settings.cvr_api_key, settings.cvr_base_url) if settings.cvr_configured else None

    print(f"as-of {as_of}  ·  sanctions {srec.content_hash[:12] if srec else 'NONE'}"
          f"  ·  fetch budget {budget[0]}")
    try:
        for cvr in args:
            load = make_loader(store, client, as_of, budget)
            root = load(cvr)
            if not root:
                print(f"\n{cvr}  NOT FOUND"); continue
            name = latest(root, Predicate.HAS_NAME, as_of)
            chain = expand(cvr, as_of=as_of, load_claims=load,
                           max_depth=int(flags.get("--depth", 3)),
                           max_nodes=int(flags.get("--nodes", 12)))

            print(f"\n{'='*118}\n{cvr}  {name.object if name else '?'}")
            print(f"  chain: {len(chain.nodes)} node(s), depth {chain.max_depth}, "
                  f"{len(chain.edges)} edge(s), {chain.calls_spent} call(s)")
            for key in sorted(chain.nodes):
                n = chain.nodes[key]
                if n.depth:
                    print(f"    d{n.depth} {key:<14}{str(n.ref.label)[:40]:<42}{n.stop}")
            eff = effective_ownership(chain)
            if eff:
                print("  effective ownership of the root, all routes:")
                for e in eff[:8]:
                    flag = " (lower bound)" if e.incomplete else ""
                    kind = "ULTIMATE " if e.terminal else "via      "
                    print(f"    {kind}{e.share:.4f}  {str(e.label or e.owner)[:40]:<42}"
                          f"{e.routes} route(s){flag}")

            results = [p(root, as_of) for p in ALL_CVR_ONLY]
            results.append(designated_holder(root, index, as_of))
            results += [p(chain, as_of) for p in ALL_CHAIN]
            results.append(designated_in_chain(chain, index, as_of))
            print(f"\n  {'value':<11}{'tier':<5}{'predicate':<38}{'raw':<24}evidence")
            print("  " + "-" * 114)
            for r in sorted(results, key=lambda r: (ORDER[r.value], r.predicate)):
                print("  " + r.line())
    finally:
        if client:
            client.close()
    print(f"\nfetch budget remaining: {budget[0]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
