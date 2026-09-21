"""The screen: CVR number in, a chain of comparable facts and a short list of
findings out.

This is where every part meets. The order matters and it is the order the
design argues for:

    goals decide what to acquire   ->  plan.py / goals.py
    adapters fetch, raw store keeps ->  adapters/, rawstore.py
    pure functions make claims      ->  extract/
    projections make the graph      ->  chain.py, contradict.py
    predicates evaluate             ->  predicates/
    calibration supplies denominators-> calibration.py
    the analyst may suppress        ->  analyst.py
    the client concludes            ->  finding.py

**Almost nothing becomes a finding.** Predicate results become `ChainFact`s —
uncoloured knowledge carrying the measured rate in ordinary Danish companies, so
the reader can compare rather than be told. A `Finding` requires a ground from
`RedGround` plus an authority and a citation, which in practice means a
designation, an adjudicated fraud, litigation, or explicit public support.
Structure never colours anything.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Callable

from gleipnir import calibration as cal
from gleipnir.analyst import active_flag, apply_flag, current_hashes
from gleipnir.chain import Chain, effective_ownership, expand
from gleipnir.claims import Claim, Predicate, at, latest
from gleipnir.contradict import Status, pair_claims
from gleipnir.extract.cvr import extract_company
from gleipnir.finding import ChainFact, Colour, Comparator, Finding, RedGround, Screen
from gleipnir.narrative import Context, gaps as find_gaps
from gleipnir.predicates.chain_preds import ALL_CHAIN, designated_in_chain
from gleipnir.predicates.core import Result, V
from gleipnir.predicates.cvr_only import ALL_CVR_ONLY, designated_holder, nominee_density

#: Measured comparators, keyed by predicate. A fact without one prints
#: "no measured comparator" rather than implying rarity — an unrated fact is
#: still knowledge, but it is not a rated fact.
def _comparator(predicate: str, owners: int | None) -> Comparator:
    if predicate in cal.STRUCTURAL_ONLY and owners:
        rate = cal.stratum_rate(predicate, owners)
        if rate is not None:
            return Comparator(ordinary=rate,
                              ordinary_n=cal.STRATIFIED_RATES[min(max(owners, 1), 6)]["n"],
                              conditioned_on=f"{owners} owner(s)")
    rate = cal.BASE_RATES.get(predicate)
    if rate is None:
        return Comparator()
    return Comparator(ordinary=rate, ordinary_n=cal.SCAN_WITH_REGISTER)


#: Facts measured outside the CVR scan, with both arms where they exist.
EXTERNAL_COMPARATORS: dict[str, Comparator] = {
    "infrastructure_jurisdiction": Comparator(
        ordinary=0.0, ordinary_n=237, contrast=0.635, contrast_n=701,
        contrast_label="designated entities"),
    "filed_domain_does_not_resolve": Comparator(ordinary=0.044, ordinary_n=251),
    "no_mx_record": Comparator(ordinary=0.025, ordinary_n=237),
}

#: Predicates whose failure means the screen could not answer a question it
#: exists to answer. Anything else that comes back UNKNOWABLE is reported as a
#: coverage limit without colouring the screen — a company that never filed a
#: signing rule is not thereby unscreenable.
MATERIAL: frozenset[str] = frozenset({
    "designated_holder_candidate", "designated_in_chain",
    "effective_ownership_over_50", "chain_shape",
})

#: The only predicates permitted to raise a Finding, and the ground each carries.
FINDING_GROUNDS: dict[str, RedGround] = {
    "designated_holder_candidate": RedGround.DESIGNATION,
    "designated_in_chain": RedGround.DESIGNATION,
}


@dataclass
class Sources:
    """Everything the screen may read. Any of them may be None — a missing
    source becomes a stated limit, never a clean result."""

    store: Any
    cvr_client: Any = None
    sanctions: Any = None
    directorships: dict[str, int] | None = None
    budget: int = 0


def _load_claims(sources: Sources, as_of: date) -> Callable[[str], list[Claim] | None]:
    spent = {"n": 0}

    def load(cvr: str) -> list[Claim] | None:
        rec = sources.store.latest("cvr", "virksomhed", cvr)
        if not rec:
            if sources.cvr_client is None or spent["n"] >= sources.budget:
                return None
            try:
                resp = sources.cvr_client.company(cvr)
            except Exception:
                return None
            spent["n"] += 1
            rec = sources.store.put(
                payload=resp.body, source="cvr", resource_type="virksomhed",
                resource_id=cvr, http_status=resp.http_status,
                request_params=resp.query)
        return extract_company(sources.store.get_json(rec.content_hash),
                               raw_ref=rec.content_hash,
                               observed_at=str(as_of)) or None

    return load


def _owner_count(claims: list[Claim], as_of: date) -> int:
    return len({c.subject.key for c in at(claims, Predicate.OWNS, as_of)
                if c.qualifiers.get("share") is not None})


def _to_chain_fact(r: Result, owners: int | None, as_of: date,
                   raw_ref: str) -> ChainFact:
    return ChainFact(
        predicate=r.predicate,
        statement=r.evidence,
        value=r.raw,
        source="cvr",
        as_of=as_of,
        evidence_ref=raw_ref,
        comparator=EXTERNAL_COMPARATORS.get(r.predicate)
        or _comparator(r.predicate, owners),
    )


def _to_finding(r: Result, as_of: date, raw_ref: str) -> Finding:
    """A designation hit. Amber unless a verdict has adjudicated the match.

    Corporate nodes are matched by NAME, and iteration 8 showed a bare name
    lookup returning a different person of the same surname on the first try.
    So the honest colour is amber with `unadjudicated_name_match`; only a
    resolved identity verdict promotes it, and nothing here does that silently.
    """
    return Finding(
        colour=Colour.AMBER,
        ground=FINDING_GROUNDS[r.predicate],
        statement=r.evidence,
        qualifier="unadjudicated_name_match",
        evidence_ref=raw_ref,
        subject_hops=0,
    )


def run(cvr: str, sources: Sources, *, as_of: date, max_depth: int = 3,
        max_nodes: int = 12) -> Screen:
    """Screen one company. Reads the cache; spends quota only up to `budget`."""
    load = _load_claims(sources, as_of)
    root = load(cvr)
    if not root:
        gap = "company not in the raw store and no budget to fetch it"
        return Screen(cvr=cvr, name="?", as_of=as_of,
                      unknowable=[gap], blocked_on=[gap])

    rec = sources.store.latest("cvr", "virksomhed", cvr)
    name = latest(root, Predicate.HAS_NAME, as_of)
    owners = _owner_count(root, as_of)
    chain = expand(cvr, as_of=as_of, load_claims=load,
                   max_depth=max_depth, max_nodes=max_nodes)

    results: list[Result] = [p(root, as_of) for p in ALL_CVR_ONLY]
    results += [p(chain, as_of) for p in ALL_CHAIN]
    results.append(designated_holder(root, sources.sanctions, as_of))
    results.append(designated_in_chain(chain, sources.sanctions, as_of))
    if sources.directorships is not None:
        results.append(nominee_density(root, sources.directorships, as_of))

    screen = Screen(cvr=cvr, name=str(name.object) if name else "?", as_of=as_of)
    for r in results:
        if r.value is V.UNKNOWABLE:
            line = f"{r.predicate}: {r.evidence}"
            screen.unknowable.append(line)
            if r.predicate in MATERIAL:
                screen.blocked_on.append(line)
            continue
        if r.predicate in FINDING_GROUNDS and r.value is V.TRUE:
            screen.findings.append(_to_finding(r, as_of, rec.content_hash))
            continue
        if r.value in (V.TRUE, V.UNKNOWN):
            screen.chain.append(_to_chain_fact(r, owners, as_of, rec.content_hash))

    # Ultimate owners, as facts rather than as a verdict.
    for e in effective_ownership(chain)[:6]:
        if not e.terminal:
            continue
        screen.chain.append(ChainFact(
            predicate="effective_owner", value=f"{e.share:.4f}",
            statement=f"{e.label or e.owner} holds {e.share:.4f} effective across "
                      f"{e.routes} route(s)"
                      + (" (lower bound — an unquantified edge on some route)"
                         if e.incomplete else ""),
            source="cvr", as_of=as_of, evidence_ref=rec.content_hash))

    # Where the account requires something no connected source shows.
    ctx = Context(cvr=cvr, as_of=as_of, registry=root, chain=chain, asked={"cvr"})
    screen.gaps = find_gaps(ctx)

    flag, status = active_flag(sources.store, cvr,
                               current_hashes(sources.store, cvr))
    if status == "stale":
        screen.blocked_on.append(
            f"analyst green flag from {flag.as_of} is stale")
        screen.unknowable.append(
            f"analyst green flag from {flag.as_of} is STALE — the documents it "
            "was pinned to have changed, so it is reported and not applied")
    kept, dropped = apply_flag(results, flag, status)
    for r in dropped:
        screen.chain = [c for c in screen.chain if c.predicate != r.predicate]
        screen.chain.append(ChainFact(
            predicate=r.predicate, value=r.raw,
            statement=f"SUPPRESSED by analyst flag ({flag.analyst}, {flag.as_of}): "
                      f"{r.evidence}",
            source="analyst", as_of=as_of))
    return screen


def render(s: Screen, chain: Chain | None = None) -> str:
    out = [f"{s.cvr}  {s.name}",
           f"as-of {s.as_of}   ·   screen colour: {s.colour.upper()}", ""]
    if s.findings:
        out.append("FINDINGS")
        for f in s.findings:
            out.append("  " + f.line().replace("\n", "\n  "))
        out.append("")
    else:
        out.append("FINDINGS\n  none — no designation, adjudicated fraud, "
                   "counterparty litigation or public support on record\n")
    if s.gaps:
        out.append("GAPS — where the account requires something no source shows")
        for g in s.gaps:
            out.append("  " + g.line().replace("\n", "\n  "))
        out.append("")
    out.append("THE CHAIN — facts, with what they look like in populations we have measured")
    for c in sorted(s.chain, key=lambda c: c.predicate):
        out.append("  " + c.line().replace("\n", "\n  "))
    if s.unknowable:
        out.append("\nNOT DETERMINABLE FROM CONNECTED SOURCES")
        for u in s.unknowable:
            mark = "  ! " if u in s.blocked_on else "    "
            out.append(f"{mark}{u}")
        if s.blocked_on:
            out.append("  ! = stopped the screen answering a question it exists to answer")
    return "\n".join(out)
