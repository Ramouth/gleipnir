"""Exculpatory evidence: does an unresolvable owner belong to a documented group?

A real case is why this exists. Every structural signal fired — chain
terminating at a Luxembourg S.à.r.l., that vehicle holding 100%, minimum share
capital against a website claiming 100 million patients across 30 countries —
on a private-equity acquisition vehicle for a mainstream generics business.

The dominant false-positive class in Denmark is not concealment. It is
**a sales subsidiary of a large legitimate group**: minimum capital, a handful of
employees, one foreign parent, and a website describing the group rather than
the entity. There are thousands, and no concealment detector distinguishes them
from the real thing.

So the useful thing to build is not a better detector. It is a **suppressor** —
positive evidence that the opaque-looking parent is a documented member of a
real group. `docs/predicates.md` §8 records that the research produced almost no
green predicates because every lane searched for concealment; this is one built
deliberately from the other direction.

**Why LEI evidence is worth something.** An LEI is not free and not permanent:
it is issued against verified reference data and must be **renewed annually**,
with a fee, or it lapses. A maintained LEI with reported Level 2 relationships
is therefore a costly, recurring, third-party-verified signal — the same logic
as patent renewals in `predicates.md` §9. A lapsed one is informative in the
other direction.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from gleipnir.adapters.gleif import GleifClient, LeiRecord

#: Legal-form suffixes and noise that stop an exact GLEIF name match. GLEIF
#: filters on the registered legal name, so "Bidco 7 (Luxembourg) Acquisition
#: S.à.r.l." must be tried both verbatim and stripped.
_SUFFIX = re.compile(
    r"\s*(?:,)?\s*(?:S\.?\s?[àa]\.?\s?r\.?\s?l\.?|S\.?A\.?R\.?L\.?|A/S|ApS|GmbH|"
    r"AB|AS|Oy|B\.?V\.?|N\.?V\.?|Ltd\.?|Limited|Inc\.?|LLC|PLC|S\.?A\.?|SE|"
    r"S\.?p\.?A\.?|Corp\.?|Corporation)\s*$", re.I)
_PARENS = re.compile(r"\s*\([^)]*\)\s*")


#: `ANDEN_DELTAGER` is not a synonym for "foreign". Measured against the cached
#: cohort it covers at least four different things, and one of them is
#: unambiguously benign and detectable by name alone:
#:
#:   estate       "Boet efter <name>" — a deceased Danish person's estate. It
#:                resolves to a named natural person and conceals nothing. It
#:                was being reported as an opaque owner beyond which ownership
#:                is "not determinable".
#:   partnership  I/S, K/S, P/S — Danish forms without their own CVR entry
#:   foreign      a company registered outside Denmark
#:   public       a municipality, region, or state body
#:
#: Classifying first is what stops the finding overclaiming.
_ESTATE = re.compile(r"^\s*(?:boet\s+efter|d\u00f8dsboet\s+efter|estate\s+of)\b", re.I)
_PARTNERSHIP = re.compile(r"\b(?:I/S|K/S|P/S|kommanditselskab|interessentskab)\b", re.I)
_PUBLIC = re.compile(
    r"\b(?:kommune|kommunen|region|regionen|staten|ministeriet|styrelsen|"
    r"universitet|municipality)\b", re.I)


class PartyKind(StrEnum):
    ESTATE = "estate"
    PARTNERSHIP = "partnership"
    PUBLIC = "public_body"
    OTHER = "other"


def classify_party(name: str) -> PartyKind:
    """What kind of non-company, non-person owner is this?"""
    if _ESTATE.search(name):
        return PartyKind.ESTATE
    if _PUBLIC.search(name):
        return PartyKind.PUBLIC
    if _PARTNERSHIP.search(name):
        return PartyKind.PARTNERSHIP
    return PartyKind.OTHER


class GroupEvidence(StrEnum):
    BENIGN_FORM = "benign_form"          # estate / public body — not opacity
    DOCUMENTED = "documented_group"      # LEI, active, with Level 2 relationships
    REGISTERED = "lei_only"              # LEI exists but no relationships reported
    LAPSED = "lei_lapsed"                # had an LEI, not maintained
    NONE = "no_lei"                      # nothing found — NOT evidence of anything
    NOT_CHECKED = "not_checked"


@dataclass(frozen=True)
class GroupFinding:
    evidence: GroupEvidence
    name: str
    lei: str | None = None
    country: str | None = None
    parent_name: str | None = None
    parent_country: str | None = None
    tried: tuple[str, ...] = ()

    @property
    def suppresses(self) -> bool:  # noqa: D401
        """Does this justify NOT treating the opaque owner as a finding?

        Only `DOCUMENTED`. A bare LEI with no group relationships says the entity
        exists and is verified; it does not say it sits inside a real corporate
        group, and a single-purpose vehicle can hold one.
        """
        return self.evidence in (GroupEvidence.DOCUMENTED, GroupEvidence.BENIGN_FORM)

    def line(self) -> str:
        bits = [f"{self.evidence:<18}{self.name[:44]:<46}"]
        if self.lei:
            bits.append(f"LEI {self.lei} ({self.country})")
        if self.parent_name:
            bits.append(f"-> {self.parent_name[:34]} ({self.parent_country})")
        if self.evidence is GroupEvidence.NONE:
            bits.append(f"tried {len(self.tried)} name form(s)")
        return "  ".join(bits)


def name_variants(name: str) -> list[str]:
    """Name forms worth trying against GLEIF, most specific first.

    A single verbatim lookup fails on most real names: GLEIF stores the
    registered legal name, and a Danish register stores whatever was typed.
    Stripping a legal-form suffix and a parenthetical is the cheap 80%.
    """
    out = [name.strip()]
    stripped = _SUFFIX.sub("", name).strip(" ,.")
    if stripped and stripped not in out:
        out.append(stripped)
    no_parens = _PARENS.sub(" ", name)
    no_parens = re.sub(r"\s+", " ", _SUFFIX.sub("", no_parens)).strip(" ,.")
    if no_parens and no_parens not in out:
        out.append(no_parens)
    return out


def assess(name: str, client: GleifClient, *, country: str | None = None) -> GroupFinding:
    """Look for group evidence behind an unresolvable owner name.

    A miss means **not determinable**, never "suspicious". GLEIF coverage skews
    to financial and larger entities, so most ordinary companies have no LEI and
    that fact carries no information at all.
    """
    kind = classify_party(name)
    if kind in (PartyKind.ESTATE, PartyKind.PUBLIC):
        # No lookup needed and none would help: an estate resolves to a named
        # person and a municipality is a public body. Neither is an opaque
        # terminus, and reporting either as one is simply wrong.
        return GroupFinding(GroupEvidence.BENIGN_FORM, name, tried=(str(kind),))
    tried = tuple(name_variants(name))
    best: LeiRecord | None = None
    for variant in tried:
        recs = client.by_name(variant, country=country)
        if recs:
            active = [r for r in recs if r.status.upper() == "ACTIVE"]
            best = (active or recs)[0]
            break
    if best is None:
        return GroupFinding(GroupEvidence.NONE, name, tried=tried)
    if best.status.upper() != "ACTIVE":
        return GroupFinding(GroupEvidence.LAPSED, name, lei=best.lei,
                            country=best.country, tried=tried)
    parent = client.parent(best.lei, "direct") or client.parent(best.lei, "ultimate")
    if parent is None:
        return GroupFinding(GroupEvidence.REGISTERED, name, lei=best.lei,
                            country=best.country, tried=tried)
    return GroupFinding(GroupEvidence.DOCUMENTED, name, lei=best.lei,
                        country=best.country, parent_name=parent.parent_name,
                        parent_country=parent.parent_country, tried=tried)
