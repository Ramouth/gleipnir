"""Institutional status: what an authority designated, and what an analyst assessed.

A CV entry or an OpenAlex affiliation names an institution. This says what is on
record about that institution — and keeps two very different kinds of record
apart, because collapsing them is how a think-tank's risk rating turns into a
legal finding about a named person.

**Designations.** A government act, with a citation. Already in the raw store:
the stored OpenSanctions extract carries the US BIS Entity List (`US-BIS-EL`)
and the NDAA §1286 list (`US-MCCAIN-1286`), and all eight of the *Seven Sons of
National Defence* plus the National University of Defence Technology are in it,
by name and alias. **Nothing is hardcoded here** — a designation list frozen
into source code is a designation list that is wrong within the quarter. The
matcher reads whatever list the store holds, and the store records when it was
fetched.

A designation is a `RedGround.DESIGNATION`, so it is the one kind of institutional
status that may reach a `Finding` at all.

**Assessments.** A researcher's published judgement — ASPI's China Defence
Universities Tracker being the substantial one. Well-sourced, far better
coverage than any designation list, and *not a legal act*. It is `THIRD_PARTY`
tier, it may never colour anything, and `test_institutions.py` asserts that over
the whole table rather than trusting the call sites.

**The matching is by name, so a hit is a candidate.** `docs/jurisdictions.md`
measured what name matching does to people: Ivanov, Petrov, Kuznetsov. Institution
names are far less collision-prone than personal names — there is one Beihang
University — but the discipline does not change: a hit here is
`unadjudicated_name_match` until somebody resolves it, exactly as in `screen.py`.

**And the denominator still applies.** An affiliation with a listed institution
is a chain fact carrying a rate, not a verdict about a person. A Danish
researcher who co-authored with Harbin Institute of Technology in 2016, when it
was not listed, did nothing that any authority had prohibited — which is why
every designation here carries the date it took effect and why an affiliation is
compared against it *as of* the affiliation's own date.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from gleipnir.finding import RedGround

#: Programme code -> the authority behind it and how to cite it. A programme
#: with no entry here is reported by its code rather than dressed in an
#: authority it may not have.
AUTHORITIES: dict[str, tuple[str, str]] = {
    "US-BIS-EL": (
        "US Department of Commerce, Bureau of Industry and Security — Entity List",
        "15 CFR Part 744, Supplement No. 4"),
    "US-MCCAIN-1286": (
        "US Department of Defense — NDAA FY2021 §1286 list of foreign entities "
        "linked to military modernisation",
        "Pub. L. 116-283 §1286"),
    "US-SDN": ("US Treasury OFAC — Specially Designated Nationals",
               "31 CFR Chapter V"),
    "EU-UKR": ("Council of the European Union — restrictive measures, Ukraine",
               "Council Regulation (EU) No 269/2014"),
}


class Rating(str):
    """ASPI's published band. A string subclass so it prints as itself."""


@dataclass(frozen=True)
class Designation:
    """An institution named by a government act. May ground a finding."""

    institution: str
    matched_name: str
    programmes: tuple[str, ...]
    countries: tuple[str, ...]
    datasets: tuple[str, ...]
    #: When the stored list last recorded a change for this target. Not the
    #: designation date itself — the extract does not carry one — so it bounds
    #: rather than states when the listing existed.
    last_change: str = ""
    ground: RedGround = RedGround.DESIGNATION

    def authorities(self) -> list[tuple[str, str]]:
        """(authority, citation) per programme. Uncited programmes are kept as
        their bare code rather than given an authority they may not have."""
        out = []
        for programme in self.programmes:
            if programme in AUTHORITIES:
                out.append(AUTHORITIES[programme])
            else:
                out.append((f"programme {programme}", ""))
        return out

    def line(self) -> str:
        authority = self.authorities()[0] if self.programmes else ("", "")
        return (f"{self.institution} — designated as {self.matched_name!r}\n"
                f"    {authority[0]}"
                + (f" · {authority[1]}" if authority[1] else "")
                + f"\n    programmes: {', '.join(self.programmes) or 'none stated'}"
                  f"   unadjudicated_name_match")


@dataclass(frozen=True)
class Assessment:
    """A published researcher's judgement. May never colour anything."""

    institution: str
    rating: str
    authority: str
    citation: str
    #: When this table was compiled from the source. An assessment with a stale
    #: retrieval date is a statement about what somebody thought then.
    retrieved_on: date
    note: str = ""
    #: Enforced, not documented. There is no code path from here to a Finding.
    may_colour: bool = False

    def line(self) -> str:
        return (f"{self.institution} — {self.rating}\n"
                f"    {self.authority} · {self.citation} (read {self.retrieved_on})\n"
                f"    an assessment, not a designation: context for a human, "
                f"never a colour")


