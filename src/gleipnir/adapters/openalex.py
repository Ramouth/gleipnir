"""OpenAlex — institutional affiliation and collaboration, for research security.

`sources.py` has carried this entry with a one-line brief since the source
survey: *affiliation strings; undisclosed dual affiliation, nationality-neutral.*
This is the adapter, and that brief is the specification.

**The question it answers.** A person doing work under a defence contract has
institutional connections that are matters of public record: where they said
they worked when they published, and who they published with. A visiting
appointment or a speaking invitation that turned into an affiliation line on a
paper is *in this data*, dated, with the institution's ROR identifier. That is a
need-to-know fact for a contracting authority, and it is one of the few things
in this project that joins at identifier strength rather than by name.

**The question it does not answer, and must never be used to answer.** Nothing
here records or infers a person's nationality, ethnicity, origin, politics or
loyalty, and no predicate built on it may derive one. The unit is an
*institution a person named on a publication*, plus that institution's
documented status under a cited authority. A researcher's origin is not a fact
this system holds an opinion about, and `investigation.py` already forbids the
model from forming one.

**The denominator, measured before the first line of this file.** OpenAlex holds
**11,290 works with both a Danish and a Chinese institution published since
2024-01-01**. Danish-Chinese academic collaboration is entirely ordinary. Any
predicate that reports a co-affiliation without that rate beside it would paint
several thousand ordinary researchers, which is the failure
`docs/loop-log.md` iteration 6 spent its length on. Every claim this module
emits is therefore a chain fact awaiting a comparator, and never a colour.

**Coverage is the honest limit.** `analyst.py` already records the specific
failure: OpenAlex returned a Norwegian mathematics-education researcher for a
common Danish name, and *industrial engineers do not publish*. Most company
officers have no academic footprint at all, so an empty result is the expected
default and means nothing. It is reported as a coverage statement, never as a
clean check.

Free, no credential. The polite pool wants a contact address in the User-Agent
and gives faster, more reliable service in exchange; `mailto` is sent for that
reason and no other.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Iterator

import httpx

log = logging.getLogger(__name__)

BASE = "https://api.openalex.org"

#: OpenAlex caps a page at 200 records.
MAX_PER_PAGE = 200

#: Peer-reviewed journal articles only. A preprint, a dataset and an erratum are
#: all `works`, and treating them alike would inflate a collaboration count with
#: things that were never reviewed.
PEER_REVIEWED = "type:article,primary_location.source.type:journal"


class OpenAlexError(RuntimeError):
    """An upstream failure for one request."""


@dataclass(frozen=True)
class OpenAlexResponse:
    """One raw response, undigested. The store keeps these bytes."""

    body: bytes
    http_status: int
    query: dict[str, Any]


class OpenAlexClient:
    """Talks to OpenAlex. Knows nothing about claims, predicates or the graph."""

    def __init__(self, mailto: str = "", *, base_url: str = BASE,
                 timeout: float = 60.0) -> None:
        agent = "GleipnirBot/0.1 (+research security screening"
        agent += f"; mailto:{mailto})" if mailto else ")"
        self._c = httpx.Client(base_url=base_url, timeout=timeout,
                               follow_redirects=True,
                               headers={"User-Agent": agent,
                                        "Accept": "application/json"})
        self._mailto = mailto

    def _get(self, path: str, params: dict[str, Any]) -> OpenAlexResponse:
        if self._mailto:
            params = {**params, "mailto": self._mailto}
        r = self._c.get(path, params=params)
        # 404 is returned rather than raised: "no such author" is a fact about
        # the source and the caller has to be able to store it.
        if r.status_code >= 500:
            raise OpenAlexError(f"OpenAlex returned {r.status_code}")
        return OpenAlexResponse(body=r.content, http_status=r.status_code,
                                query=dict(params))

    # ── discovery ───────────────────────────────────────────────────────────
    def search_author(self, name: str, *, per_page: int = 10) -> OpenAlexResponse:
        """Authors matching a name. **Candidates, never an identity.**

        OpenAlex clusters authorships into author records and the clustering is
        imperfect in exactly the way that matters here: a common name collects
        other people's papers. The caller gets `works_count` and the affiliation
        list so a human can adjudicate, and nothing downstream may treat a hit
        as resolved.
        """
        return self._get("/authors", {"search": name,
                                      "per-page": min(per_page, MAX_PER_PAGE)})

    def author(self, author_id: str) -> OpenAlexResponse:
        """One author record by OpenAlex id — dated affiliations, ORCID if any."""
        return self._get(f"/authors/{_bare(author_id)}", {})

    def works_by_author(self, author_id: str, *, per_page: int = MAX_PER_PAGE,
                        page: int = 1, peer_reviewed_only: bool = True
                        ) -> OpenAlexResponse:
        """One author's works — the co-authorship and affiliation source.

        Every institution on every authorship comes back on this one call, which
        is why collaboration and affiliation are the same request rather than
        two.
        """
        f = f"authorships.author.id:{_bare(author_id)}"
        if peer_reviewed_only:
            f += f",{PEER_REVIEWED}"
        return self._get("/works", {"filter": f, "page": page,
                                    "per-page": min(per_page, MAX_PER_PAGE)})

    def institution(self, ror_or_id: str) -> OpenAlexResponse:
        """One institution — country, type, and its ROR identifier."""
        return self._get(f"/institutions/{_bare(ror_or_id)}", {})

    def co_affiliation_count(self, country_a: str, country_b: str,
                             since: str) -> OpenAlexResponse:
        """How many works carry an institution from both countries.

        This is the denominator call. It exists so a co-affiliation fact can be
        reported beside the rate at which co-affiliation happens at all, rather
        than as a bare and frightening-sounding number.
        """
        return self._get("/works", {
            "filter": (f"institutions.country_code:{country_a.lower()},"
                       f"institutions.country_code:{country_b.lower()},"
                       f"from_publication_date:{since}"),
            "per-page": 1})

    def close(self) -> None:
        self._c.close()

    def __enter__(self) -> "OpenAlexClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def _bare(value: str) -> str:
    """`https://openalex.org/<openalex-author-id>` -> `<openalex-author-id>`; ROR URLs likewise."""
    return (value or "").rstrip("/").rsplit("/", 1)[-1]


def paged(client: OpenAlexClient, author_id: str, *, pages: int = 3
          ) -> Iterator[OpenAlexResponse]:
    """Successive pages of one author's works, stopping when a page is short.

    Bounded by `pages` rather than by a total, because an unbounded walk over a
    prolific author is how a polite client stops being one.
    """
    for page in range(1, pages + 1):
        resp = client.works_by_author(author_id, page=page)
        yield resp
        if resp.http_status != 200:
            return
        import json
        try:
            body = json.loads(resp.body)
        except json.JSONDecodeError:
            return
        if len(body.get("results") or []) < MAX_PER_PAGE:
            return
