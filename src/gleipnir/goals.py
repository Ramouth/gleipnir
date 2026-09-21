"""Gleipnir's goals, and the needs each one generates.

This is the answer to "what to find". Each goal states a question a screen must
close, and decomposes into needs that name *which source* can close them. The
planner turns unmet needs into an ordered agenda; nothing here fetches.

The decomposition is where cross-source work comes from. A single-source
predicate can only ever describe structure — `docs/loop-log.md` iteration 6
measured that and found two of six predicates were restating cap-table size.
A *need* is what makes the system reach for a second source, and the goal is
what makes the reach non-arbitrary.
"""
from __future__ import annotations

from gleipnir.claims import Predicate
from gleipnir.cv import build as build_cv, expansions
from gleipnir.plan import Act, Goal, Need, State


def _chain(s: State):
    return s.have.get(f"ownership_chain:{s.root}")


def _needs_ubo(s: State) -> list[Need]:
    chain = _chain(s)
    if chain is None:
        return [Need("ownership_chain", s.root, "cvr", Act.EXPAND, 5,
                     "who ultimately controls this entity is undecidable without "
                     "the ownership chain")]
    out: list[Need] = []
    for node in chain.unresolvable:
        label = node.ref.label
        if not label:
            continue
        # The cross-source hop. CVR records this owner as a name and nothing
        # else; GLEIF may hold an authoritative parent edge for it.
        if not s.has("lei", label):
            out.append(Need("lei", label, "gleif", Act.FETCH, 1,
                            f"chain stops at {label!r}; GLEIF may carry an "
                            f"authoritative Level 2 parent for it"))
        elif s.have.get(f"lei:{label}") and not s.has("lei_parent", label):
            out.append(Need("lei_parent", label, "gleif", Act.FETCH, 1,
                            f"{label!r} has an LEI; its Level 2 parent continues "
                            f"the chain past CVR's edge"))
    return out


def _needs_narrative(s: State) -> list[Need]:
    if not s.has("registry_claims"):
        return [Need("registry_claims", s.root, "cvr", Act.FETCH, 1,
                     "nothing to compare a self-declared claim against")]
    if not s.has("website"):
        return [Need("website", s.root, "website", Act.FETCH, 0,
                     "the company's own account of itself is the only thing that "
                     "can contradict the register")]
    return []


def _needs_scale(s: State) -> list[Need]:
    """Only asks for accounts when the website actually made a claim worth
    checking. Fetching financials for a site that states no figures is a cost
    with no possible finding."""
    web = s.have.get(f"website_claims:{s.root}")
    if web is None:
        return []
    if not any(c.predicate in ("employs", "founded_on") for c in web):
        return []
    if not s.has("accounts"):
        return [Need("accounts", s.root, "regnskaber", Act.FETCH, 1,
                     "the site states a headcount or founding date; filed accounts "
                     "are the authoritative comparator")]
    return []


def _needs_designation(s: State) -> list[Need]:
    chain = _chain(s)
    if chain is None:
        return []
    if not s.has("sanctions_index", "global"):
        return [Need("sanctions_index", "global", "opensanctions", Act.FETCH, 0,
                     "a designation nexus is what the target *is*; without the list "
                     "there is no test")]
    out = []
    for cand in s.have.get(f"designation_candidates:{s.root}") or []:
        if not s.has("identity_verdict", cand):
            out.append(Need("identity_verdict", cand, "oracle", Act.RESOLVE, 0,
                            f"{cand!r} matches a designated name; only an "
                            f"adjudicated verdict can turn that into a finding"))
    return out


def _people(s: State) -> list:
    """Everyone the register names in a role at the subject company.

    Officers, not owners: an owner is a chain question and `_needs_ubo` already
    asks it. This goal is about the people who run the company, because a
    person's record is what reaches outside the register that filed it.
    """
    registry = s.have.get(f"registry_claims:{s.root}") or []
    seen, out = set(), []
    for c in registry:
        if c.predicate is not Predicate.HAS_ROLE:
            continue
        person = c.object
        key = getattr(person, "key", None)
        if key and key not in seen:
            seen.add(key)
            out.append(person)
    return out


def _needs_cv(s: State) -> list[Need]:
    """A CV that names an entity abroad is what licenses the next register.

    The trigger is a fact somebody filed — this person is recorded at that
    company, in that country — so the agenda stays derived rather than judged.
    Nothing here is a risk assessment: working for an Estonian company is
    ordinary, and `docs/jurisdictions.md` measured the population it sits in.
    """
    registry = s.have.get(f"registry_claims:{s.root}") or []
    if not registry:
        return []

    out: list[Need] = []
    for person in _people(s):
        claims = list(registry)
        claims += s.have.get(f"attested_claims:{person.key}") or []
        claims += s.have.get(f"affiliations:{person.key}") or []
        cv = build_cv(claims, person)
        for exp in expansions(cv):
            key = f"foreign_entity"
            subject = exp.entry.entity.label or exp.entry.entity.key
            if s.recorded(key, subject):
                continue
            if not exp.resolvable:
                # Known to be abroad, and no connected register covers it.
                # Recorded as a coverage statement rather than queued forever.
                s.give_up(key, subject, exp.because)
                continue
            out.append(Need(key, subject, exp.source, Act.FETCH, 1, exp.because))
    return out


GOALS: list[Goal] = [
    Goal("ubo_resolved",
         "Who ultimately controls this entity, across every route?",
         _needs_ubo),
    Goal("narrative_consistent",
         "Does the company's own account of itself match the register?",
         _needs_narrative),
    Goal("scale_consistent",
         "Do claimed headcount and age match what was filed?",
         _needs_scale, after=("narrative_consistent",)),
    Goal("cv_resolved",
         "Does anyone who runs this company have a record outside Denmark, and "
         "what does that jurisdiction's register say about it?",
         _needs_cv, after=("narrative_consistent",)),
    Goal("designation_nexus",
         "Does any node in the chain touch a designated party?",
         _needs_designation, after=("ubo_resolved",)),
]