#: ASPI's China Defence Universities Tracker, seeded with the institutions whose
#: classification is least ambiguous: the *Seven Sons of National Defence*
#: (国防七子), subordinate to MIIT, plus the PLA's own university.
#:
#: **This table is deliberately partial.** The tracker covers well over a
#: hundred institutions and this is nine of them. It is a seed so the mechanism
#: is real and testable, not a substitute for reading the source — `coverage()`
#: reports how partial it is rather than letting a miss read as a clean result.
ASPI_TRACKER = ("Australian Strategic Policy Institute — China Defence "
                "Universities Tracker")
ASPI_URL = "https://unitracker.aspi.org.au/"
ASPI_READ = date(2026, 8, 28)

ASSESSMENTS: dict[str, Assessment] = {a.institution.casefold(): a for a in (
    Assessment("Beihang University", "very high risk", ASPI_TRACKER, ASPI_URL,
               ASPI_READ, "one of the Seven Sons of National Defence"),
    Assessment("Beijing Institute of Technology", "very high risk", ASPI_TRACKER,
               ASPI_URL, ASPI_READ, "one of the Seven Sons of National Defence"),
    Assessment("Harbin Institute of Technology", "very high risk", ASPI_TRACKER,
               ASPI_URL, ASPI_READ, "one of the Seven Sons of National Defence"),
    Assessment("Harbin Engineering University", "very high risk", ASPI_TRACKER,
               ASPI_URL, ASPI_READ, "one of the Seven Sons of National Defence"),
    Assessment("Northwestern Polytechnical University", "very high risk",
               ASPI_TRACKER, ASPI_URL, ASPI_READ,
               "one of the Seven Sons of National Defence"),
    Assessment("Nanjing University of Aeronautics and Astronautics",
               "very high risk", ASPI_TRACKER, ASPI_URL, ASPI_READ,
               "one of the Seven Sons of National Defence"),
    Assessment("Nanjing University of Science and Technology", "very high risk",
               ASPI_TRACKER, ASPI_URL, ASPI_READ,
               "one of the Seven Sons of National Defence"),
    Assessment("National University of Defense Technology", "very high risk",
               ASPI_TRACKER, ASPI_URL, ASPI_READ,
               "the PLA's own university, subordinate to the Central Military "
               "Commission"),
)}


@dataclass(frozen=True)
class Status:
    """Everything on record about one institution, kept in its own categories."""

    institution: str
    designation: Designation | None = None
    assessment: Assessment | None = None

    @property
    def may_ground_a_finding(self) -> bool:
        """Only a designation can. An assessment never does, whatever it says."""
        return self.designation is not None

    def lines(self) -> list[str]:
        out = []
        if self.designation:
            out.append(self.designation.line())
        if self.assessment:
            out.append(self.assessment.line())
        if not out:
            out.append(f"{self.institution} — no designation and no assessment "
                       f"on file. Not a clean result: this table is partial "
                       f"({len(ASSESSMENTS)} institutions assessed).")
        return out


def designation_for(name: str, index: Any) -> Designation | None:
    """Match one institution against the designation list in the raw store.

    `index` is a `SanctionsIndex`; passing `None` returns `None`, which the
    caller must report as *no list was consulted* rather than as *not listed*.
    """
    if index is None or not name:
        return None
    hits = index.by_name(name)
    if not hits:
        return None
    target = hits[0] if isinstance(hits, (list, tuple)) else hits
    return Designation(
        institution=name,
        matched_name=getattr(target, "name", name),
        programmes=tuple(getattr(target, "programs", ()) or ()),
        countries=tuple(getattr(target, "countries", ()) or ()),
        datasets=tuple(getattr(target, "datasets", ()) or ()),
        last_change=str(getattr(target, "last_change", "") or ""),
    )


def assessment_for(name: str) -> Assessment | None:
    return ASSESSMENTS.get((name or "").casefold())


def status(name: str, index: Any = None) -> Status:
    """Both kinds of record about one institution, never merged."""
    return Status(institution=name,
                  designation=designation_for(name, index),
                  assessment=assessment_for(name))


def coverage() -> str:
    """How partial the assessment table is. Printed wherever it is used, so a
    miss reads as a coverage limit rather than as a clean result."""
    return (f"{len(ASSESSMENTS)} institutions assessed, from {ASPI_TRACKER}, "
            f"read {ASPI_READ}. The source covers many more; a name absent here "
            f"has not been checked against it.")
