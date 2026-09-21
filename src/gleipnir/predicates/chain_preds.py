"""Predicates over an expanded ownership chain.

These are the ones that could not fire on single-company data — `docs/loop-log.md`
iteration 1 recorded 0/44 for both, which was a structural silence, not evidence.

Cost: one CVR request per expanded node, so a screen using these is roughly an
order of magnitude more expensive than the `cvr_only` set.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from gleipnir.chain import Chain, Effective, Stop, effective_ownership
from gleipnir.predicates.core import Result, Tier, V, unknowable, unknown

HALF = Decimal("0.50")
QUARTER = Decimal("0.25")


def chain_shape(chain: Chain, as_of: date) -> Result:
    """Facts about the chain itself. No polarity claimed."""
    name = "chain_shape"
    n = len(chain.nodes)
    if n <= 1:
        return unknowable(name, Tier.OBSERVE, "no ownership edges to expand")
    return Result(name, V.TRUE, Tier.OBSERVE,
                  raw=f"{n} nodes, depth {chain.max_depth}",
                  evidence=f"{len(chain.edges)} edge(s), {chain.calls_spent} registry call(s)")


def chain_terminates_unresolvable(chain: Chain, as_of: date) -> Result:
    """A chain that stops at an entity we cannot resolve.

    `threat-model.md` §2.1 calls this the strongest structural signal there is,
    and `poc.md` §6.5 is explicit that not resolving it is itself the finding.

    Strictly separated from our own limits: a node dropped by the depth or node
    budget is NOT a chain that terminated, and conflating the two would report a
    coverage gap as evidence.
    """
    name = "chain_terminates_unresolvable"
    unres = chain.unresolvable
    trunc = chain.truncated
    if trunc and not unres:
        # UNKNOWN, not UNKNOWABLE. The distinction is the point of the fourth
        # value: this is "not yet checked", and the fix is to spend more calls.
        # UNKNOWABLE would assert the source cannot answer, which is false and
        # would send the gap to the coverage section instead of the agenda.
        return unknown(
            name, Tier.B,
            f"{len(trunc)} node(s) left unexpanded by OUR budget, not by the "
            f"structure — raise max_depth/max_nodes to answer this")
    if not unres:
        return Result(name, V.FALSE, Tier.B, raw=f"{len(chain.nodes)} node(s)",
                      evidence="every corporate node in the chain resolved to a CVR number")
    first = unres[0]
    note = "" if not trunc else f"; {len(trunc)} further node(s) unexpanded by budget"
    return Result(name, V.TRUE, Tier.B, raw=f"{len(unres)} unresolvable",
                  evidence=f"{first.ref.label or first.ref.key!r} at depth "
                           f"{first.depth} has no CVR number — ownership beyond it "
                           f"is not determinable from this source{note}",
                  detail={"nodes": [n.ref.label or n.ref.key for n in unres]})


def effective_control_over_50(chain: Chain, as_of: date) -> Result:
    """Path-product across all routes — the Tier A legal test.

    `threat-model.md` §2.2 chain dilution: each link under 50% while the product
    over all parallel routes still exceeds it.
    """
    name = "effective_ownership_over_50"
    eff = effective_ownership(chain)
    if not eff:
        return unknowable(name, Tier.A, "no ownership edges to compute over")
    top = eff[0]
    over = [e for e in eff if e.share > HALF]
    lower_bound = " (lower bound: unquantified edge on some route)" if top.incomplete else ""
    if not over:
        return Result(name, V.FALSE, Tier.A,
                      raw=f"max {top.share:.4f}",
                      evidence=f"largest effective holder {top.label or top.owner!r} "
                               f"at {top.share:.4f} across {top.routes} route(s)"
                               f"{lower_bound}")
    return Result(name, V.TRUE, Tier.A, raw=f"{top.share:.4f}",
                  evidence=f"{top.label or top.owner!r} holds {top.share:.4f} "
                           f"effective across {top.routes} route(s){lower_bound}",
                  detail={"over_threshold": [
                      {"owner": e.owner, "label": e.label, "share": str(e.share),
                       "routes": e.routes} for e in over]})


def dilution_masks_control(chain: Chain, as_of: date) -> Result:
    """Every direct link under 50%, but an indirect owner over it.

    This is the specific evasion, isolated: a single-company screen sees only
    minority holders and reports nothing.
    """
    name = "dilution_masks_control"
    direct = {e.owner: e.share for e in chain.owners_of(chain.root)
              if e.share is not None}
    if not direct:
        return unknowable(name, Tier.A, "no quantified direct holdings")
    if any(s > HALF for s in direct.values()):
        return Result(name, V.FALSE, Tier.A,
                      raw=f"max direct {max(direct.values()):.4f}",
                      evidence="a direct holder already exceeds 50% — control is "
                               "visible without traversing the chain")
    eff = [e for e in effective_ownership(chain) if e.share > HALF]
    if not eff:
        return Result(name, V.FALSE, Tier.A, raw=f"max direct {max(direct.values()):.4f}",
                      evidence="no direct holder over 50% and no indirect holder over "
                               "50% across all routes")
    top = eff[0]
    return Result(name, V.TRUE, Tier.A, raw=f"{top.share:.4f} via {top.routes} route(s)",
                  evidence=f"no direct holder exceeds 50% (max "
                           f"{max(direct.values()):.4f}) yet {top.label or top.owner!r} "
                           f"holds {top.share:.4f} effective")


def designated_in_chain(chain: Chain, index, as_of: date) -> Result:
    """Every node in the chain checked against the designation list.

    Tier A when it hits: designation nexus is what concealed hostile-state
    ownership *means*. Still a NAME match for corporate nodes, so the finding is
    a candidate for adjudication, not a conclusion.
    """
    name = "designated_in_chain"
    if index is None:
        return unknowable(name, Tier.A, "no designation list loaded")
    checked, hits = 0, []
    for key in sorted(chain.nodes):
        node = chain.nodes[key]
        label = node.ref.label
        if not label:
            continue
        checked += 1
        found = {t.id: t for t in index.by_name(label)}
        if found:
            hits.append((key, label, node.depth, [found[i] for i in sorted(found)]))
    if not checked:
        return unknowable(name, Tier.A, f"{len(chain.nodes)} node(s), none carrying a name")
    if not hits:
        return Result(name, V.FALSE, Tier.A, raw=f"{checked} node(s) checked",
                      evidence="0 name matches against the pinned designation list")
    key, label, depth, found = hits[0]
    return Result(name, V.TRUE, Tier.A, raw=f"{len(hits)} of {checked}",
                  evidence=f"{label!r} at depth {depth} matches {found[0].id} "
                           f"[{', '.join(sorted(found[0].designating_programmes)[:3])}] "
                           f"— NAME MATCH, NOT adjudicated",
                  detail={"hits": [{"key": k, "label": l, "depth": d} for k, l, d, _ in hits]})


def ultimate_owners_are_named_persons(chain: Chain, as_of: date) -> Result:
    """GREEN predicate — positive evidence, not absence of red.

    A chain resolving fully to named natural persons is the transparent case.
    `docs/predicates.md` §8 records that the research produced almost no green
    predicates because every lane searched for concealment; this is one that
    falls out of the same traversal.

    Strength is the number of red predicates that were evaluated and returned
    FALSE against it — a default that survived challenge, not one never
    challenged (`predicate-selection.md`). That count is applied by the report,
    not here.
    """
    name = "ultimate_owners_are_named_persons"
    # Terminal means "nothing in the chain owns it", not "we stopped there".
    # A company we DID expand and which turned out to have no registered owners
    # — a K/S, a pension fund, a foundation — is a genuine chain terminus. Keying
    # on the stop reason missed all of them and reported a 7-node chain as
    # having no terminal nodes at all.
    owned = {e.owned for e in chain.edges}
    leaves = [n for k, n in sorted(chain.nodes.items())
              if k not in owned and k != chain.root]
    if not leaves:
        return unknowable(name, Tier.OBSERVE, "chain has no terminal nodes")
    if any(n.is_terminal_by_our_limits for n in leaves):
        return unknown(name, Tier.OBSERVE,
                       "chain was truncated by our budget — raise it to answer this")
    persons = [n for n in leaves if n.ref.kind == "person"]
    if len(persons) == len(leaves):
        return Result(name, V.TRUE, Tier.OBSERVE, raw=f"{len(persons)} person(s)",
                      evidence=f"every route terminates at a named natural person "
                               f"within depth {chain.max_depth}")
    others = [n for n in leaves if n.ref.kind != "person"]
    return Result(name, V.FALSE, Tier.OBSERVE,
                  raw=f"{len(persons)}/{len(leaves)} person",
                  evidence=f"{len(others)} route(s) end at a non-person: "
                           + "; ".join(str(n.ref.label or n.ref.key)[:34] for n in others[:3]))


ALL_CHAIN = [chain_shape, chain_terminates_unresolvable, effective_control_over_50,
             dilution_masks_control, ultimate_owners_are_named_persons]
