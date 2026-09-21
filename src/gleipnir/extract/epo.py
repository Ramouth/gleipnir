"""EPO OPS payloads -> claims. Pure functions over what the store already holds.

Two payload kinds, two jobs.

**`published-data/search/biblio`** answers *does this applicant name have filed,
published, examined activity, and when*. One request returns the references, the
applicant and inventor names and the publication dates together — which matters
because the free tier is metered in bytes per week, and the alternative is one
retrieval per publication.

**`legal/publication/...`** answers *has the right changed hands*. INPADOC legal
events carry proprietor and applicant changes with dates, in a register with no
Danish filing obligation behind it.

**Every claim here is a name match, and says so.** OPS searches the applicant
field, so `NORDIC PHARMA A/S` in a result is a string that matched a string. The
subject of the claim is therefore keyed `name:` rather than by CVR number, and
carries `match="applicant_name"` — the same discipline the designation matcher
runs under, for the same reason: iteration 8 showed a bare name lookup returning
a different person of the same surname on the first attempt.

**Transfers are detected on the EPO's own description text, not on a code
table.** Each national office issues its own legal-event codes for a change of
proprietor — `RAP1` at the EPO, `BECN` in Belgium, `732E` in France — and a
hardcoded list silently misses whichever one nobody thought of. The
descriptions are supplied by the EPO alongside the codes and say what happened
in words, so the marker list is readable and its gaps are visible.
"""
from __future__ import annotations

import re
from datetime import date
from typing import Any, Iterator

from gleipnir.claims import Claim, EntityRef, EpistemicTier, Predicate

#: Phrases in an INPADOC legal-event description that mean the right moved.
#: Deliberately about ownership only: a change of *address* or of *representative*
#: is not a change of proprietor, and a licence is not a transfer.
TRANSFER_MARKERS: tuple[str, ...] = (
    "TRANSFER OF RIGHTS",
    "TRANSFER OF PROPERTY",
    "TRANSFER OF THE PATENT",
    "CHANGE OF HOLDER",
    "CHANGE OF OWNER",
    "CHANGE OF PROPRIETOR",
    "CHANGE OF APPLICANT",
    "ASSIGNMENT",
    "ASSIGNED",
)

#: Phrases that contain a marker above but are not a transfer of the right.
#: Kept explicit so the exclusion is reviewable rather than implicit in a regex.
TRANSFER_EXCLUSIONS: tuple[str, ...] = (
    "CHANGE OF HOLDER'S NAME",       # the holder renamed itself; nothing moved
    "CHANGE OF NAME OF THE OWNER",
)


def _s(node: Any) -> str:
    """OPS wraps every scalar as ``{"$": value}``. Unwrap, tolerantly."""
    if isinstance(node, dict):
        return str(node.get("$", "")).strip()
    return str(node or "").strip()


def _listed(node: Any) -> list[Any]:
    """OPS returns a bare object where a list has one member."""
    if node is None:
        return []
    return node if isinstance(node, list) else [node]


def _epo_date(value: str) -> date | None:
    """OPS dates are `YYYYMMDD`, sometimes `YYYY-MM-DD`."""
    v = re.sub(r"[^0-9]", "", value or "")
    if len(v) != 8:
        return None
    try:
        return date(int(v[:4]), int(v[4:6]), int(v[6:8]))
    except ValueError:
        return None


def _name_ref(name: str, kind: str = "company") -> EntityRef:
    return EntityRef(kind=kind, key=f"name:{name.casefold()}", label=name)


def _country_of(name: str) -> str:
    """EPO's epodoc applicant format appends `[DK]`. A jurisdiction, free."""
    m = re.search(r"\[([A-Z]{2})\]\s*$", name)
    return m.group(1) if m else ""


def _clean(name: str) -> str:
    return re.sub(r"\s*\[[A-Z]{2}\]\s*$", "", name).strip()


def _parties(bib: dict[str, Any], group: str, tag: str) -> list[str]:
    """Applicant or inventor names.

    The nesting is `parties > applicants > applicant`, plural then singular, and
    reading only the plural yields a dict rather than a party — which produced
    no names at all and a clean, empty, entirely wrong result.

    Each party is listed twice, once `epodoc` (normalised, upper-cased, with a
    `[DK]` country suffix) and once `original` (as filed). Both are returned:
    the original is what a reader recognises, the epodoc form carries the
    country, and `_clean` folds them back together.
    """
    container = (bib.get("parties") or {}).get(group) or {}
    out: list[str] = []
    for party in _listed(container.get(tag) if isinstance(container, dict) else container):
        if not isinstance(party, dict):
            continue
        name = _s((party.get(f"{tag}-name") or {}).get("name"))
        if name:
            out.append(name)
    return out


def _publication(bib: dict[str, Any]) -> tuple[str, date | None]:
    """The publication's epodoc number and date."""
    number, when = "", None
    for doc in _listed((bib.get("publication-reference") or {}).get("document-id")):
        d = _epo_date(_s(doc.get("date")))
        if doc.get("@document-id-type") == "epodoc":
            number = _s(doc.get("doc-number")) or number
            when = d or when
        elif not number:
            number = (_s(doc.get("country")) + _s(doc.get("doc-number"))
                      + _s(doc.get("kind")))
            when = when or d
    return number, when


