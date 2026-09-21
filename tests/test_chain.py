"""Chain expansion and the path-product.

The dilution case is the one that matters: every link under 50%, the product
over all routes above it. A single-company screen sees only minority holders.
"""
from datetime import date
from decimal import Decimal

from gleipnir.chain import Stop, effective_ownership, expand
from gleipnir.claims import Claim, EntityRef, EpistemicTier, Predicate
from gleipnir.predicates.chain_preds import (
    chain_terminates_unresolvable, dilution_masks_control, effective_control_over_50,
    ultimate_owners_are_named_persons,
)
from gleipnir.predicates.core import V

AS_OF = date(2026, 8, 27)


def owns(owner, owned, share, kind="company", label=None):
    return Claim(subject=EntityRef(kind, owner, label or owner),
                 predicate=Predicate.OWNS,
                 object=EntityRef("company", owned), source_id="cvr",
                 epistemic_tier=EpistemicTier.SELF_DECLARED_TO_REGISTRY,
                 raw_ref="r", valid_from=date(2020, 1, 1),
                 qualifiers={"share": Decimal(share) if share else None,
                             "register": "EJERREGISTER"})


def loader(book):
    return lambda cvr: book.get(cvr)


def test_dilution_across_parallel_routes():
    """threat-model.md §2.2: D owns 80% of H and 70% of U; H holds 50% of T and
    U 40%. No direct holder of T exceeds 50%, but 0.8*0.5 + 0.7*0.4 = 0.68."""
    book = {
        "99000147": [owns("99000163", "99000147", "0.50", label="H"),
                     owns("99000287", "99000147", "0.40", label="U")],
        "99000163": [owns("p1", "99000163", "0.80", kind="person", label="D")],
        "99000287": [owns("p1", "99000287", "0.70", kind="person", label="D")],
    }
    chain = expand("99000147", as_of=AS_OF, load_claims=loader(book))
    eff = {e.owner: e.share for e in effective_ownership(chain)}
    assert eff["p1"] == Decimal("0.68")
    assert effective_control_over_50(chain, AS_OF).value is V.TRUE
    assert dilution_masks_control(chain, AS_OF).value is V.TRUE


def test_no_dilution_when_a_direct_holder_already_controls():
    book = {"99000147": [owns("99000163", "99000147", "0.90", label="H")],
            "99000163": [owns("p1", "99000163", "1.00", kind="person", label="D")]}
    chain = expand("99000147", as_of=AS_OF, load_claims=loader(book))
    assert dilution_masks_control(chain, AS_OF).value is V.FALSE
    assert effective_control_over_50(chain, AS_OF).value is V.TRUE


def test_path_product_is_order_independent():
    book = {
        "99000147": [owns("99000163", "99000147", "0.33"),
                     owns("99000287", "99000147", "0.33"),
                     owns("99000317", "99000147", "0.34")],
        "99000163": [owns("p1", "99000163", "1.00", kind="person")],
        "99000287": [owns("p1", "99000287", "1.00", kind="person")],
        "99000317": [owns("p1", "99000317", "1.00", kind="person")],
    }
    a = effective_ownership(expand("99000147", as_of=AS_OF, load_claims=loader(book)))
    rev = {k: list(reversed(v)) for k, v in book.items()}
    b = effective_ownership(expand("99000147", as_of=AS_OF, load_claims=loader(rev)))
    assert [(e.owner, e.share) for e in a] == [(e.owner, e.share) for e in b]
    assert next(e.share for e in a if e.owner == "p1") == Decimal("1.00")


def test_cycle_terminates_and_is_marked():
    """Reciprocal ownership is a real concealment technique, not a data error.
    A naive walk does not terminate."""
    book = {"99000147": [owns("99000163", "99000147", "0.60")],
            "99000163": [owns("99000147", "99000163", "0.60")]}
    chain = expand("99000147", as_of=AS_OF, load_claims=loader(book))
    assert any(n.stop is Stop.CYCLE for n in chain.nodes.values())


def test_unresolvable_node_is_the_finding():
    """poc.md §6.5 — reaching a foreign entity and stopping is itself the
    result, and must not read as a blank."""
    book = {"99000147": [owns("enh:999", "99000147", "0.70", label="Cyprus Holdco")]}
    chain = expand("99000147", as_of=AS_OF, load_claims=loader(book))
    r = chain_terminates_unresolvable(chain, AS_OF)
    assert r.value is V.TRUE
    assert "Cyprus Holdco" in r.evidence


def test_our_budget_limit_is_not_reported_as_termination():
    """A node we chose not to expand is a coverage gap, never evidence that the
    chain ended there."""
    book = {"99000147": [owns("99000163", "99000147", "0.60")],
            "99000163": [owns("99000287", "99000163", "0.60")],
            "99000287": [owns("p1", "99000287", "1.00", kind="person")]}
    chain = expand("99000147", as_of=AS_OF, load_claims=loader(book), max_depth=1)
    r = chain_terminates_unresolvable(chain, AS_OF)
    assert r.value is V.UNKNOWN
    assert "OUR budget" in r.evidence


