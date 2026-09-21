"""Source registry licence gating, and the streaming raw-store path."""
import pytest

from gleipnir.rawstore import RawStore
from gleipnir.sources import REGISTRY, AuthMode, Licence, by_auth, redistributable


def test_every_source_declares_auth_and_licence():
    for spec in REGISTRY.values():
        assert isinstance(spec.auth, AuthMode)
        assert isinstance(spec.licence, Licence)
        assert spec.base_url.startswith("http")


def test_unknown_licence_is_treated_as_not_redistributable():
    """A source whose terms nobody has read is not a source whose terms permit
    resale. Fails closed."""
    assert not redistributable("statstidende")     # Licence.UNKNOWN
    assert not redistributable("nonexistent")


def test_non_commercial_sources_cannot_be_quoted_in_a_sold_report():
    """OpenSanctions' free tier is non-commercial: analysis yes, a report sold
    to a bank quoting it no."""
    assert not redistributable("opensanctions_bulk")
    assert REGISTRY["opensanctions_bulk"].licence is Licence.NON_COMMERCIAL


def test_open_sources_are_redistributable():
    assert redistributable("gleif")
    assert redistributable("ofac_sdn")


def test_registry_lookup_sources_are_not_bulk_redistributable():
    """Most registries permit lookup and analysis while forbidding
    redistribution — CVR among them."""
    assert REGISTRY["cvr"].licence is Licence.LOOKUP_ONLY
    assert not redistributable("cvr")


def test_sources_needing_registration_are_enumerable():
    ids = {s.id for s in by_auth(AuthMode.REGISTER)}
    assert {"statstidende", "epo_ops"} <= ids


# ── streaming store ─────────────────────────────────────────────────────────

def test_put_stream_hashes_and_stores_a_large_payload(tmp_path):
    store = RawStore(tmp_path)
    chunks = [b"a" * 1000, b"b" * 1000, b"c" * 500]
    rec = store.put_stream(iter(chunks), source="opensanctions",
                           resource_type="bulk", resource_id="sanctions",
                           http_status=200, request_params={"url": "u"})
    assert rec.byte_len == 2500
    assert store.get(rec.content_hash) == b"".join(chunks)


def test_put_stream_is_content_addressed_like_put(tmp_path):
    store = RawStore(tmp_path)
    a = store.put_stream(iter([b"same"]), source="s", resource_type="t",
                         resource_id="r", http_status=200, request_params={})
    b = store.put(payload=b"same", source="s", resource_type="t",
                  resource_id="r", http_status=200, request_params={})
    assert a.content_hash == b.content_hash
    assert len(list((tmp_path / "blobs").rglob("*.json"))) == 1


def test_an_interrupted_stream_leaves_no_blob(tmp_path):
    """A truncated blob under a hash claiming to describe its full contents
    would be trusted by every later reparse."""
    store = RawStore(tmp_path)

    def exploding():
        yield b"partial"
        raise OSError("connection reset")

    with pytest.raises(OSError):
        store.put_stream(exploding(), source="s", resource_type="t",
                         resource_id="r", http_status=200, request_params={})
    assert list((tmp_path / "blobs").rglob("*.json")) == []
    assert store.fetches() == []


def test_path_of_points_at_a_readable_blob(tmp_path):
    store = RawStore(tmp_path)
    rec = store.put_stream(iter([b"xyz"]), source="s", resource_type="t",
                           resource_id="r", http_status=200, request_params={})
    assert store.path_of(rec.content_hash).read_bytes() == b"xyz"
