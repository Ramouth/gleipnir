"""EPO Open Patent Services — filed activity, and the chain of title.

`sources.py` has carried this entry since the source survey: *OAuth2. 4GB/week
free. Inventor identity + INPADOC chain of title.* This is the adapter.

**What a patent register is good for here, and what it is not.**

It is not a detector. Patent-holding is rare among ordinary Danish companies, so
"this company has no filings" is the overwhelming default and is evidence of
nothing whatever. Any predicate that fired on the absence would be measuring the
population, which is the confounder `docs/loop-log.md` iteration 6 spent its
length on.

It is good for two things that no other connected source provides:

1. **Corroboration against a self-declared claim.** A site that says *our
   patented process* is making a checkable assertion. Filings, or their absence
   under that applicant name, settle it. That is a `narrative.py` gap with a
   stated expectation, not a predicate with a threshold.

2. **Chain of title.** INPADOC legal events record changes of applicant and
   proprietor, dated. That is an ownership transfer written down in a register
   that is not CVR, by parties under no Danish filing obligation — which is
   precisely the shape `docs/threat-model.md` cares about.

**Everything here is a name match.** OPS searches applicant and inventor names,
so a result is a candidate and never an identity, and the claims this produces
say so. `analyst.py` already records the specific failure: inventor search
cannot disambiguate a common Danish name.

**Auth and quota.** OAuth2 client credentials against `/auth/accesstoken`; the
token lives about twenty minutes and is refreshed once on a 401 rather than on a
timer, so a clock skew cannot strand the client. Every response carries
`X-Throttling-Control`, which names the colour of each service bucket; the
client reads it and refuses to keep hammering a bucket the server has marked
red. The free tier is 4GB/week, so `published-data/search` is preferred over
whole-document retrieval wherever a count or a name is all that is needed.
"""
from __future__ import annotations

import base64
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

log = logging.getLogger(__name__)

BASE = "https://ops.epo.org/3.2"
TOKEN_PATH = "/auth/accesstoken"
REST = "/rest-services"

#: OPS answers a search with at most 100 records, and pages by an inclusive
#: 1-based range. Anything above this is a client-side loop, not a bigger ask.
MAX_RANGE = 100


class OpsAuthError(RuntimeError):
    """Credentials rejected.

    Separate from every other failure for the same reason `CvrAuthError` is:
    it is not resource-specific, so a batch run must stop rather than record
    ten thousand entities as having no patents.
    """


class OpsRequestError(RuntimeError):
    """A non-auth upstream failure for one request."""


class OpsThrottled(OpsRequestError):
    """The server says this bucket is exhausted. Not a resource-level fact."""


@dataclass(frozen=True)
class OpsResponse:
    """One raw OPS response, undigested. The store keeps these bytes."""

    body: bytes
    http_status: int
    query: dict[str, Any]
    #: The server's own statement about how much of each bucket is left.
    throttling: str = ""


@dataclass
class Throttle:
    """`X-Throttling-Control`, parsed.

    Shape: ``busy (images=green:100, inpadoc=green:45, other=green:1000, ...)``
    — an overall state plus a colour and a per-minute allowance per service.
    Kept because a client that ignores it is one the EPO is entitled to block.
    """

    overall: str = ""
    services: dict[str, tuple[str, int]] = field(default_factory=dict)

    @classmethod
    def parse(cls, header: str) -> "Throttle":
        if not header:
            return cls()
        overall = header.split("(", 1)[0].strip()
        services = {}
        for name, colour, limit in re.findall(
                r"(\w+)\s*=\s*(green|yellow|red|black)\s*:\s*(\d+)", header):
            services[name] = (colour, int(limit))
        return cls(overall=overall, services=services)

    def colour(self, service: str) -> str:
        return self.services.get(service, ("green", 0))[0]

    def blocked(self, service: str) -> bool:
        """Black means the bucket is spent. Red is throttled but still moving,
        so it is a reason to slow down rather than to stop."""
        return self.colour(service) == "black"


