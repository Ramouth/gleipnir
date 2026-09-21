"""GLEIF client. A 404 on a parent means *no parent reported*, not an error."""
import httpx

from gleipnir.adapters.gleif import GleifClient


def client_with(handler):
    c = GleifClient()
    c._c = httpx.Client(transport=httpx.MockTransport(handler),
                        base_url="https://api.gleif.org/api/v1")
    return c


def record(lei="L1", name="MARLOG AS", country="NO", status="ACTIVE"):
    return {"attributes": {"lei": lei, "entity": {
        "legalName": {"name": name}, "legalAddress": {"country": country},
        "status": status, "legalForm": {"id": "XTIQ"}}}}


def test_by_name_parses_records():
    c = client_with(lambda r: httpx.Response(200, json={"data": [record()]}))
    recs = c.by_name("MARLOG AS")
    assert recs[0].lei == "L1" and recs[0].country == "NO" and recs[0].status == "ACTIVE"


def test_country_narrows_the_query():
    seen = {}

    def handler(request):
        seen.update(dict(request.url.params))
        return httpx.Response(200, json={"data": []})

    client_with(handler).by_name("X", country="DK")
    assert seen["filter[entity.legalAddress.country]"] == "DK"
    assert seen["filter[entity.legalName]"] == "X"


def test_a_transport_failure_returns_no_candidates_rather_than_raising():
    """A GLEIF miss must never abort a screen — coverage skews to large
    entities and absence carries no information."""
    def boom(request):
        raise httpx.ConnectError("down")
    assert client_with(boom).by_name("X") == []


def test_no_parent_reported_is_none_not_an_error():
    """An entity at the top of its group legitimately has no parent."""
    c = client_with(lambda r: httpx.Response(404, json={"errors": [{"status": "404"}]}))
    assert c.parent("L1") is None


def test_a_reported_parent_is_returned_with_its_jurisdiction():
    c = client_with(lambda r: httpx.Response(200, json={
        "data": record(lei="L2", name="Real Parent AB", country="SE")}))
    edge = c.parent("L1", "direct")
    assert edge.parent_lei == "L2"
    assert edge.parent_name == "Real Parent AB"
    assert edge.parent_country == "SE"
    assert edge.kind == "direct"


def test_an_empty_data_payload_is_treated_as_no_parent():
    c = client_with(lambda r: httpx.Response(200, json={"data": None}))
    assert c.parent("L1") is None
