"""Name continuity between a subsidiary and its owner.

A Danish entity called `XR-Turbo International ApS` owned by
`Eastport XR-TURBO Corp.` is *announcing* the relationship. That is transparency,
and it is one of the very few pieces of positive evidence available from
registry data alone — `docs/predicates.md` §8 records that the research produced
almost no green predicates, because all six lanes searched for concealment.

It is the mirror of a red predicate already in the set: the Iran lane rated
`trade_name_near_identical_across_border` very-high-precision when a *successor*
entity mirrors a *designated* entity's name. Same measurement, opposite sign,
depending on what sits at the other end.

**Its FALSE is not amber, and that distinction is the whole design.** Measured
against real cases: `Pharmaco Denmark ApS` owned by `Bidco 7 (Luxembourg)
Acquisition S.à.r.l.` shares no tokens at all — because a private-equity
acquisition vehicle is deliberately named for the deal, not the business. PE
structures produce FALSE systematically, and they are the dominant
false-positive class in Denmark. So a mismatch is *absence of evidence*, never
evidence of concealment.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

#: Legal forms, and geographic or functional qualifiers a subsidiary routinely
#: carries that its parent does not. Stripping them is what lets
#: "X Denmark ApS" match "X Corp".
_NOISE = {
    # legal forms
    "aps", "as", "a/s", "ab", "oy", "oyj", "gmbh", "ag", "ltd", "limited", "inc",
    "llc", "bv", "nv", "plc", "sa", "sarl", "srl", "spa", "se", "kg", "kft",
    "corp", "corporation", "company", "co", "holding", "holdings", "group",
    "gruppen", "invest", "koncern",
    # geography and scope
    "denmark", "danmark", "dansk", "danske", "nordic", "nordics", "norden",
    "scandinavia", "scandinavian", "skandinavien", "europe", "european", "eu",
    "international", "intl", "global", "worldwide", "north", "south", "east",
    "west", "nord", "syd",
    # generic descriptors
    "the", "and", "og", "of", "for", "systems", "solutions", "services",
    "technologies", "technology", "tech", "industries", "industri", "trading",
    "consulting", "partners", "ventures", "capital",
}


#: Danish letters do not decompose under NFKD — they are distinct letters, not
#: base+diacritic — so an ASCII-only split silently ate them: "Ærø" became "r".
#: Transliterating to the conventional two-letter forms also makes a Danish name
#: match the way a foreign parent would spell it (Ærø / Aeroe).
_TRANSLIT = str.maketrans({
    "æ": "ae", "ø": "oe", "å": "aa", "ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss",
})


def core_tokens(name: str) -> frozenset[str]:
    """Distinctive tokens: lowercased, transliterated, noise words removed."""
    s = (name or "").lower().translate(_TRANSLIT)
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    parts = re.split(r"[^0-9a-z]+", s)
    return frozenset(p for p in parts if p and p not in _NOISE and len(p) > 1)


@dataclass(frozen=True)
class NameLink:
    subsidiary: str
    owner: str
    shared: frozenset[str]
    sub_core: frozenset[str]
    owner_core: frozenset[str]

    @property
    def continuous(self) -> bool:
        """Every distinctive token of one name appears in the other.

        Containment rather than Jaccard: a parent legitimately carries extra
        tokens the subsidiary does not (`Jiangsu` XR-TURBO), and requiring
        symmetric similarity would reject exactly the transparent case this is
        meant to reward.
        """
        if not self.sub_core or not self.owner_core:
            return False
        return (self.sub_core <= self.owner_core) or (self.owner_core <= self.sub_core)

    @property
    def partial(self) -> bool:
        return bool(self.shared) and not self.continuous


def compare(subsidiary: str, owner: str) -> NameLink:
    a, b = core_tokens(subsidiary), core_tokens(owner)
    return NameLink(subsidiary=subsidiary, owner=owner, shared=a & b,
                    sub_core=a, owner_core=b)