def test_unquantified_edge_makes_the_number_a_lower_bound():
    book = {"99000147": [owns("99000163", "99000147", None)],
            "99000163": [owns("p1", "99000163", "1.00", kind="person")]}
    chain = expand("99000147", as_of=AS_OF, load_claims=loader(book))
    eff = effective_ownership(chain)
    assert all(e.incomplete for e in eff)
    assert "lower bound" in effective_control_over_50(chain, AS_OF).evidence


def test_green_predicate_fires_only_on_a_fully_resolved_chain():
    book = {"99000147": [owns("99000163", "99000147", "0.60")],
            "99000163": [owns("p1", "99000163", "1.00", kind="person", label="A Person")]}
    chain = expand("99000147", as_of=AS_OF, load_claims=loader(book))
    assert ultimate_owners_are_named_persons(chain, AS_OF).value is V.TRUE


def test_green_predicate_refuses_to_claim_on_a_truncated_chain():
    book = {"99000147": [owns("99000163", "99000147", "0.60")],
            "99000163": [owns("99000287", "99000163", "0.60")],
            "99000287": [owns("p1", "99000287", "1.00", kind="person")]}
    chain = expand("99000147", as_of=AS_OF, load_claims=loader(book), max_depth=1)
    # UNKNOWN, not UNKNOWABLE: raising the budget would answer it.
    assert ultimate_owners_are_named_persons(chain, AS_OF).value is V.UNKNOWN


def test_expansion_counts_registry_calls():
    book = {"99000147": [owns("99000163", "99000147", "0.60")],
            "99000163": [owns("p1", "99000163", "1.00", kind="person")]}
    chain = expand("99000147", as_of=AS_OF, load_claims=loader(book))
    assert chain.calls_spent == 2


def test_intermediates_and_ultimate_owners_are_distinguished():
    """A holding company and the person behind it are both effective owners at
    the same percentage. A report that does not separate them reads as two."""
    book = {"99000147": [owns("99000163", "99000147", "0.50", label="Holdco")],
            "99000163": [owns("p1", "99000163", "1.00", kind="person", label="A Person")]}
    eff = {e.owner: e for e in effective_ownership(
        expand("99000147", as_of=AS_OF, load_claims=loader(book)))}
    assert eff["99000163"].share == eff["p1"].share == Decimal("0.50")
    assert eff["p1"].terminal and not eff["99000163"].terminal


def test_an_expanded_company_with_no_owners_is_still_terminal():
    """Regression from a live chain: a K/S or pension fund that we DID expand
    and which has no registered owners is a genuine terminus. Keying leaf
    detection on the stop reason reported a 7-node chain as having none."""
    book = {"99000147": [owns("99000163", "99000147", "0.60", label="Fund K/S")],
            "99000163": []}
    chain = expand("99000147", as_of=AS_OF, load_claims=lambda c: book.get(c) or (
        [] if c in book else None))
    r = ultimate_owners_are_named_persons(chain, AS_OF)
    assert r.value is V.FALSE
    assert "Fund K/S" in r.evidence


def test_designated_in_chain_checks_every_named_node():
    """The designation nexus is what the target *is*. Corporate nodes are
    matched by NAME, so a hit is a candidate for adjudication — never a
    conclusion."""
    from gleipnir.predicates.chain_preds import designated_in_chain

    class FakeTarget:
        id = "NK-1"
        designating_programmes = ("EU-IRN", "US-IRAN", "GB-IRAN")

    class FakeIndex:
        def by_name(self, n):
            return [FakeTarget()] if n == "Bad Holdco" else []

    book = {"99000147": [owns("99000163", "99000147", "0.60", label="Bad Holdco")],
            "99000163": [owns("p1", "99000163", "1.00", kind="person", label="A Person")]}
    chain = expand("99000147", as_of=AS_OF, load_claims=loader(book))
    r = designated_in_chain(chain, FakeIndex(), AS_OF)
    assert r.value is V.TRUE
    assert "NOT adjudicated" in r.evidence
    assert "EU-IRN" in r.evidence


def test_designated_in_chain_is_clean_when_nothing_matches():
    from gleipnir.predicates.chain_preds import designated_in_chain

    class Empty:
        def by_name(self, n):
            return []

    book = {"99000147": [owns("99000163", "99000147", "0.60", label="Fine Holdco")],
            "99000163": []}
    chain = expand("99000147", as_of=AS_OF,
                   load_claims=lambda c: book.get(c) if c in book else None)
    r = designated_in_chain(chain, Empty(), AS_OF)
    assert r.value is V.FALSE
    # The corpus size must not appear: it changes on every export, so two
    # identical screens would produce non-identical reports.
    assert "designations" not in r.evidence or "pinned" in r.evidence


def test_designated_in_chain_without_a_list_is_unknowable():
    from gleipnir.predicates.chain_preds import designated_in_chain
    book = {"99000147": [owns("2", "99000147", "1.0", label="X")], "2": []}
    chain = expand("99000147", as_of=AS_OF,
                   load_claims=lambda c: book.get(c) if c in book else None)
    assert designated_in_chain(chain, None, AS_OF).value is V.UNKNOWABLE
