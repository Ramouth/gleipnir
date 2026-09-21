"""Goal decomposition, and group evidence with a stubbed GLEIF."""
from datetime import date

from gleipnir.adapters.gleif import LeiRecord, ParentEdge
from gleipnir.chain import Node, Stop
from gleipnir.claims import Claim, EntityRef, EpistemicTier, Predicate
from gleipnir.goals import GOALS
from gleipnir.group import GroupEvidence, assess
from gleipnir.plan import State, agenda

AS_OF = date(2026, 8, 27)


def state():
    return State(root="99000147", as_of=AS_OF)


class FakeChain:
    def __init__(self, unresolvable=()):
        self.unresolvable = [
            Node(EntityRef("other_party", f"anden:{i}", name), 1, Stop.UNRESOLVABLE)
            for i, name in enumerate(unresolvable)]
        self.nodes = {}


def keys(goals, s):
    return [n.key for n in agenda(goals, s)]


# ── decomposition ───────────────────────────────────────────────────────────

def test_cold_start_asks_only_for_what_nothing_depends_on():
    """Nothing known: the chain and the registry record. Not GLEIF, not the
    oracle — those are gated on results that do not exist yet."""
    assert set(keys(GOALS, state())) == {"ownership_chain", "registry_claims"}


def test_gleif_is_requested_only_for_a_node_cvr_could_not_resolve():
    s = state()
    s.mark("registry_claims", s.root, [])
    s.mark("ownership_chain", s.root, FakeChain(["MARLOG AS"]))
    s.mark("website", s.root, "u")
    assert "lei" in keys(GOALS, s)


def test_a_fully_resolved_chain_asks_gleif_for_nothing():
    s = state()
    s.mark("registry_claims", s.root, [])
    s.mark("ownership_chain", s.root, FakeChain())
    s.mark("website", s.root, "u")
    assert "lei" not in keys(GOALS, s)


def test_the_parent_lookup_is_gated_on_an_lei_actually_being_found():
    s = state()
    s.mark("registry_claims", s.root, [])
    s.mark("ownership_chain", s.root, FakeChain(["Examplia GmbH"]))
    s.mark("website", s.root, "u")
    s.mark("lei", "Examplia GmbH", None)          # searched, nothing found
    assert "lei_parent" not in keys(GOALS, s)
    s.mark("lei", "Examplia GmbH", LeiRecord("L", "Examplia GmbH", "DE", "ACTIVE"))
    assert "lei_parent" in keys(GOALS, s)


def test_accounts_are_requested_only_when_the_site_made_a_checkable_claim():
    """Fetching financials for a site that states no figures is a cost with no
    possible finding."""
    s = state()
    s.mark("registry_claims", s.root, [])
    s.mark("ownership_chain", s.root, FakeChain())
    s.mark("website", s.root, "u")
    s.mark("website_claims", s.root, [])
    assert "accounts" not in keys(GOALS, s)

    s.mark("website_claims", s.root, [Claim(
        subject=EntityRef("company", "99000147"), predicate=Predicate.EMPLOYS,
        object=200, source_id="website", epistemic_tier=EpistemicTier.SELF_DECLARED,
        raw_ref="w")])
    assert "accounts" in keys(GOALS, s)


def test_designation_work_waits_for_a_chain():
    s = state()
    s.mark("registry_claims", s.root, [])
    assert "sanctions_index" not in keys(GOALS, s)


# ── group evidence ──────────────────────────────────────────────────────────

class FakeGleif:
    def __init__(self, records=None, parent=None):
        self.records = records or {}
        self._parent = parent
        self.queries: list[str] = []

    def by_name(self, name, country=None):
        self.queries.append(name)
        return self.records.get(name, [])

    def parent(self, lei, kind="direct"):
        return self._parent


def test_an_estate_is_resolved_without_any_lookup():
    """'Boet efter <name>' is a deceased person's estate. It resolves to a
    named person and conceals nothing — and no lookup would help."""
    g = FakeGleif()
    f = assess("Boet efter Jens Peter Hansen", g)
    assert f.evidence is GroupEvidence.BENIGN_FORM and f.suppresses
    assert g.queries == [], "no network call should be made"


def test_name_variants_are_tried_until_one_hits():
    """A verbatim lookup missed a PE fund's Luxembourg vehicle; the suffix-stripped
    form found LEI 5493000EXAMPLE0LEI00."""
    name = "Bidco 7 (Luxembourg) Acquisition S.à.r.l."
    g = FakeGleif({"Bidco 7 (Luxembourg) Acquisition":
                   [LeiRecord("5493000EXAMPLE0LEI00", name, "LU", "ACTIVE")]})
    f = assess(name, g)
    assert f.lei == "5493000EXAMPLE0LEI00"
    assert g.queries[0] == name, "the verbatim form is tried first"
    assert len(g.queries) == 2


def test_a_bare_lei_does_not_suppress():
    """An LEI says the entity is verified and located, not that it sits inside
    a real group — a single-purpose vehicle can hold one."""
    g = FakeGleif({"MARLOG AS": [LeiRecord("L", "MARLOG AS", "NO", "ACTIVE")]},
                  parent=None)
    f = assess("MARLOG AS", g)
    assert f.evidence is GroupEvidence.REGISTERED and not f.suppresses


def test_a_documented_group_suppresses():
    g = FakeGleif({"Sub AB": [LeiRecord("L1", "Sub AB", "SE", "ACTIVE")]},
                  parent=ParentEdge("L1", "L2", "Real Parent AB", "SE", "direct"))
    f = assess("Sub AB", g)
    assert f.evidence is GroupEvidence.DOCUMENTED and f.suppresses
    assert f.parent_name == "Real Parent AB"


def test_a_lapsed_lei_is_recorded_and_does_not_suppress():
    """An LEI must be renewed annually or it lapses — a costly recurring signal
    in the other direction."""
    g = FakeGleif({"Old Ltd": [LeiRecord("L", "Old Ltd", "GB", "LAPSED")]})
    f = assess("Old Ltd", g)
    assert f.evidence is GroupEvidence.LAPSED and not f.suppresses


def test_no_lei_is_not_evidence_of_anything():
    """GLEIF coverage skews to financial and larger entities. Most ordinary
    companies have none and that fact carries no information."""
    f = assess("Eastport XR-TURBO Corp.", FakeGleif())
    assert f.evidence is GroupEvidence.NONE and not f.suppresses
    assert len(f.tried) >= 2
