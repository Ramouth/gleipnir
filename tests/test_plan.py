"""The resolution planner: the knowledge structure driving acquisition.

These pin the three properties that make it a rule engine rather than a prompt —
the agenda is derived, the stopping rule is deterministic, and an unknowable
need stops generating work.
"""
from datetime import date

from gleipnir.plan import Act, Goal, Need, State, agenda, run

AS_OF = date(2026, 8, 27)


def need(key, cost=1, subject="root", source="src"):
    return Need(key, subject, source, Act.FETCH, cost, f"because {key}")


def goal(name, keys, after=()):
    return Goal(name, f"q:{name}",
                lambda s, _k=tuple(keys): [need(k) for k in _k if not s.has(k)],
                after=after)


def state():
    return State(root="root", as_of=AS_OF)


# ── agenda derivation ───────────────────────────────────────────────────────

def test_a_closed_goal_generates_nothing():
    s = state()
    s.mark("a", "root", 1)
    assert agenda([goal("g", ["a"])], s) == []


def test_agenda_is_ordered_cheapest_first():
    """Cheap needs often make expensive ones unnecessary: reading a website
    costs nothing and can close a question that would take ten registry calls
    to answer structurally."""
    g = Goal("g", "q", lambda s: [need("expensive", 9), need("cheap", 0)])
    assert [n.key for n in agenda([g], state())] == ["cheap", "expensive"]


def test_the_same_need_is_not_queued_twice_for_two_goals():
    s = state()
    goals = [goal("g1", ["shared"]), goal("g2", ["shared"])]
    assert len(agenda(goals, s)) == 1


def test_ordering_is_total_so_a_budget_cap_drops_the_same_items_every_run():
    g = Goal("g", "q", lambda s: [need("b", 1), need("a", 1), need("c", 1)])
    assert [n.key for n in agenda([g], state())] == ["a", "b", "c"]


# ── unknowable ──────────────────────────────────────────────────────────────

def test_an_unknowable_need_stops_generating_actions():
    """Otherwise the planner loops forever on a source that answers 404, and
    the report renders a coverage hole as an open question."""
    s = state()
    s.give_up("a", "root", "source has no such record")
    assert agenda([goal("g", ["a"])], s) == []


def test_unknowable_is_distinct_from_absent_and_from_held():
    """Three states, not two. `has` must be False for an abandoned need, or a
    goal written as `if not s.has(k)` treats giving up as having it."""
    s = state()
    assert not s.has("a") and not s.dead("a") and not s.recorded("a")
    s.give_up("a", "root", "why")
    assert s.dead("a")
    assert not s.has("a"), "an abandoned need is not held"
    assert s.recorded("a"), "but the conclusion is recorded, so nothing is lost"
    s2 = state()
    s2.mark("b", "root", 1)
    assert s2.has("b") and not s2.dead("b") and s2.recorded("b")


def test_a_goal_whose_prerequisite_is_unknowable_is_dropped():
    """A coverage limit must propagate, not become a false negative on the
    goal that depended on it."""
    s = state()
    s.give_up("chain", "root", "registry unreachable")
    goals = [goal("ubo", ["chain"]), goal("designation", ["names"], after=("ubo",))]
    assert agenda(goals, s) == []


# ── fixpoint ────────────────────────────────────────────────────────────────

def test_run_reaches_fixpoint_and_returns_nothing_outstanding():
    s = state()
    order = []

    def execute(n, st):
        order.append(n.key)
        st.mark(n.key, n.subject, True)
        return True

    left = run([goal("g", ["a", "b", "c"])], s, execute, budget=10)
    assert left == []
    assert sorted(order) == ["a", "b", "c"]


def test_a_need_that_changes_nothing_is_marked_unknowable_not_retried():
    s = state()
    calls = []

    def execute(n, st):
        calls.append(n.key)
        return False

    run([goal("g", ["a"])], s, execute, budget=10, max_rounds=5)
    assert calls == ["a"], "a non-progressing need must not be retried each round"
    assert s.dead("a")


def test_budget_stops_spending_and_leaves_the_need_outstanding():
    s = state()
    g = Goal("g", "q", lambda st: [] if st.has("big") else [need("big", 99)])

    def execute(n, st):
        st.mark(n.key, n.subject, True)
        st.spent += n.cost
        return True

    left = run([g], s, execute, budget=5)
    assert [n.key for n in left] == ["big"]
    assert s.spent == 0, "nothing was spent, because nothing was affordable"


def test_run_terminates_when_no_round_makes_progress():
    """Guards against a goal whose need can never be satisfied by its own
    executor looping to max_rounds and beyond."""
    s = state()
    g = Goal("g", "q", lambda st: [need("never")])
    left = run([g], s, lambda n, st: True, budget=100, max_rounds=3)
    assert [n.key for n in left] == ["never"]


def test_state_scopes_needs_by_subject():
    s = state()
    s.mark("lei", "Owner A", object())
    assert s.has("lei", "Owner A")
    assert not s.has("lei", "Owner B")


def test_blocked_is_distinct_from_closed():
    """A closed goal is decidable with nothing outstanding. A blocked goal is
    undecidable and will stay that way. Only the second propagates."""
    from gleipnir.plan import is_blocked
    s = state()
    closed_goal = goal("g", ["a"])
    s.mark("a", "root", 1)
    assert not is_blocked(closed_goal, s)

    s2 = state()
    s2.give_up("a", "root", "gone")
    assert is_blocked(goal("g", ["a"]), s2)


def test_a_partially_answerable_prerequisite_does_not_block_dependents():
    s = state()
    s.give_up("chain", "root", "unreachable")
    goals = [
        Goal("ubo", "q", lambda st: [n for n in (need("chain"), need("other"))
                                     if not st.has(n.key)]),
        goal("designation", ["names"], after=("ubo",)),
    ]
    keys = {n.key for n in agenda(goals, s)}
    assert "names" in keys, "one dead need must not block a goal that has others"
