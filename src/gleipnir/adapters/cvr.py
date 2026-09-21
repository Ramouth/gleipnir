"""Layer 1 — CVR source adapter (architecture.md §4).

Talks to Erhvervsstyrelsen's system-til-system distribution endpoint, which is a
plain Elasticsearch index:

    POST {base}/cvr-permanent/virksomhed/_search     companies
    POST {base}/cvr-permanent/deltager/_search       participants (persons + orgs)

HTTP Basic auth; `CVR_API_KEY` is the base64 of "user:pass".

Three decisions worth stating, because each is easy to get wrong later:

**We fetch the whole document, no `_source` filtering.** Field selection at fetch
time would be a small saving now and would defeat the raw store's whole purpose:
the first predicate that needs `attributter` (where *tegningsregel* lives) would
force a refetch of every entity already collected. Bytes are cheap; quota is not.

**The endpoint is Elasticsearch, so quota is per request, not per company.** A
`size: 1000` scan costs the same one call as a single lookup. That is what makes
the base-rate work in docs/predicates.md §5 affordable — batch the statistics,
spend single lookups only on live screens.

**This module knows nothing about claims, predicates, or the graph.** It returns
raw payloads and fetch metadata. Parsing lives in layer 3 as pure functions over
what the raw store already holds.
"""
from __future__ import annotations

import base64
import logging
from dataclasses import dataclass
from typing import Any, Iterator

import httpx

log = logging.getLogger(__name__)

COMPANY_INDEX = "cvr-permanent/virksomhed"
PARTICIPANT_INDEX = "cvr-permanent/deltager"


class CvrAuthError(RuntimeError):
    """Credentials rejected (401/403).

    Separated from every other failure because it is not resource-specific:
    every subsequent request fails identically, so a batch run should stop
    rather than mark ten thousand entities as 'not found'.
    """


class CvrRequestError(RuntimeError):
    """A non-auth upstream failure for one request."""


@dataclass(frozen=True)
class CvrResponse:
    """One raw Elasticsearch response, undigested."""

    body: bytes
    http_status: int
    index: str
    query: dict[str, Any]

    def hits(self) -> list[dict[str, Any]]:
        import json

        return json.loads(self.body).get("hits", {}).get("hits", [])


def normalise_cvr(value: str | int) -> str:
    """Digits only, zero-padded to 8. Raises on anything that cannot be one."""
    digits = "".join(c for c in str(value) if c.isdigit())
    if not digits or len(digits) > 8:
        raise ValueError(f"not a CVR number: {value!r}")
    return digits.zfill(8)


def valid_cvr_checksum(cvr: str) -> bool:
    """Danish CVR numbers carry a modulus-11 check digit.

    Validated at the adapter boundary because a checksum failure is almost
    always a *parsing* bug on our side — a column misread, a truncated
    spreadsheet cell — and catching it here is free (architecture.md §5).
    Note this checks form, never plausibility: the adapter rejects malformed
    input and admits everything else, per docs/predicates.md gate 1.
    """
    if len(cvr) != 8 or not cvr.isdigit():
        return False
    weights = (2, 7, 6, 5, 4, 3, 2, 1)
    return sum(int(d) * w for d, w in zip(cvr, weights)) % 11 == 0


class CvrClient:
    """Synchronous ES client. Async is not useful here — the binding constraint
    is a monthly request quota, not concurrency."""

    def __init__(self, api_key: str, base_url: str, timeout: float = 60.0) -> None:
        if not api_key:
            raise RuntimeError("CVR_API_KEY is not set")
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Basic {api_key}",
            },
            timeout=timeout,
        )

    @classmethod
    def from_userpass(cls, username: str, password: str, base_url: str) -> "CvrClient":
        token = base64.b64encode(f"{username}:{password}".encode()).decode()
        return cls(api_key=token, base_url=base_url)

    def search(self, index: str, query: dict[str, Any]) -> CvrResponse:
        """One request. This is the unit your quota is counted in."""
        try:
            resp = self._client.post(f"/{index}/_search", json=query)
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            if e.response.status_code in (401, 403):
                raise CvrAuthError(
                    f"CVR rejected our credentials (HTTP {e.response.status_code}). "
                    "Check CVR_API_KEY — base64 of user:pass for distribution.virk.dk."
                ) from e
            raise CvrRequestError(
                f"CVR returned HTTP {e.response.status_code} for {index}"
            ) from e
        except httpx.HTTPError as e:
            raise CvrRequestError(f"CVR request failed for {index}: {e}") from e
        return CvrResponse(
            body=resp.content, http_status=resp.status_code, index=index, query=query
        )

    def company(self, cvr: str | int) -> CvrResponse:
        """Full company document, history included.

        `deltagerRelation` carries participants with their roles *and validity
        periods*, which is where bitemporality comes from — poc.md §11 makes it
        non-negotiable, and it costs nothing extra because it is in the same
        document.
        """
        number = normalise_cvr(cvr)
        if not valid_cvr_checksum(number):
            raise ValueError(f"CVR checksum failed for {number} — likely a parsing bug")
        return self.search(
            COMPANY_INDEX,
            {"query": {"term": {"Vrvirksomhed.cvrNummer": int(number)}}, "size": 1},
        )

    def participant(self, enhedsnummer: int | str) -> CvrResponse:
        """One participant (person or organisation) and every relation it holds.

        This is the person -> companies direction, and it is what
        `nominee_density` and the Tier B recurrence predicates are computed
        from. It is also the expensive half of a screen (docs/predicates.md),
        so callers should expand a participant only when a predicate could
        actually change as a result.
        """
        return self.search(
            PARTICIPANT_INDEX,
            {
                "query": {"term": {"Vrdeltagerperson.enhedsNummer": int(enhedsnummer)}},
                "size": 1,
            },
        )

    def scan(
        self, index: str, query: dict[str, Any], page_size: int = 1000
    ) -> Iterator[list[dict[str, Any]]]:
        """Paginate a whole result set with `search_after`, yielding pages.

        One page is one request. This is how the base-rate work is affordable:
        the directorships-per-person distribution and the ownership-percentage
        bunching estimator both need corpus scale, and at 1000 records per call
        the whole register is ~1000 requests rather than ~1M.

        `search_after` rather than `from`/`size` because deep paging with
        `from` degrades badly and is capped server-side; and rather than
        `scroll` because a scroll context holds server resources for a client
        we do not control and cannot promise to close.
        """
        body = dict(query)
        body["size"] = page_size
        body.setdefault("sort", [{"_doc": "asc"}])
        after: list[Any] | None = None
        while True:
            page_body = dict(body)
            if after is not None:
                page_body["search_after"] = after
            hits = self.search(index, page_body).hits()
            if not hits:
                return
            yield hits
            last_sort = hits[-1].get("sort")
            if not last_sort:
                log.warning("scan stopped: response carried no sort values to page on")
                return
            after = last_sort

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "CvrClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
