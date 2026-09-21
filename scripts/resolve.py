"""Goal-driven multi-source screen. The knowledge structure decides what to fetch.

    .venv/bin/python scripts/resolve.py 99000147 --as-of=2026-08-27 --budget=15

Prints the agenda at each round, so the derivation is visible: which goal wanted
what, from which source, and why.
"""
from __future__ import annotations

import sys
from datetime import date, datetime, timezone

from gleipnir.adapters.cvr import CvrClient
from gleipnir.adapters.ee_ariregister import holders as ee_holders, lookup as ee_lookup
from gleipnir.adapters.gleif import GleifClient
from gleipnir.adapters.opensanctions import SanctionsIndex
from gleipnir.adapters.website import WebsiteClient, normalise_site
from gleipnir.chain import expand
from gleipnir.claims import Predicate, at, latest
from gleipnir.config import settings
from gleipnir.contradict import pair_claims
from gleipnir.extract.cvr import extract_company
from gleipnir.extract.ee import (
    extract_company as ee_company_claims, extract_holders as ee_holder_claims,
    unresolvable_holders)
from gleipnir.extract.website import extract_page
from gleipnir.goals import GOALS
from gleipnir.plan import Act, Need, State, agenda, run
from gleipnir.rawstore import RawStore


def build_executor(store, cvr_client, gleif, as_of):
    def load(cvr: str):
        rec = store.latest("cvr", "virksomhed", cvr)
        if not rec:
            if cvr_client is None:
                return None
            try:
                resp = cvr_client.company(cvr)
            except Exception:
                return None
            rec = store.put(payload=resp.body, source="cvr", resource_type="virksomhed",
                            resource_id=cvr, http_status=resp.http_status,
                            request_params=resp.query)
        return extract_company(store.get_json(rec.content_hash),
                               raw_ref=rec.content_hash, observed_at=str(as_of)) or None

    def execute(need: Need, state: State) -> bool:
        if need.key == "registry_claims":
            c = load(state.root)
            if not c:
                return False
            state.mark("registry_claims", state.root, c)
            state.spent += 1
            return True

        if need.key == "ownership_chain":
            ch = expand(state.root, as_of=as_of, load_claims=load,
                        max_depth=3, max_nodes=10)
            state.mark("ownership_chain", state.root, ch)
            state.spent += ch.calls_spent
            return True

        if need.key == "website":
            reg = state.have.get(f"registry_claims:{state.root}")
            site = latest(reg or [], Predicate.HAS_WEBSITE, as_of)
            if not site or not site.object:
                state.give_up("website", state.root, "no website filed in CVR")
                return True
            url = normalise_site(str(site.object))
            cached = store.latest("website", "page", url)
            if cached:
                body, h = store.get(cached.content_hash), cached.content_hash
            else:
                with WebsiteClient() as w:
                    if not w.may_fetch(url):
                        state.give_up("website", state.root, "robots.txt disallows")
                        return True
                    try:
                        resp = w.get(url)
                    except Exception:
                        return False
                r = store.put(payload=resp.body, source="website", resource_type="page",
                              resource_id=url, http_status=resp.http_status,
                              request_params={"final_url": resp.url})
                body, h = resp.body, r.content_hash
            subject = (state.have.get(f"registry_claims:{state.root}") or [None])[0].subject
            wc = extract_page(body, url=url, subject=subject, raw_ref=h,
                              observed_at=str(as_of))
            state.mark("website", state.root, url)
            state.mark("website_claims", state.root, wc)
            return True

        if need.key == "lei":
            recs = gleif.by_name(need.subject)
            state.mark("lei", need.subject, recs[0] if recs else None)
            if not recs:
                state.give_up("lei", need.subject, "no LEI under that legal name")
            return True

        if need.key == "lei_parent":
            rec = state.have.get(f"lei:{need.subject}")
            edge = gleif.parent(rec.lei) if rec else None
            state.mark("lei_parent", need.subject, edge)
            if edge is None:
                state.give_up("lei_parent", need.subject, "no Level 2 parent reported")
            return True

        if need.key == "sanctions_index":
            srec = store.latest("opensanctions", "targets.simple.csv", "sanctions")
            if not srec:
                state.give_up("sanctions_index", "global", "no designation list stored")
                return True
            idx = SanctionsIndex.from_csv(store.path_of(srec.content_hash))
            state.mark("sanctions_index", "global", idx)
            ch = state.have.get(f"ownership_chain:{state.root}")
            cands = []
            for k in sorted(ch.nodes):
                lbl = ch.nodes[k].ref.label
                if lbl and idx.by_name(lbl):
                    cands.append(lbl)
            state.mark("designation_candidates", state.root, cands)
            return True

        if need.key == "identity_verdict":
            # The oracle leaf. Not wired to a model yet — escalating is the
            # honest state, and it is a *bounded pairwise question*, which is
            # what §7.1 permits.
            state.give_up("identity_verdict", need.subject,
                          "requires an adjudicated pairwise verdict — not yet wired")
            return True

        if need.key == "foreign_entity":
            # A person who runs this company is recorded at an entity abroad.
            # Resolve it in that jurisdiction's own register.
            if need.source != "ee_ariregister":
                state.give_up(need.key, need.subject,
                              f"no adapter for {need.source} yet")
                return True
            found, blob = ee_lookup(store, names=[need.subject])
            if not found:
                state.give_up(need.key, need.subject,
                              "not found in the Estonian register under that name "
                              "— a name lookup, so absence is not proof of absence")
                return True
            owners, oblob = ee_holders(store, found)
            claims = []
            for code, company in found.items():
                claims += ee_company_claims(company, raw_ref=blob,
                                            observed_at=str(as_of))
                hs = owners.get(code) or []
                claims += ee_holder_claims(company, hs, raw_ref=oblob,
                                           observed_at=str(as_of))
                if unresolvable_holders(hs):
                    state.mark("ee_unresolvable", code, unresolvable_holders(hs))
            state.mark(need.key, need.subject, claims)
            return True

        if need.key == "accounts":
            state.give_up("accounts", state.root, "regnskaber adapter not yet built")
            return True
        return False

    return execute


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = {a.split("=", 1)[0]: a.split("=", 1)[-1] for a in sys.argv[1:] if a.startswith("--")}
    as_of = (date.fromisoformat(flags["--as-of"]) if "--as-of" in flags
             else datetime.now(timezone.utc).date())
    budget = int(flags.get("--budget", 15))
    store = RawStore(settings.raw_store_path)
    cvr_client = CvrClient(settings.cvr_api_key, settings.cvr_base_url) if settings.cvr_configured else None

    with GleifClient() as gleif:
        for cvr in args:
            state = State(root=cvr, as_of=as_of)
            execute = build_executor(store, cvr_client, gleif, as_of)
            print(f"\n{'='*100}\n{cvr}   as-of {as_of}   budget {budget}\n")
            round_no = 0
            while True:
                todo = agenda(GOALS, state)
                if not todo:
                    print("  agenda empty — every goal decidable or unknowable")
                    break
                round_no += 1
                print(f"  round {round_no}:")
                acted = False
                for need in todo:
                    if state.spent + need.cost > budget:
                        print(f"    SKIP  {need.key:<20}{need.source:<12}(budget)")
                        continue
                    print(f"    {need.act:<8}{need.key:<20}{need.source:<12}"
                          f"cost {need.cost}  <- {need.because[:62]}")
                    execute(need, state)
                    acted = True
                if not acted or round_no > 6:
                    break

            print(f"\n  spent {state.spent} registry call(s)")
            reg = state.have.get(f"registry_claims:{cvr}") or []
            nm = latest(reg, Predicate.HAS_NAME, as_of)
            print(f"  {nm.object if nm else '?'}")
            ch = state.have.get(f"ownership_chain:{cvr}")
            if ch:
                print(f"  chain: {len(ch.nodes)} nodes, depth {ch.max_depth}, "
                      f"{len(ch.unresolvable)} unresolvable")
                for n in ch.unresolvable:
                    lei = state.have.get(f"lei:{n.ref.label}")
                    par = state.have.get(f"lei_parent:{n.ref.label}")
                    tag = "no LEI" if lei is None else f"LEI {lei.lei} ({lei.country})"
                    if par:
                        tag += f" -> parent {par.parent_name} ({par.parent_country})"
                    print(f"    UNRESOLVED  {str(n.ref.label)[:40]:<42}{tag}")
            wc = state.have.get(f"website_claims:{cvr}")
            if wc:
                pairs = pair_claims(wc, reg, as_of=as_of)
                print(f"  website: {len(wc)} checkable statement(s), {len(pairs)} paired")
                for p in pairs:
                    print("    " + p.line())
            if state.unknowable:
                print("  UNKNOWABLE:")
                for k in sorted(state.unknowable):
                    print(f"    {k}")
    if cvr_client:
        cvr_client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
