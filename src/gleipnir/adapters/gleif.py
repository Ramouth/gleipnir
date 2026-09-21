"""GLEIF — authoritative cross-border ownership (architecture.md §3.2).

Where CVR stops, this sometimes continues. A Danish company's owner that is not
a CVR-registered entity arrives as `ANDEN_DELTAGER` with a name and nothing
else; if that name carries an LEI, GLEIF's Level 2 relationships give a
*pre-resolved, authoritative* parent edge — given, not inferred, and §6 tier 2
says trust it above our own matching.

Free, no credential, no rate limit published. Coverage skews to financial and
larger entities, so a miss is common and means nothing.
"""
from __future__ import annotations

from dataclasses import dataclass

import httpx

BASE = "https://api.gleif.org/api/v1"


@dataclass(frozen=True)
class LeiRecord:
    lei: str
    name: str
    country: str
    status: str
    legal_form: str | None = None


@dataclass(frozen=True)
class ParentEdge:
    child_lei: str
    parent_lei: str
    parent_name: str
    parent_country: str
    kind: str          # 'direct' | 'ultimate'


class GleifClient:
    def __init__(self, timeout: float = 30.0) -> None:
        self._c = httpx.Client(base_url=BASE, timeout=timeout,
                               headers={"Accept": "application/vnd.api+json"},
                               follow_redirects=True)

    def by_name(self, name: str, country: str | None = None) -> list[LeiRecord]:
        """Exact legal-name search. Candidates, not a match.

        GLEIF filters on the registered legal name, so this is closer to
        deterministic than a fuzzy person match — but two entities can share a
        legal name across jurisdictions, which is why `country` narrows it and
        why the caller still adjudicates.
        """
        params = {"filter[entity.legalName]": name, "page[size]": 10}
        if country:
            params["filter[entity.legalAddress.country]"] = country
        try:
            r = self._c.get("/lei-records", params=params)
            r.raise_for_status()
        except httpx.HTTPError:
            return []
        out = []
        for rec in r.json().get("data", []):
            a = rec.get("attributes", {})
            e = a.get("entity", {})
            out.append(LeiRecord(
                lei=a.get("lei", ""),
                name=(e.get("legalName") or {}).get("name", ""),
                country=(e.get("legalAddress") or {}).get("country", ""),
                status=e.get("status", ""),
                legal_form=((e.get("legalForm") or {}).get("id")),
            ))
        return out

    def parent(self, lei: str, kind: str = "direct") -> ParentEdge | None:
        """Level 2 parent. 404 means *no parent reported*, not an error —
        an entity at the top of its group legitimately has none."""
        try:
            r = self._c.get(f"/lei-records/{lei}/{kind}-parent")
            if r.status_code == 404:
                return None
            r.raise_for_status()
        except httpx.HTTPError:
            return None
        d = r.json().get("data")
        if not d:
            return None
        a = d.get("attributes", {})
        e = a.get("entity", {})
        return ParentEdge(child_lei=lei, parent_lei=a.get("lei", ""),
                          parent_name=(e.get("legalName") or {}).get("name", ""),
                          parent_country=(e.get("legalAddress") or {}).get("country", ""),
                          kind=kind)

    def close(self) -> None:
        self._c.close()

    def __enter__(self) -> "GleifClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
