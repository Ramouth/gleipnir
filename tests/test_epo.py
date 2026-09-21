"""EPO OPS: the client's manners, and what the payloads are allowed to assert.

The fixtures mirror shapes taken from live responses, including the two that
caught the parser out: parties nest plural-then-singular (`applicants` >
`applicant`), and a search result carries `docdb` references where only the
`biblio` constituent carries names and dates.
"""
from datetime import date

import httpx
import pytest

from gleipnir.adapters.epo import (
    OpsAuthError, OpsClient, OpsRequestError, OpsThrottled, Throttle,
)
from gleipnir.claims import EpistemicTier, Predicate
from gleipnir.extract.epo import (
    extract_applicant_search, extract_legal_events, is_transfer,
)

THROTTLE = "busy (images=green:100, inpadoc=green:45, other=green:1000, retrieval=green:100)"


# ── payloads ────────────────────────────────────────────────────────────────

def search_biblio(applicant="NORDIC PHARMA A/S", epodoc_applicant="NORDIC PHARMA AS [DK]",
                  inventor="DOE JANE", number="WO2026000001",
                  when="20260820", total="4551"):
    bib = {
        "publication-reference": {"document-id": [
            {"@document-id-type": "docdb", "country": {"$": "WO"},
             "doc-number": {"$": "2026171974"}, "kind": {"$": "A2"},
             "date": {"$": when}},
            {"@document-id-type": "epodoc", "doc-number": {"$": number},
             "date": {"$": when}},
        ]},
        "invention-title": [{"$": "A drug delivery device"}],
        # The nesting that produced no names at all: plural, then singular.
        "parties": {
            "applicants": {"applicant": [
                {"@data-format": "epodoc",
                 "applicant-name": {"name": {"$": epodoc_applicant}}},
                {"@data-format": "original",
                 "applicant-name": {"name": {"$": applicant}}},
            ]},
            "inventors": {"inventor": [
                {"@data-format": "epodoc",
                 "inventor-name": {"name": {"$": inventor}}},
            ]},
        },
    }
    document = {"@family-id": "90000001", "@country": "WO",
                "@doc-number": "2026171974", "bibliographic-data": bib}
    return {"ops:world-patent-data": {"ops:biblio-search": {
        "@total-result-count": total,
        "ops:search-result": {"exchange-documents": [
            {"exchange-document": document}]},
    }}}


def legal(desc, code="RAP1", gazette="2011-03-16"):
    return {"ops:world-patent-data": {"ops:patent-family": {
        "ops:family-member": [{
            "publication-reference": {"document-id": [
                {"@document-id-type": "epodoc", "doc-number": {"$": "EP1000000"}}]},
            "ops:legal": [{
                "@code": code, "@desc": desc,
                "ops:L007EP": {"$": gazette, "@desc": "Gazette DATE"},
            }]}]}}}


# ── the client ──────────────────────────────────────────────────────────────

def client_with(handler, **kw):
    c = OpsClient("key", "secret", min_interval=0.0, **kw)
    c._c = httpx.Client(base_url="https://ops.epo.org/3.2",
                        transport=httpx.MockTransport(handler))
    return c


def test_it_authenticates_once_and_reuses_the_token():
    calls = {"token": 0, "get": 0}

    def handler(request):
        if request.url.path.endswith("/accesstoken"):
            calls["token"] += 1
            return httpx.Response(200, json={"access_token": "T", "expires_in": 1199})
        calls["get"] += 1
        assert request.headers["Authorization"] == "Bearer T"
        return httpx.Response(200, json={}, headers={"X-Throttling-Control": THROTTLE})

    c = client_with(handler)
    c.search_applicant("Fixture ApS")
    c.search_applicant("Other ApS")
    assert (calls["token"], calls["get"]) == (1, 2)


def test_a_401_mid_flight_re_authenticates_once():
    """The token lives twenty minutes. Retrying on the server's answer rather
    than on a local timer means a wrong clock cannot strand the client."""
    seen = {"token": 0, "get": 0}

    def handler(request):
        if request.url.path.endswith("/accesstoken"):
            seen["token"] += 1
            return httpx.Response(200, json={"access_token": f"T{seen['token']}"})
        seen["get"] += 1
        if seen["get"] == 1:
            return httpx.Response(401)
        return httpx.Response(200, json={"ok": True})

    c = client_with(handler)
    assert c.search_applicant("Fixture ApS").http_status == 200
    assert seen == {"token": 2, "get": 2}


