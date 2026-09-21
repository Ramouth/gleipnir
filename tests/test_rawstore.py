import json

from gleipnir.rawstore import RawStore


def _put(store, payload: bytes, rid="99000147", status=200):
    return store.put(
        payload=payload,
        source="cvr",
        resource_type="virksomhed",
        resource_id=rid,
        http_status=status,
        request_params={"size": 1},
    )


def test_identical_payloads_share_one_blob_but_log_twice(tmp_path):
    store = RawStore(tmp_path)
    a = _put(store, b'{"x":1}')
    b = _put(store, b'{"x":1}')
    assert a.content_hash == b.content_hash
    # "we asked again and nothing had changed" is a fact the bitemporal layer
    # needs, so the log grows even though the blob does not.
    assert len(store.fetches()) == 2
    blobs = list((tmp_path / "blobs").rglob("*.json"))
    assert len(blobs) == 1


def test_roundtrip_json(tmp_path):
    store = RawStore(tmp_path)
    rec = _put(store, json.dumps({"navn": "Acme Vodka ApS"}).encode())
    assert store.get_json(rec.content_hash)["navn"] == "Acme Vodka ApS"


def test_latest_ignores_failed_fetches(tmp_path):
    store = RawStore(tmp_path)
    ok = _put(store, b'{"v":1}', status=200)
    _put(store, b'{"error":"gone"}', status=404)
    latest = store.latest("cvr", "virksomhed", "99000147")
    assert latest is not None
    assert latest.content_hash == ok.content_hash


def test_latest_returns_none_for_unknown_resource(tmp_path):
    store = RawStore(tmp_path)
    assert store.latest("cvr", "virksomhed", "99999999") is None
