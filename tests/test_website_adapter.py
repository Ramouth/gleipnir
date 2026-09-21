"""Website fetcher: politeness is a design constraint, not a nicety.

`architecture.md` §11 — a due diligence firm caught misbehaving is a due
diligence firm with a credibility problem, and integrity is the product.
"""
import httpx
import pytest

from gleipnir.adapters.website import (
    USER_AGENT, PageResponse, RobotsDisallowed, WebsiteClient, normalise_site,
)


def client_with(handler, **kw):
    w = WebsiteClient(delay=0.0, **kw)
    w._client = httpx.Client(transport=httpx.MockTransport(handler),
                             headers={"User-Agent": USER_AGENT},
                             follow_redirects=True)
    return w


def test_normalise_site_accepts_bare_hostnames():
    """CVR files websites as bare hostnames as often as URLs."""
    assert normalise_site("www.pharmaco.example") == "https://www.pharmaco.example"
    assert normalise_site("https://x.dk") == "https://x.dk"
    assert normalise_site("http://x.dk") == "http://x.dk"
    assert normalise_site("  ") == ""


def test_a_disallowed_path_is_not_fetched():
    seen = []

    def handler(request):
        seen.append(str(request.url))
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /")
        return httpx.Response(200, text="<html>secret</html>")

    w = client_with(handler)
    assert not w.may_fetch("https://example.dk/om-os")
    with pytest.raises(RobotsDisallowed):
        w.get("https://example.dk/om-os")
    assert all("/robots.txt" in u for u in seen), "only robots.txt was requested"


def test_an_allowed_path_is_fetched():
    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /private")
        return httpx.Response(200, text="<html>ok</html>",
                              headers={"content-type": "text/html"})

    w = client_with(handler)
    assert w.may_fetch("https://example.dk/")
    resp = w.get("https://example.dk/")
    assert resp.http_status == 200 and b"ok" in resp.body


def test_a_missing_robots_file_is_permissive():
    """The standard interpretation — but nothing should imply we read one."""
    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, text="<html>ok</html>")

    assert client_with(handler).may_fetch("https://example.dk/")


def test_robots_is_fetched_once_per_host():
    calls = []

    def handler(request):
        if request.url.path == "/robots.txt":
            calls.append(request.url.host)
        return httpx.Response(200, text="User-agent: *\nAllow: /")

    w = client_with(handler)
    w.may_fetch("https://a.dk/one")
    w.may_fetch("https://a.dk/two")
    w.may_fetch("https://b.dk/one")
    assert calls == ["a.dk", "b.dk"]


def test_the_body_is_capped():
    """A 4MB cap stops one pathological page consuming the run."""
    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="")
        return httpx.Response(200, content=b"x" * 5000)

    w = client_with(handler, max_bytes=1000)
    assert len(w.get("https://example.dk/").body) == 1000


def test_we_identify_ourselves():
    assert "GleipnirBot" in USER_AGENT
    assert "robots.txt" in USER_AGENT


def test_the_final_url_after_redirects_is_recorded():
    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="")
        if request.url.host == "old.dk":
            return httpx.Response(301, headers={"location": "https://new.dk/"})
        return httpx.Response(200, text="<html>moved</html>")

    resp = client_with(handler).get("https://old.dk/")
    assert resp.url == "https://new.dk/"


def test_page_response_is_immutable():
    r = PageResponse(url="u", body=b"", http_status=200, content_type="text/html")
    with pytest.raises(Exception):
        r.url = "other"
