"""CVR Elasticsearch client: quota is per request, and auth failure is global."""
import httpx
import pytest

from gleipnir.adapters.cvr import (
    COMPANY_INDEX, CvrAuthError, CvrClient, CvrRequestError, normalise_cvr,
)


def client_with(handler):
    c = CvrClient(api_key="dGVzdA==", base_url="http://x")
    c._client = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://x")
    return c


def hits(*ids, sort=True):
    return {"hits": {"hits": [
        {"_source": {"Vrvirksomhed": {"cvrNummer": i}},
         **({"sort": [i]} if sort else {})} for i in ids]}}


def test_auth_failure_is_raised_not_swallowed():
    """It is not resource-specific: every subsequent call fails identically, so
    a batch run must stop rather than mark ten thousand entities not-found."""
    for code in (401, 403):
        c = client_with(lambda r, _c=code: httpx.Response(_c))
        with pytest.raises(CvrAuthError):
            c.search(COMPANY_INDEX, {})


def test_other_upstream_failures_are_request_scoped():
    c = client_with(lambda r: httpx.Response(500))
    with pytest.raises(CvrRequestError):
        c.search(COMPANY_INDEX, {})


def test_a_transport_error_is_wrapped():
    def boom(request):
        raise httpx.ConnectError("no route")
    with pytest.raises(CvrRequestError):
        client_with(boom).search(COMPANY_INDEX, {})


def test_company_lookup_validates_the_checksum_before_spending_a_call():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(200, json=hits(99000147))

    c = client_with(handler)
    with pytest.raises(ValueError, match="checksum"):
        c.company("12345678")
    assert calls == [], "a malformed number must not cost a request"
    c.company("99000147")
    assert len(calls) == 1


def test_scan_pages_with_search_after_and_stops_when_empty():
    """One page is one request — this is what makes calibration affordable at
    ~1000 records per call instead of one."""
    seen = []

    def handler(request):
        import json
        body = json.loads(request.content)
        seen.append(body.get("search_after"))
        after = (body.get("search_after") or [0])[0]
        if after >= 6:
            return httpx.Response(200, json=hits())
        return httpx.Response(200, json=hits(after + 1, after + 2, after + 3))

    pages = list(client_with(handler).scan(COMPANY_INDEX, {"query": {}}, page_size=3))
    assert [len(p) for p in pages] == [3, 3]
    assert seen == [None, [3], [6]]


def test_scan_stops_rather_than_looping_when_a_response_carries_no_sort():
    def handler(request):
        return httpx.Response(200, json=hits(1, 2, sort=False))
    pages = list(client_with(handler).scan(COMPANY_INDEX, {"query": {}}, page_size=2))
    assert len(pages) == 1


def test_scan_supplies_a_default_sort_so_paging_is_deterministic():
    import json
    captured = {}

    def handler(request):
        captured.update(json.loads(request.content))
        return httpx.Response(200, json=hits())

    list(client_with(handler).scan(COMPANY_INDEX, {"query": {}}))
    assert captured["sort"] == [{"_doc": "asc"}]


def test_a_caller_supplied_sort_is_respected():
    import json
    captured = {}

    def handler(request):
        captured.update(json.loads(request.content))
        return httpx.Response(200, json=hits())

    q = {"query": {}, "sort": [{"Vrvirksomhed.cvrNummer": "asc"}]}
    list(client_with(handler).scan(COMPANY_INDEX, q))
    assert captured["sort"] == [{"Vrvirksomhed.cvrNummer": "asc"}]


def test_from_userpass_builds_the_basic_token():
    import base64
    c = CvrClient.from_userpass("u", "p", "http://x")
    assert c._client.headers["Authorization"] == \
        "Basic " + base64.b64encode(b"u:p").decode()
    c.close()


def test_a_missing_credential_fails_fast():
    with pytest.raises(RuntimeError, match="CVR_API_KEY"):
        CvrClient(api_key="", base_url="http://x")


def test_normalise_rejects_a_number_that_cannot_be_a_cvr():
    for bad in ("", "abc", "123456789"):
        with pytest.raises(ValueError):
            normalise_cvr(bad)