def test_repeated_rejection_is_an_auth_error_not_an_empty_result():
    """A credential failure must stop a batch, not mark every company as having
    no patents."""
    def handler(request):
        if request.url.path.endswith("/accesstoken"):
            return httpx.Response(200, json={"access_token": "T"})
        return httpx.Response(401)

    with pytest.raises(OpsAuthError):
        client_with(handler).search_applicant("Fixture ApS")


def test_bad_credentials_fail_at_the_token_endpoint():
    def handler(request):
        return httpx.Response(401, text="<error>Client credentials are invalid</error>")

    with pytest.raises(OpsAuthError, match="rejected the consumer key"):
        client_with(handler).search_applicant("Fixture ApS")


def test_a_missing_key_is_refused_before_any_request():
    with pytest.raises(OpsAuthError):
        OpsClient("", "")


def test_a_404_is_returned_because_it_is_a_fact():
    """"No publication under this number" is a statement about the register and
    the caller has to be able to store it."""
    def handler(request):
        if request.url.path.endswith("/accesstoken"):
            return httpx.Response(200, json={"access_token": "T"})
        return httpx.Response(404, text="not found")

    assert client_with(handler).biblio("EP9999999").http_status == 404


def test_a_server_error_is_raised():
    def handler(request):
        if request.url.path.endswith("/accesstoken"):
            return httpx.Response(200, json={"access_token": "T"})
        return httpx.Response(503)

    with pytest.raises(OpsRequestError):
        client_with(handler).search_applicant("Fixture ApS")


def test_an_exhausted_bucket_stops_the_client():
    def handler(request):
        if request.url.path.endswith("/accesstoken"):
            return httpx.Response(200, json={"access_token": "T"})
        return httpx.Response(200, json={}, headers={
            "X-Throttling-Control": "idle (inpadoc=black:0, other=green:1000)"})

    c = client_with(handler)
    c.search_applicant("Fixture ApS")           # learns the bucket state
    with pytest.raises(OpsThrottled, match="inpadoc"):
        c.legal("EP1000000")
    c.search_applicant("Still fine")            # a different bucket still moves


def test_the_throttle_header_is_parsed():
    t = Throttle.parse(THROTTLE)
    assert t.overall == "busy"
    assert t.services["inpadoc"] == ("green", 45)
    assert not t.blocked("inpadoc")
    assert Throttle.parse("").services == {}


def test_a_quote_in_a_company_name_cannot_break_the_query():
    """Otherwise a 400 comes back and reads as "this company has no patents"."""
    seen = {}

    def handler(request):
        if request.url.path.endswith("/accesstoken"):
            return httpx.Response(200, json={"access_token": "T"})
        seen["q"] = request.url.params.get("q")
        return httpx.Response(200, json={})

    client_with(handler).search_applicant('Bad " name ApS')
    assert seen["q"] == 'pa="Bad   name ApS"'


def test_the_range_never_exceeds_what_ops_will_return():
    seen = {}

    def handler(request):
        if request.url.path.endswith("/accesstoken"):
            return httpx.Response(200, json={"access_token": "T"})
        seen["range"] = request.url.params.get("Range")
        return httpx.Response(200, json={})

    client_with(handler).search_applicant("Fixture ApS", count=5000)
    assert seen["range"] == "1-100"


# ── the extractor ───────────────────────────────────────────────────────────

def test_an_applicant_search_yields_dated_claims():
    claims = extract_applicant_search(search_biblio(), query="nordic pharma",
                                      raw_ref="hash", observed_at="2026-08-28")
    holds = [c for c in claims if c.predicate is Predicate.HOLDS_PATENT_APPLICATION]
    assert holds, "the plural/singular nesting must be traversed"
    assert {c.valid_from for c in holds} == {date(2026, 8, 20)}
    assert all(c.raw_ref == "hash" for c in claims)
    assert all(c.epistemic_tier is EpistemicTier.REGISTERED for c in claims)


