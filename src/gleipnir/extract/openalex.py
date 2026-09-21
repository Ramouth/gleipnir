"""OpenAlex payloads -> claims. Affiliation and collaboration, dated.

Three claim kinds, and the reason each is shaped the way it is.

**`HAS_AFFILIATION`** — a person named an institution on a publication in a
given year. The institution's ROR id, country code and type ride on the claim as
qualifiers, which is what lets one query answer both halves of the question: an
`education` affiliation is a university connection and a `company` affiliation
is a corporate one, and they are the same claim.

**`CO_AUTHORED_WITH`** — two people on the same publication. Dated, and nothing
more. Co-authorship is not endorsement, association, direction or control, and a
predicate that treated it as any of those would be inventing a relationship the
source does not assert.

**`PEER_REVIEWED_FOR`** — self-reported review activity for a named journal or
organisation. Included with its coverage stated rather than left out: ORCID
records are self-selected and open-peer-review journals are a small minority, so
absence here is close to meaningless.

**Tier is `THIRD_PARTY`, and that is not a technicality.** The affiliation line
on a paper is authored by the researcher and published by a journal; OpenAlex
aggregates it. Nobody bears legal consequence for it being wrong. It is
therefore evidence a human weighs, never a registered fact, and
`finding.py` will not let it colour anything.

**Nothing here is an identity.** An author search matches a name; OpenAlex's own
author clustering is imperfect in exactly the way that hurts — a common name
collects other people's papers. Every claim carries `match="author_name"` or the
OpenAlex author id it was expanded from, so a reader can see which.
"""
from __future__ import annotations

from datetime import date
from typing import Any, Iterator

from gleipnir.claims import Claim, EntityRef, EpistemicTier, Predicate

#: Institution types OpenAlex distinguishes. Kept as a qualifier rather than
#: split into separate predicates: "which universities" and "which companies"
#: are the same question asked of one field.
INSTITUTION_TYPES = ("education", "company", "government", "facility",
                     "nonprofit", "healthcare", "archive", "other")


def _bare(value: str | None) -> str:
    return (value or "").rstrip("/").rsplit("/", 1)[-1]


def _person_ref(name: str, author_id: str | None = None) -> EntityRef:
    """Keyed on the OpenAlex author id where there is one, on the name where
    there is not — so a resolved person and a bare name are never confused."""
    if author_id:
        return EntityRef(kind="person", key=f"openalex:{_bare(author_id)}",
                         label=name)
    return EntityRef(kind="person", key=f"name:{name.casefold()}", label=name)


def _institution_ref(inst: dict[str, Any]) -> EntityRef:
    """ROR is the identifier; the OpenAlex id is the fallback."""
    ror = _bare(inst.get("ror"))
    key = f"ror:{ror}" if ror else f"openalex:{_bare(inst.get('id'))}"
    return EntityRef(kind="company", key=key, label=inst.get("display_name") or "")


def _institution_quals(inst: dict[str, Any]) -> dict[str, Any]:
    return {
        "institution_type": inst.get("type") or "",
        "country_code": inst.get("country_code") or "",
        "ror": _bare(inst.get("ror")),
        "openalex_institution": _bare(inst.get("id")),
    }


def _pub_date(work: dict[str, Any]) -> date | None:
    raw = work.get("publication_date") or ""
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        year = work.get("publication_year")
        return date(int(year), 1, 1) if year else None


def extract_author(payload: dict[str, Any], *, raw_ref: str,
                   observed_at: str | None = None,
                   query: str = "") -> list[Claim]:
    """An author record -> one dated affiliation claim per institution-year.

    OpenAlex gives `affiliations` as institution plus the list of years it was
    stated. One claim per year keeps the bitemporal model honest: an affiliation
    asserted in 2019 and again in 2024 is two statements, and a gap between them
    is a fact about the record rather than something to smooth over.
    """
    author = payload or {}
    if "results" in author:                      # a search response, not one author
        return [c for hit in (author.get("results") or [])
                for c in extract_author(hit, raw_ref=raw_ref,
                                        observed_at=observed_at, query=query)]

    name = author.get("display_name") or ""
    author_id = _bare(author.get("id"))
    if not name:
        return []
    orcid = _bare(author.get("orcid"))

    claims: list[Claim] = []
    for affiliation in author.get("affiliations") or []:
        inst = affiliation.get("institution") or {}
        if not inst.get("display_name"):
            continue
        for year in affiliation.get("years") or [None]:
            claims.append(Claim(
                subject=_person_ref(name, author_id),
                predicate=Predicate.HAS_AFFILIATION,
                object=_institution_ref(inst),
                source_id="openalex",
                epistemic_tier=EpistemicTier.THIRD_PARTY,
                raw_ref=raw_ref,
                valid_from=date(int(year), 1, 1) if year else None,
                valid_to=date(int(year), 12, 31) if year else None,
                observed_at=observed_at,
                qualifiers={
                    **_institution_quals(inst),
                    "orcid": orcid,
                    "works_count": author.get("works_count"),
                    "openalex_author": author_id,
                    # A name search produces candidates; an id expansion does not.
                    "match": "author_name" if query else "author_id",
                    "query": query,
                    "stated_on": "author record",
                },
            ))
    return claims


