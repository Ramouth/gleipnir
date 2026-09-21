"""Estonian register records -> claims. Pure functions, same shapes as CVR.

The point of matching CVR's claim shapes exactly is that nothing downstream has
to know Estonia exists. `chain.py` expands an `OWNS` edge the same way whether
the register that filed it is in Copenhagen or Tartu, and the predicates
evaluate the result without a special case.

**Ownership joins at identifier strength, and only there.** A legal-entity
holder carries `isikukood_registrikood`, an Estonian registry code, so
`company --owns--> company` is a real edge that the chain can follow. A natural
person carries a name and, since the 2025 restriction, usually nothing else —
so a person holder becomes a claim that *records* the holding without pretending
the person is identified. `docs/jurisdictions.md` has the measurement: matching
those names against a designation list is Ivanov, Petrov and Kuznetsov.

**Tier is `REGISTERED`.** Estonia's shareholder list is a register entry with a
legal filing obligation behind it, the same standing as CVR's *ejerregister* —
which is `SELF_DECLARED_TO_REGISTRY` in `extract/cvr.py`, and that is the tier
used here too for the ownership edge, for exactly the reason `claims.py` gives:
the field closest to the mission is the one the subject authors.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Iterable

from gleipnir.adapters.ee_ariregister import COUNTRY, SOURCE_ID, Company, Holder
from gleipnir.claims import Claim, EntityRef, EpistemicTier, Predicate


def company_ref(company: Company) -> EntityRef:
    """Keyed on the Estonian registry code, which is what makes it joinable."""
    return EntityRef(kind="company", key=f"ee:{company.code}", label=company.name)


def holder_ref(holder: Holder) -> EntityRef:
    """A legal holder is keyed on its registry code; a natural person is not.

    A person with no identifier gets a name key, and every consumer can see
    from the key alone that nothing resolved them.
    """
    if holder.is_company and holder.code:
        return EntityRef(kind="company", key=f"ee:{holder.code}", label=holder.name)
    if holder.code:
        return EntityRef(kind="person", key=f"ee-person:{holder.code}",
                         label=holder.name)
    kind = "company" if holder.is_company else "person"
    return EntityRef(kind=kind, key=f"name:{holder.name.casefold()}",
                     label=holder.name)


def _share(percent: str) -> float | None:
    """`osaluse_protsent` as a fraction, or None when the register left it out."""
    if not percent:
        return None
    try:
        return float(Decimal(percent.replace(",", ".")) / Decimal(100))
    except (InvalidOperation, ValueError, ZeroDivisionError):
        return None


def extract_company(company: Company, *, raw_ref: str,
                    observed_at: str | None = None) -> list[Claim]:
    """Identity claims for one Estonian company."""
    subject = company_ref(company)
    common = dict(source_id=SOURCE_ID, raw_ref=raw_ref, observed_at=observed_at)
    claims = [
        Claim(subject=subject, predicate=Predicate.HAS_NAME, object=company.name,
              epistemic_tier=EpistemicTier.REGISTERED,
              valid_from=company.registered_on, **common),
        Claim(subject=subject, predicate=Predicate.HAS_STATUS,
              object=company.status_text or company.status,
              epistemic_tier=EpistemicTier.REGISTERED,
              valid_from=company.registered_on,
              qualifiers={"status_code": company.status,
                          "jurisdiction": COUNTRY}, **common),
    ]
    if company.legal_form:
        claims.append(Claim(subject=subject, predicate=Predicate.HAS_LEGAL_FORM,
                            object=company.legal_form,
                            epistemic_tier=EpistemicTier.REGISTERED,
                            valid_from=company.registered_on, **common))
    if company.registered_on:
        claims.append(Claim(subject=subject, predicate=Predicate.FOUNDED_ON,
                            object=str(company.registered_on),
                            epistemic_tier=EpistemicTier.REGISTERED, **common))
    if company.address:
        claims.append(Claim(subject=subject, predicate=Predicate.REGISTERED_AT,
                            object=EntityRef(kind="address",
                                             key=f"ee-addr:{company.address.casefold()}",
                                             label=company.address),
                            epistemic_tier=EpistemicTier.REGISTERED,
                            valid_from=company.registered_on,
                            qualifiers={"jurisdiction": COUNTRY}, **common))
    return claims


def extract_holders(company: Company, holders: Iterable[Holder], *, raw_ref: str,
                    observed_at: str | None = None) -> list[Claim]:
    """Ownership edges, filed holder -> held company.

    Direction matches `extract/cvr.py`: the holder is the subject and the
    company it holds is the object, so `chain.py` walks an Estonian edge with
    no special case.
    """
    owned = company_ref(company)
    out: list[Claim] = []
    for holder in holders:
        if not holder.name and not holder.code:
            continue
        share = _share(holder.share_percent)
        out.append(Claim(
            subject=holder_ref(holder),
            predicate=Predicate.OWNS,
            object=owned,
            source_id=SOURCE_ID,
            # The same standing as Denmark's ejerregister: a filing obligation
            # on the subject, checked by nobody.
            epistemic_tier=EpistemicTier.SELF_DECLARED_TO_REGISTRY,
            raw_ref=raw_ref,
            valid_from=holder.valid_from,
            valid_to=holder.valid_to,
            observed_at=observed_at,
            qualifiers={
                "share": share,
                "share_percent": holder.share_percent,
                "jurisdiction": COUNTRY,
                "holder_type": "legal" if holder.is_company else "natural",
                # Whether this edge can be followed at all. A natural person
                # since the 2025 restriction usually cannot be.
                "resolvable": holder.resolvable,
                "holder_country": holder.foreign_country or COUNTRY,
                "country_code": COUNTRY,
            },
        ))
    return out


def unresolvable_holders(holders: Iterable[Holder]) -> list[Holder]:
    """Holders the chain cannot follow — reported, never silently dropped.

    A terminating edge is a fact about the register's coverage and belongs in
    the screen's `unknowable` section, not in a gap in the graph.
    """
    return [h for h in holders if not h.resolvable]
