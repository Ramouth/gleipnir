"""The CV — every dated position one person held, across every source.

`chain.py` projects ownership outward from a company. This projects *positions*
outward from a person, and it is the same kind of object: a pure function of the
claim graph, rebuildable, storing nothing of its own.

    company --has_role--> person          CVR directorships and management
    person  --has_affiliation--> institution   OpenAlex, universities and companies
    person  --holds_patent_application--> patent   EPO applicants
    person  --has_role--> company          an analyst's reading of a profile or CV

CVR files the role edge company-to-person, so a person's CV is the **reverse
index** `architecture-v2.md` §1.1 named as the missing piece. Building it here
rather than in a query keeps the direction in one place.

**Why this exists: the expansion rule.** A CV that names only Danish entities is
answerable from CVR. A CV that names an Estonian employer is not — and that
foreign entry is what licenses the next fetch, against that jurisdiction's own
register. The trigger is a fact somebody filed, not a hunch, which is what keeps
`plan.py`'s agenda derived rather than judged.

**Three rules, because this is a file about a named human being.**

1. **Country is read, never inferred.** A source that does not state where an
   entity sits yields `country=""`, and an entry with no country is a *question*
   — neither foreign nor domestic. Guessing a jurisdiction from a company-name
   suffix is how a Danish ApS with a Latvian-sounding name becomes a finding.

2. **A foreign entry is an agenda item and never a colour.** Working for an
   Estonian company is entirely ordinary; `docs/jurisdictions.md` measured the
   population it sits in. What a foreign entry buys is the right to look that
   entity up, and nothing else.

3. **Every entry keeps its tier and its evidence.** A directorship registered at
   Erhvervsstyrelsen and a role read off a profile by an analyst are both
   entries, and they are never the same kind of statement. The tier travels so
   `contradict.py` can pair them against each other rather than merge them.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Iterable

from gleipnir.claims import Claim, EntityRef, EpistemicTier, Predicate

#: The home jurisdiction. An entry here is answerable from sources already
#: connected; anything else is what this module exists to notice.
HOME = "DK"

#: Which predicate contributes a position, and what to call the capacity when
#: the claim does not name one. Closed: a predicate that is not a position does
#: not belong in a CV, and adding one is a deliberate act.
POSITION_PREDICATES: dict[Predicate, str] = {
    Predicate.HAS_ROLE: "role",
    Predicate.HAS_AFFILIATION: "affiliation",
    Predicate.HOLDS_PATENT_APPLICATION: "patent applicant",
    Predicate.NAMED_AS_INVENTOR: "inventor",
    Predicate.OWNS: "shareholding",
    Predicate.MEMBER_OF_GROUP: "stated group membership",
}

#: Where to go for a company registered in a given jurisdiction. A country with
#: no entry is a coverage statement — we know the entry is foreign and we have
#: nowhere to resolve it — which is reported rather than silently dropped.
REGISTER_FOR: dict[str, str] = {
    "DK": "cvr",
    "EE": "ee_ariregister",
    "GR": "gr_gemi",
    "NO": "brreg",
    "FI": "prh",
}


@dataclass(frozen=True)
class Entry:
    """One dated position: this person, at this entity, in this capacity."""

    entity: EntityRef
    capacity: str
    valid_from: date | None
    valid_to: date | None
    source_id: str
    tier: EpistemicTier
    evidence_ref: str
    #: ISO-3166 alpha-2, uppercase, **as stated by a source**. Empty means no
    #: source said, which is a different fact from "Danish".
    country: str = ""
    #: The source's own words, where it has any. Carried for the same reason
    #: `oracle.py` carries one: a statement a reader cannot check is not one.
    quote: str = ""
    entity_type: str = ""

    @property
    def country_known(self) -> bool:
        return bool(self.country)

    def covers(self, on: date) -> bool:
        return ((self.valid_from is None or self.valid_from <= on)
                and (self.valid_to is None or self.valid_to >= on))

    def line(self) -> str:
        period = f"{self.valid_from or '…'} → {self.valid_to or 'open'}"
        where = self.country or "??"
        return (f"{period:<26}{where:<4}{self.capacity:<22}"
                f"{self.entity.label or self.entity.key}  [{self.source_id}/{self.tier}]")


@dataclass(frozen=True)
class Expansion:
    """A foreign entry, and where it could be resolved.

    Not a finding, not a risk, not a score. It is the derivation of one fetch:
    *this person's record names this entity in that country; that country's
    register is this one; nothing has asked it yet.*
    """

    entry: Entry
    country: str
    source: str
    because: str

    @property
    def resolvable(self) -> bool:
        """Do we know of a register that covers it? A `False` here is a
        coverage statement, and it must be reported rather than dropped."""
        return bool(self.source)


@dataclass
class CV:
    """Every position one person holds or held, from every source consulted."""

    person: EntityRef
    entries: tuple[Entry, ...] = ()
    #: Sources actually asked. An empty CV from one source and an empty CV from
    #: five are different statements, and only the second is close to evidence.
    consulted: tuple[str, ...] = ()

    def at(self, on: date) -> list[Entry]:
        return [e for e in self.entries if e.covers(on)]

    def jurisdictions(self) -> set[str]:
        """Countries any source actually stated. Never includes a guess."""
        return {e.country for e in self.entries if e.country_known}

    def unknown_country(self) -> list[Entry]:
        """Entries no source placed anywhere. These are questions, not risks."""
        return [e for e in self.entries if not e.country_known]

    def foreign(self, home: str = HOME) -> list[Entry]:
        return [e for e in self.entries
                if e.country_known and e.country != home]

    def line(self) -> str:
        return f"{self.person.label or self.person.key}  —  {len(self.entries)} entries"


#: Sources whose scope *is* a jurisdiction. An entity recorded in the Danish
#: business register is in Denmark because that is what the register is; reading
#: the country off the source is not an inference about the entity, it is the
#: source's own coverage. A source covering many jurisdictions is absent here
#: and must state the country per record.
SOURCE_JURISDICTION: dict[str, str] = {
    "cvr": "DK",
    "regnskaber": "DK",
    "dkhm": "DK",
    "ee_ariregister": "EE",
    "brreg": "NO",
    "prh": "FI",
    "gr_gemi": "GR",
}


def _country_of(claim: Claim) -> str:
    """The country a source stated for the claim's object, or empty.

    Reads only what a source actually asserts: a per-record country field, or
    the single jurisdiction a national register covers by definition. There is
    deliberately no fallback inferring a jurisdiction from a name, a legal-form
    suffix or an address string — that is how a Danish ApS with a foreign
    sounding name becomes a foreign entry, and a foreign entry costs a fetch.
    """
    for key in ("country_code", "applicant_country", "jurisdiction", "country"):
        value = claim.qualifiers.get(key)
        if value:
            return str(value).upper()[:2]
    return SOURCE_JURISDICTION.get(claim.source_id, "")


def _object_ref(claim: Claim) -> EntityRef | None:
    obj = claim.object
    if isinstance(obj, EntityRef):
        return obj
    if obj is None or obj == "":
        return None
    return EntityRef(kind="other", key=f"value:{str(obj).casefold()}", label=str(obj))


def build(claims: Iterable[Claim], person: EntityRef, *,
          consulted: Iterable[str] = ()) -> CV:
    """Project the claim graph into one person's CV.

    Reads both directions, because the sources disagree about which way a
    position points: CVR files `company --has_role--> person`, while an
    affiliation is `person --has_affiliation--> institution`. A CV that read
    only one direction would be empty for whichever half of the graph it did
    not expect.
    """
    entries: list[Entry] = []
    for claim in claims:
        capacity = POSITION_PREDICATES.get(claim.predicate)
        if capacity is None:
            continue

        obj = _object_ref(claim)
        if isinstance(claim.object, EntityRef) and claim.object.key == person.key:
            # company --has_role--> person: the entity is the *subject*.
            entity = claim.subject
        elif claim.subject.key == person.key and obj is not None:
            entity = obj
        else:
            continue

        entries.append(Entry(
            entity=entity,
            capacity=str(claim.qualifiers.get("role") or capacity),
            valid_from=claim.valid_from,
            valid_to=claim.valid_to,
            source_id=claim.source_id,
            tier=claim.epistemic_tier,
            evidence_ref=claim.raw_ref,
            country=_country_of(claim),
            quote=str(claim.qualifiers.get("quote") or ""),
            entity_type=str(claim.qualifiers.get("institution_type") or ""),
        ))

    entries.sort(key=lambda e: (e.valid_from or date.min, e.entity.key, e.capacity))
    return CV(person=person, entries=tuple(entries),
              consulted=tuple(sorted(set(consulted) or {e.source_id for e in entries})))


def expansions(cv: CV, *, home: str = HOME,
               already_resolved: Iterable[str] = ()) -> list[Expansion]:
    """The foreign entries worth resolving, and where to resolve each.

    This is the whole expansion rule: *the CV names an entity outside the home
    jurisdiction, that jurisdiction has a register, and nobody has asked it.*
    Deduplicated by entity, because one person holding a role at the same
    Estonian company across four years is one lookup and not four.
    """
    done = set(already_resolved)
    seen: set[str] = set()
    out: list[Expansion] = []
    for entry in cv.foreign(home):
        if entry.entity.key in seen or entry.entity.key in done:
            continue
        seen.add(entry.entity.key)
        source = REGISTER_FOR.get(entry.country, "")
        label = entry.entity.label or entry.entity.key
        because = (f"{cv.person.label or cv.person.key} is recorded at {label!r} "
                   f"in {entry.country} ({entry.capacity}, "
                   f"{entry.valid_from or 'undated'}); "
                   + (f"{entry.country}'s register is {source}"
                      if source else
                      f"no connected register covers {entry.country}"))
        out.append(Expansion(entry=entry, country=entry.country,
                             source=source, because=because))
    return out


def render(cv: CV, *, home: str = HOME) -> str:
    """The CV as text. Facts, in date order, with the source on every line."""
    out = [cv.line(), ""]
    if not cv.entries:
        out.append("  no position on file from any source consulted "
                   f"({', '.join(cv.consulted) or 'none'})")
        out.append("  — a coverage statement, not a clean result")
        return "\n".join(out)

    out.append("  POSITIONS, AS FILED OR STATED")
    for entry in cv.entries:
        out.append("    " + entry.line())

    unknown = cv.unknown_country()
    if unknown:
        out.append(f"\n  NO SOURCE PLACED THESE ANYWHERE  ({len(unknown)})")
        out.append("    neither foreign nor domestic — a question for a source")
        for entry in unknown:
            out.append(f"    {entry.entity.label or entry.entity.key}  [{entry.source_id}]")

    todo = expansions(cv, home=home)
    if todo:
        out.append(f"\n  EXPANDS OUTSIDE {home}  ({len(todo)})")
        for e in todo:
            mark = "  " if e.resolvable else "! "
            out.append(f"    {mark}{e.because}")
        if any(not e.resolvable for e in todo):
            out.append("    ! = named abroad, and no connected register covers it")
    return "\n".join(out)
