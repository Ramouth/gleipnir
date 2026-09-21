"""Ownership chain expansion and the path-product over all routes.

This is the graph layer from `architecture.md` §4 — a projection computed from
adjudicated claims, not a store. It is rebuilt from the raw store on demand and
nothing depends on it persisting.

**Why a path-product and not a tree walk.** `threat-model.md` §2.2's third
evasion is chain dilution: every individual link sits under 50%, but the product
along the chain still confers control, and multiple parallel routes sum. A
company has many shareholders, the same ultimate owner reaches a target by
several routes, and reciprocal holdings make cycles — so the structure is a
directed multigraph, and effective ownership is

    effective(o, t) = Σ over all acyclic paths o→t of Π(share on each edge)

Decimal throughout: these are decimal fractions filed to four places, and float
addition is not associative, which would make the same cap table produce
different totals depending on traversal order.

**Cost.** One CVR request per expanded node. That is the expensive half of a
screen, so expansion is bounded by an explicit budget and every stop reason is
recorded — a chain truncated by budget must never be reported as a chain that
terminated.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Callable, Iterable

from gleipnir.claims import Claim, EntityRef, Predicate, at

ZERO = Decimal("0")


class Stop(StrEnum):
    """Why a node was not expanded. Distinguishing these is the whole point:
    `UNRESOLVABLE` is a finding, `BUDGET` is a coverage limit, and reporting one
    as the other is the difference between evidence and a lie."""

    EXPANDED = "expanded"
    NATURAL_PERSON = "natural_person"      # chains end at people; not a gap
    UNRESOLVABLE = "unresolvable"          # no CVR number — the chain goes opaque
    NOT_FOUND = "not_found"                # CVR returned nothing
    DEPTH = "max_depth"                    # our cap, not the structure's
    BUDGET = "budget_exhausted"            # our cap, not the structure's
    CYCLE = "cycle"                        # reciprocal holding — itself a signal


@dataclass
class Node:
    ref: EntityRef
    depth: int
    stop: Stop
    claims: list[Claim] = field(default_factory=list)

    @property
    def is_terminal_by_our_limits(self) -> bool:
        return self.stop in (Stop.DEPTH, Stop.BUDGET)


@dataclass
class Edge:
    owner: str
    owned: str
    share: Decimal | None
    voting: Decimal | None
    owner_label: str | None = None


@dataclass
class Chain:
    root: str
    as_of: date
    nodes: dict[str, Node]
    edges: list[Edge]
    calls_spent: int

    def owners_of(self, key: str) -> list[Edge]:
        return [e for e in self.edges if e.owned == key]

    @property
    def unresolvable(self) -> list[Node]:
        return sorted((n for n in self.nodes.values() if n.stop is Stop.UNRESOLVABLE),
                      key=lambda n: n.ref.key)

    @property
    def truncated(self) -> list[Node]:
        return sorted((n for n in self.nodes.values() if n.is_terminal_by_our_limits),
                      key=lambda n: n.ref.key)

    @property
    def max_depth(self) -> int:
        return max((n.depth for n in self.nodes.values()), default=0)


def expand(
    root_cvr: str,
    *,
    as_of: date,
    load_claims: Callable[[str], list[Claim] | None],
    max_depth: int = 4,
    max_nodes: int = 25,
) -> Chain:
    """Walk ownership upward from `root_cvr`.

    `load_claims(cvr) -> claims | None` is injected so this module does no I/O
    and can be tested without a network or a quota.

    Breadth-first with a sorted frontier: the traversal order decides which
    nodes a budget cap drops, so it must not depend on dict ordering.
    """
    nodes: dict[str, Node] = {}
    edges: list[Edge] = []
    calls = 0

    root_claims = load_claims(root_cvr)
    if root_claims is None:
        nodes[root_cvr] = Node(EntityRef("company", root_cvr), 0, Stop.NOT_FOUND)
        return Chain(root_cvr, as_of, nodes, edges, calls)
    calls += 1
    nodes[root_cvr] = Node(EntityRef("company", root_cvr), 0, Stop.EXPANDED, root_claims)

    frontier: list[tuple[int, str]] = [(0, root_cvr)]
    while frontier:
        depth, key = frontier.pop(0)
        node = nodes[key]
        for owner_ref, share, voting in _owner_edges(node.claims, key, as_of):
            edges.append(Edge(owner=owner_ref.key, owned=key, share=share,
                              voting=voting, owner_label=owner_ref.label))
            if owner_ref.key in nodes:
                # Already known. If it is an ancestor of itself we have a cycle;
                # either way it is not re-expanded.
                if _reaches(edges, owner_ref.key, owner_ref.key):
                    nodes[owner_ref.key].stop = Stop.CYCLE
                continue
            if owner_ref.kind == "person":
                # Includes foreign natural persons, keyed `fp:`. A named human
                # is a chain terminus, never an opaque one.
                nodes[owner_ref.key] = Node(owner_ref, depth + 1, Stop.NATURAL_PERSON)
                continue
            if owner_ref.key.startswith(("enh:", "anden:", "fc:")):
                # Not a Danish registered company and not a natural person.
                # The chain cannot continue through it from this source, and
                # saying so IS the finding (poc.md §6.5) — as distinct from a
                # lookup that failed, or a node we chose not to spend on.
                nodes[owner_ref.key] = Node(owner_ref, depth + 1, Stop.UNRESOLVABLE)
                continue
            if depth + 1 > max_depth:
                nodes[owner_ref.key] = Node(owner_ref, depth + 1, Stop.DEPTH)
                continue
            if len(nodes) >= max_nodes:
                nodes[owner_ref.key] = Node(owner_ref, depth + 1, Stop.BUDGET)
                continue
            child = load_claims(owner_ref.key)
            calls += 1
            if child is None:
                nodes[owner_ref.key] = Node(owner_ref, depth + 1, Stop.NOT_FOUND)
                continue
            nodes[owner_ref.key] = Node(owner_ref, depth + 1, Stop.EXPANDED, child)
            frontier.append((depth + 1, owner_ref.key))
        frontier.sort()
    return Chain(root_cvr, as_of, nodes, edges, calls)


def _owner_edges(claims: list[Claim], owned_key: str, as_of: date):
    """Owner refs with their share and voting share, in a stable order."""
    votes: dict[str, Decimal] = {}
    for c in at(claims, Predicate.HAS_VOTING_RIGHTS, as_of):
        s = c.qualifiers.get("share")
        if s is not None:
            votes[c.subject.key] = max(votes.get(c.subject.key, ZERO), Decimal(str(s)))
    seen: dict[str, tuple[EntityRef, Decimal | None]] = {}
    for c in at(claims, Predicate.OWNS, as_of):
        s = c.qualifiers.get("share")
        share = Decimal(str(s)) if s is not None else None
        prev = seen.get(c.subject.key)
        if prev is None or (share is not None and (prev[1] is None or share > prev[1])):
            seen[c.subject.key] = (c.subject, share)
    for key in sorted(seen):
        ref, share = seen[key]
        yield ref, share, votes.get(key)


def _reaches(edges: list[Edge], start: str, target: str) -> bool:
    """Does `start` own `target` transitively? Used only for cycle detection."""
    stack, seen = [start], set()
    down = defaultdict(list)
    for e in edges:
        down[e.owner].append(e.owned)
    while stack:
        cur = stack.pop()
        for nxt in down.get(cur, ()):
            if nxt == target and cur != start:
                return True
            if nxt not in seen:
                seen.add(nxt)
                stack.append(nxt)
    return False


@dataclass(frozen=True)
class Effective:
    owner: str
    label: str | None
    share: Decimal
    routes: int
    incomplete: bool   # some route carried an unquantified edge
    #: True when nothing owns this owner in the expanded chain — a natural
    #: person, or a node we could not expand. Intermediates are effective owners
    #: too and belong in the >50% test, but a report that does not separate them
    #: reads as if a holding company and the person behind it were two owners.
    terminal: bool = False


def effective_ownership(chain: Chain, target: str | None = None) -> list[Effective]:
    """Path-product across every acyclic route, per ultimate owner.

    Returns owners sorted by descending effective share, then by key — a total
    order, so the reported figure never depends on traversal order.

    `incomplete` marks an owner whose routes include an edge filed without a
    percentage. Such a route contributes nothing to the sum, so the number is a
    **lower bound**, and it must be reported as one.
    """
    target = target or chain.root
    up: dict[str, list[Edge]] = defaultdict(list)
    for e in chain.edges:
        up[e.owned].append(e)

    totals: dict[str, Decimal] = defaultdict(lambda: ZERO)
    routes: dict[str, int] = defaultdict(int)
    partial: dict[str, bool] = defaultdict(bool)
    labels: dict[str, str | None] = {}

    def walk(node: str, factor: Decimal, path: frozenset[str], lossy: bool) -> None:
        parents = sorted(up.get(node, ()), key=lambda e: e.owner)
        if not parents:
            return
        for e in parents:
            if e.owner in path:
                continue                      # cycle: do not traverse twice
            labels.setdefault(e.owner, e.owner_label)
            if e.share is None:
                # Unquantified edge: the owner is real, the arithmetic is not.
                routes[e.owner] += 1
                partial[e.owner] = True
                walk(e.owner, ZERO, path | {e.owner}, True)
                continue
            contribution = factor * e.share
            totals[e.owner] += contribution
            routes[e.owner] += 1
            if lossy:
                partial[e.owner] = True
            walk(e.owner, contribution, path | {e.owner}, lossy)

    walk(target, Decimal("1"), frozenset({target}), False)
    out = [Effective(owner=k, label=labels.get(k), share=totals.get(k, ZERO),
                     routes=routes[k], incomplete=partial[k],
                     terminal=not up.get(k))
           for k in sorted(routes)]
    return sorted(out, key=lambda x: (-x.share, x.owner))