def _authorships(work: dict[str, Any]) -> Iterator[tuple[dict, str, str]]:
    for a in work.get("authorships") or []:
        author = a.get("author") or {}
        name = author.get("display_name") or ""
        if name:
            yield a, name, _bare(author.get("id"))


def extract_works(payload: dict[str, Any], *, subject_author_id: str,
                  raw_ref: str, observed_at: str | None = None) -> list[Claim]:
    """A works page -> affiliation claims and co-authorship claims.

    Affiliations taken from a *work* are stronger than those on an author
    record: the institution was stated on a specific dated publication, and a
    person listing two institutions on one paper is asserting both at once.
    That co-statement is the fact worth having — it is not inferred from two
    separate records that happen to overlap in time.
    """
    subject = _bare(subject_author_id)
    claims: list[Claim] = []

    for work in (payload or {}).get("results") or []:
        when = _pub_date(work)
        work_id = _bare(work.get("id"))
        title = (work.get("display_name") or "")[:200]
        doi = work.get("doi") or ""
        peer_reviewed = (work.get("type") == "article")

        subject_name = ""
        for authorship, name, author_id in _authorships(work):
            if author_id == subject:
                subject_name = name
                break

        for authorship, name, author_id in _authorships(work):
            institutions = authorship.get("institutions") or []
            is_subject = author_id == subject

            # Affiliations, for the screened person only. Recording every
            # co-author's affiliations too would turn one screen into a
            # standing file on several hundred uninvolved researchers, which
            # architecture.md §11 does not permit.
            if is_subject:
                countries = sorted({i.get("country_code") for i in institutions
                                    if i.get("country_code")})
                for inst in institutions:
                    if not inst.get("display_name"):
                        continue
                    claims.append(Claim(
                        subject=_person_ref(name, author_id),
                        predicate=Predicate.HAS_AFFILIATION,
                        object=_institution_ref(inst),
                        source_id="openalex",
                        epistemic_tier=EpistemicTier.THIRD_PARTY,
                        raw_ref=raw_ref,
                        valid_from=when,
                        observed_at=observed_at,
                        qualifiers={
                            **_institution_quals(inst),
                            "work": work_id,
                            "doi": doi,
                            "title": title,
                            "peer_reviewed": peer_reviewed,
                            "match": "author_id",
                            "openalex_author": author_id,
                            # The co-statement: every country this person named
                            # on THIS publication. A single-source fact, not an
                            # overlap inferred across two records.
                            "co_stated_countries": countries,
                            "stated_on": "publication",
                        },
                    ))
            else:
                claims.append(Claim(
                    subject=_person_ref(subject_name or subject, subject),
                    predicate=Predicate.CO_AUTHORED_WITH,
                    object=_person_ref(name, author_id),
                    source_id="openalex",
                    epistemic_tier=EpistemicTier.THIRD_PARTY,
                    raw_ref=raw_ref,
                    valid_from=when,
                    observed_at=observed_at,
                    qualifiers={
                        "work": work_id,
                        "doi": doi,
                        "title": title,
                        "peer_reviewed": peer_reviewed,
                        # The co-author's institutions are context for this
                        # edge, not a standing affiliation record about them.
                        "co_author_institutions": [
                            {"name": i.get("display_name"),
                             "country_code": i.get("country_code"),
                             "type": i.get("type"),
                             "ror": _bare(i.get("ror"))}
                            for i in institutions if i.get("display_name")],
                        # Co-authorship is collaboration. It is not
                        # endorsement, association, direction or control.
                        "asserts": "co-authorship only",
                    },
                ))
    return claims


def co_stated_countries(claims: list[Claim], person_key: str) -> dict[str, list]:
    """Which countries this person named *on the same publication*, by work.

    The screening question — did someone hold a Danish and a foreign
    institutional affiliation simultaneously — is answered here rather than by a
    predicate, because the answer is only interpretable beside the measured rate
    at which that happens at all.
    """
    out: dict[str, list] = {}
    for c in claims:
        if (c.predicate is not Predicate.HAS_AFFILIATION
                or c.subject.key != person_key):
            continue
        countries = c.qualifiers.get("co_stated_countries") or []
        if len(countries) > 1:
            out.setdefault(str(c.qualifiers.get("work") or ""), []).append(
                {"date": c.valid_from, "countries": countries,
                 "institution": c.object.label,
                 "doi": c.qualifiers.get("doi")})
    return out
