"""Layer 1 — company website adapter.

Fetches a page, stores the exact bytes in the raw store, and does nothing else.
Parsing is layer 3, so a change to the extraction patterns is a reparse of what
we already hold rather than a re-crawl of someone's site.

**Politeness is a design constraint, not a nicety.** `architecture.md` §11 is
blunt about it: a due diligence firm caught misbehaving is a due diligence firm
with a credibility problem, and integrity is the product. So: robots.txt is
honoured, a real User-Agent identifies us, one page at a time, and a delay
between requests to the same host.

Company websites are the company's own outbound marketing — a different risk
category from LinkedIn, and the one `architecture.md` §11 puts on the safe side
of the line. Nothing here logs in, bypasses anything, or touches personal
profiles.
"""
from __future__ import annotations

import time
import urllib.robotparser
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

import httpx

USER_AGENT = (
    "GleipnirBot/0.1 (corporate due-diligence research; "
    "respects robots.txt; contact: set GLEIPNIR_CONTACT)"
)

#: Paths worth trying on a Danish company site, in priority order. The CVR
#: number is legally required to appear somewhere, and in practice lives in a
#: footer or on the contact/about page.
CANDIDATE_PATHS = (
    "/", "/om-os", "/about", "/about-us", "/kontakt", "/contact",
    "/team", "/om", "/vores-team", "/people", "/ledelse", "/management",
)


class RobotsDisallowed(RuntimeError):
    """The site's robots.txt forbids this path. Not an error to route around."""


@dataclass(frozen=True)
class PageResponse:
    url: str
    body: bytes
    http_status: int
    content_type: str


class WebsiteClient:
    def __init__(self, *, delay: float = 1.0, timeout: float = 20.0,
                 max_bytes: int = 4_000_000) -> None:
        self._client = httpx.Client(
            headers={"User-Agent": USER_AGENT,
                     "Accept": "text/html,application/xhtml+xml"},
            timeout=timeout, follow_redirects=True,
        )
        self._delay = delay
        self._max_bytes = max_bytes
        self._robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}
        self._last_hit: dict[str, float] = {}

    def _robots_for(self, url: str):
        host = urlparse(url).netloc
        if host in self._robots:
            return self._robots[host]
        rp = urllib.robotparser.RobotFileParser()
        try:
            r = self._client.get(urljoin(url, "/robots.txt"))
            rp.parse(r.text.splitlines() if r.status_code == 200 else [])
        except httpx.HTTPError:
            # No robots.txt reachable — treat as permissive, which is the
            # standard interpretation, but record nothing that implies we read
            # one.
            rp.parse([])
        self._robots[host] = rp
        return rp

    def may_fetch(self, url: str) -> bool:
        rp = self._robots_for(url)
        return rp.can_fetch(USER_AGENT, url) if rp else True

    def get(self, url: str) -> PageResponse:
        if not self.may_fetch(url):
            raise RobotsDisallowed(url)
        host = urlparse(url).netloc
        elapsed = time.monotonic() - self._last_hit.get(host, 0.0)
        if elapsed < self._delay:
            time.sleep(self._delay - elapsed)
        resp = self._client.get(url)
        self._last_hit[host] = time.monotonic()
        body = resp.content[: self._max_bytes]
        return PageResponse(url=str(resp.url), body=body,
                            http_status=resp.status_code,
                            content_type=resp.headers.get("content-type", ""))

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "WebsiteClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def normalise_site(value: str) -> str:
    """CVR files websites as bare hostnames as often as URLs."""
    value = value.strip()
    if not value:
        return ""
    if not value.startswith(("http://", "https://")):
        value = "https://" + value.lstrip("/")
    return value
