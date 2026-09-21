"""Analyst green flags: scoped, attributable, and stale when the facts move."""
from datetime import date

import pytest

from gleipnir.analyst import Observation, active_flag, apply_flag, record
from gleipnir.predicates.core import Result, Tier, V
from gleipnir.rawstore import RawStore


def obs(**kw):
    base = dict(subject="99000236", verdict="green", basis="officers are engineers",
                analyst="a", observed_at="2026-08-27T10:00:00+00:00",
                as_of="2026-08-27", sources=("https://example",),
                pinned=("hash1",), suppresses=("majority_owner_unresolvable",))
    base.update(kw)
    return Observation(**base)


def _results():
    return [Result("majority_owner_unresolvable", V.TRUE, Tier.B),
            Result("designated_in_chain", V.TRUE, Tier.A)]


def test_active_flag_suppresses_only_what_it_scoped(tmp_path):
    store = RawStore(tmp_path)
    record(store, obs())
    flag, status = active_flag(store, "99000236", {"hash1"})
    assert status == "active"
    kept, dropped = apply_flag(_results(), flag, status)
    assert [r.predicate for r in dropped] == ["majority_owner_unresolvable"]
    # A judgement about the officers must never suppress a sanctions hit.
    assert [r.predicate for r in kept] == ["designated_in_chain"]


def test_flag_goes_stale_when_the_pinned_facts_move(tmp_path):
    """The analyst judged a set of facts. If ownership or officers change, the
    flag covers the entity as it was, not as it is."""
    store = RawStore(tmp_path)
    record(store, obs())
    flag, status = active_flag(store, "99000236", {"hash2"})
    assert status == "stale"
    kept, dropped = apply_flag(_results(), flag, status)
    assert dropped == [] and len(kept) == 2


def test_withdrawal_is_an_append_not_a_deletion(tmp_path):
    store = RawStore(tmp_path)
    record(store, obs())
    record(store, obs(verdict="withdraw", observed_at="2026-09-01T10:00:00+00:00"))
    flag, status = active_flag(store, "99000236", {"hash1"})
    assert status == "withdrawn"
    assert apply_flag(_results(), flag, status)[1] == []


def test_no_flag_suppresses_nothing(tmp_path):
    flag, status = active_flag(RawStore(tmp_path), "99000236", set())
    assert status == "none"
    assert apply_flag(_results(), flag, status)[1] == []


def test_observation_carries_its_own_provenance(tmp_path):
    c = obs().to_claim()
    assert c.epistemic_tier == "analyst"
    assert c.qualifiers["analyst"] == "a"
    assert c.qualifiers["basis"]
    assert c.qualifiers["sources"] == ["https://example"]
    assert c.valid_from == date(2026, 8, 27)


def _put(store, subject, payload, source="cvr", resource_type="virksomhed"):
    return store.put(payload=payload, source=source, resource_type=resource_type,
                     resource_id=subject, http_status=200, request_params={}
                     ).content_hash


def test_current_hashes_is_the_latest_payload_not_every_one_ever_fetched(tmp_path):
    """The bug that made staleness inert.

    Every call site computed "every hash ever fetched for this resource_id". The
    raw store is append-only, so a superseded blob's hash never leaves that set,
    `moved` was always empty, and no flag could ever go stale. A judgement then
    outlived the facts it was pinned to — a blind spot wearing a suppression's
    clothes.
    """
    from gleipnir.analyst import current_hashes

    store = RawStore(tmp_path)
    first = _put(store, "99000236", b'{"v": 1}')
    assert current_hashes(store, "99000236") == {first}

    second = _put(store, "99000236", b'{"v": 2}')
    assert current_hashes(store, "99000236") == {second}, (
        "the superseded payload must leave the in-force set")

    record(store, obs(pinned=(first,)))
    _, status = active_flag(store, "99000236", current_hashes(store, "99000236"))
    assert status == "stale"


def test_each_resource_is_superseded_only_by_its_own_kind(tmp_path):
    """A new DNS observation does not supersede the company document."""
    from gleipnir.analyst import current_hashes

    store = RawStore(tmp_path)
    company = _put(store, "99000236", b'{"company": 1}')
    dns = _put(store, "99000236", b'{"mx": 1}', source="dns",
               resource_type="observation")
    assert current_hashes(store, "99000236") == {company, dns}


def test_a_judgement_is_not_part_of_what_it_was_judged_against(tmp_path):
    """Recording an observation must not move the state the flag pins to —
    otherwise a judgement pins to itself, and a second one restates the first."""
    from gleipnir.analyst import current_hashes

    store = RawStore(tmp_path)
    company = _put(store, "99000236", b'{"company": 1}')
    record(store, obs(pinned=(company,)))
    assert current_hashes(store, "99000236") == {company}
    _, status = active_flag(store, "99000236", current_hashes(store, "99000236"))
    assert status == "active"