class OpsClient:
    """Talks to OPS. Knows nothing about claims, predicates or the graph.

    Returns raw payloads and fetch metadata, exactly as `CvrClient` does — the
    caller writes them to the raw store and a pure function parses them later,
    so a parser fix is a reparse rather than a refetch against a weekly cap.
    """

    def __init__(self, key: str, secret: str, *, base_url: str = BASE,
                 timeout: float = 45.0, min_interval: float = 0.4) -> None:
        if not (key and secret):
            raise OpsAuthError("OPS needs a consumer key and secret")
        self._basic = base64.b64encode(f"{key}:{secret}".encode()).decode()
        self._c = httpx.Client(base_url=base_url, timeout=timeout,
                               follow_redirects=True)
        self._token: str | None = None
        self._min_interval = min_interval
        self._last_call = 0.0
        self.throttle = Throttle()

    # ── auth ────────────────────────────────────────────────────────────────
    def _authenticate(self) -> str:
        r = self._c.post(TOKEN_PATH,
                         headers={"Authorization": f"Basic {self._basic}",
                                  "Content-Type": "application/x-www-form-urlencoded"},
                         data={"grant_type": "client_credentials"})
        if r.status_code in (400, 401, 403):
            raise OpsAuthError(f"OPS rejected the consumer key ({r.status_code})")
        if r.status_code != 200:
            raise OpsRequestError(f"OPS token endpoint returned {r.status_code}")
        self._token = r.json()["access_token"]
        return self._token

    # ── one request ─────────────────────────────────────────────────────────
    def _get(self, path: str, params: dict[str, Any] | None = None, *,
             service: str = "other", accept: str = "application/json") -> OpsResponse:
        """One GET, with a single re-auth retry and the throttle respected.

        The retry is on 401 rather than on an expiry timer: the token's lifetime
        is short and a wrong local clock would otherwise strand the client with
        a token it believes is valid.
        """
        if self.throttle.blocked(service):
            raise OpsThrottled(f"OPS reports the {service!r} bucket exhausted")

        gap = self._min_interval - (time.monotonic() - self._last_call)
        if gap > 0:
            time.sleep(gap)

        for attempt in (1, 2):
            token = self._token or self._authenticate()
            r = self._c.get(f"{REST}{path}", params=params,
                            headers={"Authorization": f"Bearer {token}",
                                     "Accept": accept})
            self._last_call = time.monotonic()
            self.throttle = Throttle.parse(r.headers.get("X-Throttling-Control", ""))
            if r.status_code == 401 and attempt == 1:
                self._token = None            # expired mid-flight; get a new one
                continue
            break

        if r.status_code in (401, 403):
            raise OpsAuthError(f"OPS refused an authenticated request ({r.status_code})")
        if r.status_code == 403:
            raise OpsThrottled("OPS quota exceeded")
        # 404 is returned, not raised: "no patents under this name" is a fact
        # about the register and the caller must be able to store it.
        if r.status_code >= 500:
            raise OpsRequestError(f"OPS returned {r.status_code}")
        return OpsResponse(body=r.content, http_status=r.status_code,
                           query=dict(params or {}),
                           throttling=r.headers.get("X-Throttling-Control", ""))

    # ── the calls this project makes ────────────────────────────────────────
    def search_applicant(self, name: str, *, start: int = 1,
                         count: int = 25) -> OpsResponse:
        """Published applications naming `name` as applicant.

        A NAME match against the applicant field. Two companies can share a
        name across jurisdictions and a Danish ApS can share one with an
        unrelated foreign filer, so what comes back is a candidate set for a
        human or a later resolution step — never an identity.
        """
        end = min(start + max(count, 1) - 1, start + MAX_RANGE - 1)
        return self._get("/published-data/search",
                         params={"q": f'pa="{_quote(name)}"',
                                 "Range": f"{start}-{end}"},
                         service="other")

    def search_inventor(self, name: str, *, start: int = 1,
                        count: int = 25) -> OpsResponse:
        """Published applications naming `name` as inventor.

        Kept deliberately separate from the applicant search. An inventor is not
        an owner, and `analyst.py` records that this search cannot disambiguate
        a common Danish name — so it produces leads for a person to read, and
        nothing a predicate may threshold.
        """
        end = min(start + max(count, 1) - 1, start + MAX_RANGE - 1)
        return self._get("/published-data/search",
                         params={"q": f'in="{_quote(name)}"',
                                 "Range": f"{start}-{end}"},
                         service="other")

    def biblio(self, publication: str) -> OpsResponse:
        """Bibliographic data for one publication, by EPODOC number."""
        return self._get(
            f"/published-data/publication/epodoc/{_quote(publication)}/biblio",
            service="retrieval")

    def legal(self, publication: str) -> OpsResponse:
        """INPADOC legal events — the chain of title.

        This is the call that earns the source its place: assignments and
        proprietor changes, dated, in a register with no Danish filing
        obligation behind it.
        """
        return self._get(f"/legal/publication/epodoc/{_quote(publication)}",
                         service="inpadoc")

    def family(self, publication: str) -> OpsResponse:
        """INPADOC family — the same invention across jurisdictions."""
        return self._get(f"/family/publication/epodoc/{_quote(publication)}/biblio",
                         service="inpadoc")

    def close(self) -> None:
        self._c.close()

    def __enter__(self) -> "OpsClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def _quote(value: str) -> str:
    """Strip the characters that would break out of a CQL term.

    OPS's query language is not SQL and the risk is a malformed query rather
    than an injection, but a company name containing a double quote otherwise
    produces a 400 that looks like "this company has no patents".
    """
    return re.sub(r'["\\]', " ", value).strip()