def test_a_hit_is_a_name_match_and_never_an_identity():
    """A string matched a string. Nothing here resolves to a CVR number."""
    claims = extract_applicant_search(search_biblio(), query="nordic pharma",
                                      raw_ref="hash")
    for c in claims:
        assert c.subject.key.startswith("name:")
        assert c.qualifiers["match"] in ("applicant_name", "inventor_name")
        assert c.qualifiers["query"] == "nordic pharma"


def test_the_country_suffix_is_kept_as_a_qualifier_not_as_a_name():
    claims = extract_applicant_search(search_biblio(), query="q", raw_ref="hash")
    holds = [c for c in claims if c.predicate is Predicate.HOLDS_PATENT_APPLICATION]
    assert {c.qualifiers["applicant_country"] for c in holds} == {"DK"}
    assert all("[DK]" not in (c.subject.label or "") for c in holds)


def test_inventors_are_separated_from_applicants():
    """An inventor is not an owner, and the name cannot be disambiguated."""
    claims = extract_applicant_search(search_biblio(), query="q", raw_ref="hash")
    inv = [c for c in claims if c.predicate is Predicate.NAMED_AS_INVENTOR]
    assert inv and all(c.subject.kind == "person" for c in inv)
    assert all(c.predicate is not Predicate.OWNS for c in claims)


def test_a_result_without_a_publication_number_is_skipped():
    payload = search_biblio()
    doc = payload["ops:world-patent-data"]["ops:biblio-search"][
        "ops:search-result"]["exchange-documents"][0]["exchange-document"]
    doc["bibliographic-data"]["publication-reference"] = {}
    assert extract_applicant_search(payload, query="q", raw_ref="h") == []


def test_an_empty_search_yields_nothing_rather_than_raising():
    assert extract_applicant_search({}, query="q", raw_ref="h") == []
    assert extract_applicant_search(
        {"ops:world-patent-data": {"ops:biblio-search": {}}}, query="q",
        raw_ref="h") == []


# ── chain of title ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("desc", [
    "TRANSFER OF RIGHTS OF AN EP APPLICATION",
    "FR: TRANSFER OF PROPERTY",
    "CHANGE OF PROPRIETOR",
    "GB: ASSIGNMENT OF THE PATENT",
])
def test_a_transfer_is_recognised_whatever_office_wrote_it(desc):
    """Detected on the EPO's own description rather than a national code table,
    which would silently miss whichever code nobody listed."""
    assert is_transfer(desc)


@pytest.mark.parametrize("desc", [
    "BE: CHANGE OF HOLDER'S NAME",          # the holder renamed itself
    "FIRST EXAMINATION REPORT DESPATCHED",
    "ANNUAL FEE PAID TO NATIONAL OFFICE",
    "REFERENCE TO A NATIONAL CODE",
])
def test_prosecution_history_is_not_a_transfer(desc):
    assert not is_transfer(desc)


def test_a_transfer_becomes_a_dated_claim():
    claims = extract_legal_events(legal("TRANSFER OF RIGHTS OF AN EP APPLICATION"),
                                  publication="EP1000000", raw_ref="hash")
    assert len(claims) == 1
    c = claims[0]
    assert c.predicate is Predicate.PATENT_RIGHTS_TRANSFERRED
    assert c.valid_from == date(2011, 3, 16)
    assert c.qualifiers["legal_event_code"] == "RAP1"


def test_only_transfers_are_imported():
    """The rest of an INPADOC record is a patent attorney's diary."""
    assert extract_legal_events(legal("FIRST EXAMINATION REPORT DESPATCHED", "17Q"),
                                publication="EP1000000", raw_ref="h") == []


def test_an_undated_transfer_is_still_a_claim():
    payload = legal("CHANGE OF PROPRIETOR")
    del payload["ops:world-patent-data"]["ops:patent-family"][
        "ops:family-member"][0]["ops:legal"][0]["ops:L007EP"]
    claims = extract_legal_events(payload, publication="EP1000000", raw_ref="h")
    assert len(claims) == 1 and claims[0].valid_from is None


def test_an_empty_legal_payload_yields_nothing():
    assert extract_legal_events({}, publication="EP1", raw_ref="h") == []