def extract_applicant_search(payload: dict[str, Any], *, query: str, raw_ref: str,
                             observed_at: str | None = None) -> list[Claim]:
    """`published-data/search/biblio` -> holds-application and inventor claims.

    `query` is the name that was searched. It rides on every claim, because a
    claim whose subject is a name match is only interpretable alongside the
    string that produced it.
    """
    root = (payload or {}).get("ops:world-patent-data", {}).get("ops:biblio-search", {})
    total = root.get("@total-result-count")
    result = root.get("ops:search-result") or {}

    claims: list[Claim] = []
    for doc in _listed(result.get("exchange-documents")):
        ex = doc.get("exchange-document") or doc
        bib = ex.get("bibliographic-data") or {}
        number, published = _publication(bib)
        if not number:
            continue
        title = ""
        for t in _listed(bib.get("invention-title")):
            if _s(t):
                title = _s(t)
                break
        family = ex.get("@family-id") or doc.get("@family-id") or ""

        applicants = _parties(bib, "applicants", "applicant")
        inventors = _parties(bib, "inventors", "inventor")
        country = next((_country_of(a) for a in applicants if _country_of(a)), "")

        for name in {_clean(a) for a in applicants if _clean(a)}:
            claims.append(Claim(
                subject=_name_ref(name),
                predicate=Predicate.HOLDS_PATENT_APPLICATION,
                object=EntityRef(kind="other", key=f"patent:{number}", label=number),
                source_id="epo_ops",
                # The applicant on a publication is what the office recorded.
                epistemic_tier=EpistemicTier.REGISTERED,
                raw_ref=raw_ref,
                valid_from=published,
                observed_at=observed_at,
                qualifiers={
                    "query": query,
                    # Never an identity. A string matched a string.
                    "match": "applicant_name",
                    "publication": number,
                    "family_id": str(family),
                    "title": title,
                    "applicant_country": country,
                    "total_for_query": total,
                },
            ))

        for name in {_clean(i) for i in inventors if _clean(i)}:
            claims.append(Claim(
                subject=_name_ref(name, kind="person"),
                predicate=Predicate.NAMED_AS_INVENTOR,
                object=EntityRef(kind="other", key=f"patent:{number}", label=number),
                source_id="epo_ops",
                epistemic_tier=EpistemicTier.REGISTERED,
                raw_ref=raw_ref,
                valid_from=published,
                observed_at=observed_at,
                qualifiers={
                    "query": query,
                    # `analyst.py`: inventor search cannot disambiguate a common
                    # Danish name. This is a lead for a person, not a threshold.
                    "match": "inventor_name",
                    "publication": number,
                    "family_id": str(family),
                },
            ))
    return claims


def is_transfer(description: str) -> bool:
    """Does this legal-event description record the right changing hands?"""
    d = (description or "").upper()
    if any(x in d for x in TRANSFER_EXCLUSIONS):
        return False
    return any(m in d for m in TRANSFER_MARKERS)


def _legal_events(payload: dict[str, Any]) -> Iterator[tuple[str, dict[str, Any]]]:
    family = ((payload or {}).get("ops:world-patent-data", {})
              .get("ops:patent-family", {}))
    for member in _listed(family.get("ops:family-member")):
        number, _ = _publication(member)
        for event in _listed(member.get("ops:legal")):
            yield number, event


def _event_date(event: dict[str, Any]) -> date | None:
    """The gazette date, out of whichever `L0xx` slot the office used."""
    for key, node in event.items():
        if not key.startswith("ops:L"):
            continue
        if isinstance(node, dict) and "DATE" in str(node.get("@desc", "")).upper():
            when = _epo_date(_s(node))
            if when:
                return when
    return None


def extract_legal_events(payload: dict[str, Any], *, publication: str, raw_ref: str,
                         observed_at: str | None = None) -> list[Claim]:
    """`legal/publication/...` -> chain-of-title claims.

    Only transfers become claims. The rest of an INPADOC record is prosecution
    history — examination reports, fee payments, designations — and importing it
    would be storing a patent attorney's diary in an ownership graph.
    """
    claims: list[Claim] = []
    for number, event in _legal_events(payload):
        description = str(event.get("@desc") or "")
        if not is_transfer(description):
            continue
        code = str(event.get("@code") or "").strip()
        when = _event_date(event)
        target = number or publication
        claims.append(Claim(
            subject=EntityRef(kind="other", key=f"patent:{target}", label=target),
            predicate=Predicate.PATENT_RIGHTS_TRANSFERRED,
            object=description.title(),
            source_id="epo_ops",
            epistemic_tier=EpistemicTier.REGISTERED,
            raw_ref=raw_ref,
            valid_from=when,
            observed_at=observed_at,
            qualifiers={
                "publication": target,
                "legal_event_code": code,
                "description": description,
                "searched_publication": publication,
            },
        ))
    return claims
