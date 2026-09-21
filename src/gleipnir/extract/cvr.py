"""Layer 3 — CVR payload → claims. Pure functions, no I/O.

Everything here operates on bytes already in the raw store, so a parser fix is a
reparse and costs no quota. Tested against recorded fixtures.

**The tier map is the substance of this module.** One CVR document carries at
least three epistemic tiers, and applying one tier to the whole source is the
mistake that would quietly break every downstream contradiction:

    REGISTERED                  name, status, legal form, address, industry,
                                management roles (LEDELSESORGAN), founders,
                                auditor, capital, signing rule, purpose
    SELF_DECLARED_TO_REGISTRY   EJERREGISTER and REELLE_EJERE — the company
                                tells Erhvervsstyrelsen who owns it and nobody
                                verifies it

That second bucket is the one the mission depends on and the one the adversary
authors. See `claims.EpistemicTier`.

Person names are retained: they are the substrate for entity resolution and the
Tier B recurrence predicates, so they cannot be dropped at ingest the way a CPR
number is. The control point for personal data is **egress** — report
generation — not this layer.
"""
from __future__ import annotations

import re
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Iterator

from gleipnir.adapters.cvr import valid_cvr_checksum
from gleipnir.claims import Claim, EntityRef, EpistemicTier, Predicate

SOURCE_ID = "cvr"

#: `attributter` entries we turn into claims, with the tier each carries.
_ATTRIBUTE_MAP: dict[str, tuple[Predicate, EpistemicTier]] = {
    "KAPITAL": (Predicate.HAS_CAPITAL, EpistemicTier.REGISTERED),
    "TEGNINGSREGEL": (Predicate.SIGNING_RULE, EpistemicTier.REGISTERED),
    "FORMÅL": (Predicate.HAS_PURPOSE, EpistemicTier.REGISTERED),
    "REVISION_FRAVALGT": (Predicate.AUDIT_WAIVED, EpistemicTier.REGISTERED),
    "OMFATTET_AF_LOV_OM_HVIDVASK_OG_TERRORFINANSIERING": (
        Predicate.AML_OBLIGED_ENTITY,
        EpistemicTier.REGISTERED,
    ),
    # Found by the import contract, not by anyone remembering they existed.
    "BØRSNOTERET": (Predicate.IS_LISTED, EpistemicTier.REGISTERED),
    "GENOPTAGELSE_TVANGSOPLØSNING": (
        Predicate.REINSTATED_AFTER_DISSOLUTION, EpistemicTier.REGISTERED),
    "OFFENTLIG_EJERBOG": (
        Predicate.PUBLISHES_SHAREHOLDER_REGISTER, EpistemicTier.REGISTERED),
    "TILSYN_KATEGORI": (Predicate.SUPERVISORY_CATEGORY, EpistemicTier.REGISTERED),
    # Found by the question generator, not by anyone remembering it existed —
    # the same route as BØRSNOTERET above.
    "OPLØSNINGSTRUSSEL_SENESTE": (Predicate.DISSOLUTION_THREAT,
                                  EpistemicTier.REGISTERED),
}

#: Organisation names in `deltagerRelation` that denote *ownership* rather than
#: a management role. Both are self-declared to the registrar.
_OWNERSHIP_ORGS = {"EJERREGISTER", "REELLE EJERE", "REELLEEJERE"}


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except (ValueError, TypeError):
        return None


def _period(obj: dict[str, Any] | None) -> tuple[date | None, date | None]:
    p = (obj or {}).get("periode") or {}
    return _parse_date(p.get("gyldigFra")), _parse_date(p.get("gyldigTil"))


def _company_ref(doc: dict[str, Any]) -> EntityRef:
    cvr = doc.get("cvrNummer")
    names = doc.get("navne") or []
    label = names[-1].get("navn") if names else None
    return EntityRef(kind="company", key=str(cvr).zfill(8), label=label)


#: Legal-form suffixes that mark a name as a company rather than a person.
_LEGAL_FORM = re.compile(
    r"(?:\b|\s)(?:A/S|ApS|IVS|K/S|I/S|P/S|S\.?\s?[àa]\.?\s?r\.?\s?l\.?|"
    r"S\.?A\.?R\.?L\.?|GmbH|AG|mbH|B\.?V\.?|N\.?V\.?|Ltd\.?|Limited|LLC|"
    r"L\.?L\.?C\.?|Inc\.?|Corp\.?|Corporation|PLC|S\.?A\.?|S\.?p\.?A\.?|"
    r"AB|Oy|Oyj|AS|ASA|SE|SARL|SAS|Kft|Sp\.? z o\.?o\.?|Pty|Pte)\s*$", re.I)


def classify_other_party(name: str | None, hovedtyper: set[str]) -> str:
    """Is this `ANDEN_DELTAGER` a natural person, a company, or undetermined?

    `ANDEN_DELTAGER` is CVR's catch-all for a participant with no Danish unit
    number, and it covers more than iteration 8 catalogued. It is also where
    **foreign natural persons** land — one observed subsidiary's three directors
    were all filed this way, and treating them as non-persons made `nominee_density`
    blind to every foreign director and made `ultimate_owners_are_named_persons`
    report a named human as "not a person".

    One rule settles most of it deterministically: **Danish company law requires
    members of a management body to be natural persons.** So an `ANDEN_DELTAGER`
    sitting in a `LEDELSESORGAN` is a person, whatever else is unknown about it.
    Across the corpus that is 90 of 134 appearances.

    Otherwise a legal-form suffix marks a company, and anything left is
    `other_party` — undetermined, and reported as such rather than guessed.
    """
    if "LEDELSESORGAN" in hovedtyper:
        return "person"
    if name and _LEGAL_FORM.search(name.strip()):
        return "company"
    return "other_party"


def _participant_ref(deltager: dict[str, Any],
                     hovedtyper: set[str] | None = None) -> EntityRef:
    """Key a participant so that company nodes share one identifier space.

    For a VIRKSOMHED participant, `forretningsnoegle` is its **CVR number** —
    the same key the screened company itself is under. Keying on it rather than
    on `enhedsNummer` is what makes an ownership edge traversable: the chain
    walker can fetch the owner directly, and the node it creates is the same
    node the owner's own document produces. Keyed on enhedsNummer instead, every
    company would appear twice in the graph and no chain would ever join up.

    For a PERSON, `enhedsNummer` is all there is. CVR does not expose CPR
    numbers and we do not want them (§11) — so a person key is CVR-scoped, two
    different values are *not* evidence of two different people across sources,
    and resolving that is the tier-3 problem this deliberately does not solve.
    """
    etype = deltager.get("enhedstype") or ""
    names = deltager.get("navne") or []
    label = names[-1].get("navn") if names else None
    if etype == "VIRKSOMHED":
        cvr = deltager.get("forretningsnoegle")
        if cvr:
            number = str(cvr).zfill(8)
            if valid_cvr_checksum(number):
                return EntityRef(kind="company", key=number, label=label)
        # A company participant with no usable CVR is still a real edge; keep it
        # under its enhedsNummer and mark it unresolvable rather than dropping.
        return EntityRef(kind="company", key=f"enh:{deltager.get('enhedsNummer')}",
                         label=label)
    if etype == "ANDEN_DELTAGER":
        kind = classify_other_party(label, hovedtyper or set())
        prefix = {"person": "fp", "company": "fc"}.get(kind, "anden")
        return EntityRef(kind=kind,
                         key=f"{prefix}:{deltager.get('enhedsNummer')}", label=label)
    kind = {"PERSON": "person"}.get(etype, "other")
    return EntityRef(kind=kind, key=str(deltager.get("enhedsNummer")), label=label)


def _valued(entries: list[dict[str, Any]] | None, key: str = "navn") -> Iterator[tuple[Any, date | None, date | None]]:
    """Yield (value, valid_from, valid_to) over a CVR history list.

    CVR represents nearly everything as a list of dated versions. Collapsing to
    "newest" here would throw away the timing signal that `docs/predicates.md`
    Tier B is built on, so every version becomes its own claim.
    """
    for entry in entries or []:
        frm, to = _period(entry)
        yield entry.get(key), frm, to


def extract_company(
    payload: dict[str, Any], *, raw_ref: str, observed_at: str | None = None
) -> list[Claim]:
    """Turn one `Vrvirksomhed` document into claims.

    Accepts either the raw Elasticsearch response or an unwrapped document, so
    a caller does not have to know which layer it is holding.
    """
    doc = _unwrap(payload)
    if not doc:
        return []

    subject = _company_ref(doc)
    claims: list[Claim] = []

    def add(pred: Predicate, obj: Any, tier: EpistemicTier,
            frm: date | None = None, to: date | None = None, **qual: Any) -> None:
        claims.append(
            Claim(
                subject=subject, predicate=pred, object=obj, source_id=SOURCE_ID,
                epistemic_tier=tier, raw_ref=raw_ref, valid_from=frm, valid_to=to,
                observed_at=observed_at, qualifiers=qual,
            )
        )

    for navn, frm, to in _valued(doc.get("navne")):
        add(Predicate.HAS_NAME, navn, EpistemicTier.REGISTERED, frm, to)

    for status, frm, to in _valued(doc.get("virksomhedsstatus"), key="status"):
        add(Predicate.HAS_STATUS, status, EpistemicTier.REGISTERED, frm, to,
            field="virksomhedsstatus")

    # `virksomhedsstatus` is narrow and frequently just "NORMAL" for a company
    # that is in fact bankrupt. The lifecycle state lives in
    # `virksomhedMetadata.sammensatStatus` (UNDERKONKURS, OPLØSTEFTERKONKURS,
    # TVANGSOPLØST, …), and that is the one a screening finding depends on.
    # Observed: Example Bank A/S, dissolved after bankruptcy in 2011, carries
    # `virksomhedsstatus = NORMAL`.
    #
    # It is filed with NO period, and that made it true of every as-of date. A
    # screen of Example Bank A/S as of 2010 — a year before it failed — returned
    # `insolvency_or_dissolution TRUE UNDERKONKURS`, because `claims.at()`
    # admits a claim with no validity period at any date. Hindsight, in the one
    # place `architecture.md` §9 forbids it.
    #
    # The composite describes the state that began when the CURRENT status
    # period began, so it is dated from the newest `virksomhedsstatus`
    # `gyldigFra`. Where the document carries no dated status at all it stays
    # timeless, and consumers see `composite=True` with no `valid_from` —
    # which is a coverage statement, not a licence to read it as always true.
    meta = doc.get("virksomhedMetadata") or {}
    if meta.get("sammensatStatus"):
        starts = [frm for _, frm, _ in _valued(doc.get("virksomhedsstatus"), key="status")
                  if frm]
        add(Predicate.HAS_STATUS, meta["sammensatStatus"], EpistemicTier.REGISTERED,
            max(starts) if starts else None, None,
            field="sammensatStatus", composite=True)

    for lf in doc.get("livsforloeb") or []:
        frm, to = _period(lf)
        add(Predicate.HAS_LIFECYCLE, "active", EpistemicTier.REGISTERED, frm, to)

    for form, frm, to in _valued(doc.get("virksomhedsform"), key="langBeskrivelse"):
        add(Predicate.HAS_LEGAL_FORM, form, EpistemicTier.REGISTERED, frm, to)

    for adr in doc.get("beliggenhedsadresse") or []:
        frm, to = _period(adr)
        add(Predicate.REGISTERED_AT,
            EntityRef(kind="address", key=_address_key(adr), label=_address_label(adr)),
            EpistemicTier.REGISTERED, frm, to,
            kommune=(adr.get("kommune") or {}).get("kommuneNavn"),
            postcode=str(adr.get("postnummer") or ""), unit="registered_office")

    for br in doc.get("hovedbranche") or []:
        frm, to = _period(br)
        add(Predicate.HAS_INDUSTRY, br.get("branchekode"), EpistemicTier.REGISTERED,
            frm, to, text=br.get("branchetekst"))

    # Contact details live under `kontaktoplysning` and carry a `hemmelig`
    # flag — Denmark's contact-secrecy marker. A protected entry is dropped at
    # this boundary rather than filtered downstream, for the same reason CPR
    # numbers are: a value that must not be used should not enter the graph
    # where a later consumer can reach it by accident.
    for entry in doc.get("hjemmeside") or []:
        if entry.get("hemmelig"):
            continue
        frm, to = _period(entry)
        add(Predicate.HAS_WEBSITE, entry.get("kontaktoplysning"),
            EpistemicTier.REGISTERED, frm, to)

    for entry in doc.get("elektroniskPost") or []:
        if entry.get("hemmelig"):
            continue
        value = entry.get("kontaktoplysning") or ""
        frm, to = _period(entry)
        # The local part is a person's name often enough that keeping it would
        # be gratuitous. The domain is what shared-infrastructure detection
        # needs, and it is a corporate fact.
        domain = value.rsplit("@", 1)[-1].lower() if "@" in value else ""
        if domain:
            add(Predicate.HAS_EMAIL_DOMAIN, domain, EpistemicTier.REGISTERED, frm, to)

    for attr in doc.get("attributter") or []:
        mapped = _ATTRIBUTE_MAP.get(attr.get("type") or "")
        if not mapped:
            continue
        pred, tier = mapped
        for v in attr.get("vaerdier") or []:
            frm, to = _period(v)
            add(pred, v.get("vaerdi"), tier, frm, to)

    # Founding date and employment come from virksomhedMetadata. Without them
    # every website claim of "founded 1911" or "50 employees" pairs against
    # nothing and is reported as an existence_conflict — a false finding
    # manufactured by our own missing extraction, on every site that states
    # either. Observed on 3 of 4 live sites tested.
    if meta.get("stiftelsesDato"):
        add(Predicate.FOUNDED_ON, _parse_date(meta["stiftelsesDato"]),
            EpistemicTier.REGISTERED)

    for key in ("nyesteAarsbeskaeftigelse", "nyesteKvartalsbeskaeftigelse",
                "nyesteErstMaanedsbeskaeftigelse", "nyesteMaanedsbeskaeftigelse"):
        emp = meta.get(key)
        if isinstance(emp, dict) and emp.get("antalAnsatte") is not None:
            add(Predicate.EMPLOYS, int(emp["antalAnsatte"]),
                EpistemicTier.REGISTERED, basis=key,
                interval=emp.get("intervalKodeAntalAnsatte"))
            break

    # Production units are branch addresses. A multi-site business lists all of
    # them on its website while the registry records one legal seat — comparing
    # branches against the seat produced 7 false conflicts on one live site.
    for pe in doc.get("penheder") or []:
        for adr in pe.get("beliggenhedsadresse") or []:
            frm, to = _period(adr)
            add(Predicate.REGISTERED_AT,
                EntityRef(kind="address", key=_address_key(adr),
                          label=_address_label(adr)),
                EpistemicTier.REGISTERED, frm, to, unit="production_unit",
                postcode=str(adr.get("postnummer") or ""))

    claims.extend(_extract_relations(doc, subject, raw_ref, observed_at))
    return claims


def _extract_relations(
    doc: dict[str, Any], subject: EntityRef, raw_ref: str, observed_at: str | None
) -> list[Claim]:
    out: list[Claim] = []
    for rel in doc.get("deltagerRelation") or []:
        deltager = rel.get("deltager") or {}
        # The participant's own roles decide what kind of thing it is, so the
        # organisations must be read before the reference is built.
        hovedtyper = {org.get("hovedtype") for org in (rel.get("organisationer") or [])
                      if org.get("hovedtype")}
        if not deltager:
            # A relation can carry an organisation with no participant attached.
            # Skipping it silently would lose a board seat, so record it as an
            # unresolved reference instead.
            party = EntityRef(kind="other", key="unresolved")
        else:
            party = _participant_ref(deltager, hovedtyper)

        for org in rel.get("organisationer") or []:
            org_names = [n.get("navn") for n in (org.get("organisationsNavn") or [])]
            org_name = org_names[-1] if org_names else None
            is_ownership = any(
                (n or "").upper().replace(" ", "") in
                {s.replace(" ", "") for s in _OWNERSHIP_ORGS}
                for n in org_names
            )
            for md in org.get("medlemsData") or []:
                out.extend(
                    _member_claims(
                        subject=subject, party=party, org_name=org_name,
                        hovedtype=org.get("hovedtype"), member=md,
                        is_ownership=is_ownership, raw_ref=raw_ref,
                        observed_at=observed_at,
                    )
                )
            if not (org.get("medlemsData") or []):
                # Membership with no attributes still asserts the relationship.
                frm, to = _period(org)
                out.append(Claim(
                    subject=subject,
                    predicate=Predicate.HAS_ROLE, object=party,
                    source_id=SOURCE_ID, epistemic_tier=EpistemicTier.REGISTERED,
                    raw_ref=raw_ref, valid_from=frm, valid_to=to,
                    observed_at=observed_at,
                    qualifiers={"role": org_name, "hovedtype": org.get("hovedtype")},
                ))
    return out


def _member_claims(
    *, subject: EntityRef, party: EntityRef, org_name: str | None,
    hovedtype: str | None, member: dict[str, Any], is_ownership: bool,
    raw_ref: str, observed_at: str | None,
) -> Iterator[Claim]:
    """Claims from one membership.

    Ownership and voting rights are emitted as *separate* claims even when the
    two percentages agree, because the whole point of keeping them apart is to
    detect the case where they diverge — a minority stake carrying majority
    votes is the classic >50%-rule evasion (threat-model.md §2.2). Collapsing
    them when equal would mean the divergence had nowhere to appear.
    """
    emitted_share = False
    for attr in member.get("attributter") or []:
        atype = attr.get("type") or ""
        for v in attr.get("vaerdier") or []:
            frm, to = _period(v)
            raw = v.get("vaerdi")
            if atype == "EJERANDEL_PROCENT":
                emitted_share = True
                yield Claim(
                    subject=party, predicate=Predicate.OWNS, object=subject,
                    source_id=SOURCE_ID,
                    epistemic_tier=EpistemicTier.SELF_DECLARED_TO_REGISTRY,
                    raw_ref=raw_ref, valid_from=frm, valid_to=to,
                    observed_at=observed_at,
                    qualifiers={"share": _as_fraction(raw), "register": org_name},
                )
            elif atype == "EJERANDEL_STEMMERET_PROCENT":
                yield Claim(
                    subject=party, predicate=Predicate.HAS_VOTING_RIGHTS,
                    object=subject, source_id=SOURCE_ID,
                    epistemic_tier=EpistemicTier.SELF_DECLARED_TO_REGISTRY,
                    raw_ref=raw_ref, valid_from=frm, valid_to=to,
                    observed_at=observed_at,
                    qualifiers={"share": _as_fraction(raw), "register": org_name},
                )

    if not is_ownership:
        # One claim per FUNKTION period, not one undated claim per membership.
        #
        # `medlemsData` entries carry no `periode` of their own — the dates are
        # one level down, on each FUNKTION *value*. `_period(member)` therefore
        # returned `(None, None)` for **every management role in the register**:
        # 3,774 of 12,458 claims across 400 companies, 100% of `has_role`.
        # `claims.at()` admits an undated claim at any as-of date, so a 2024
        # board appointment was visible to a 2016 screen. Found by the question
        # generator, which reported a liquidator in office a year before
        # bankruptcy in 789 of 2,126 cases — the same defect one layer up.
        for frm, to, function in _function_periods(member):
            yield Claim(
                subject=subject, predicate=_role_predicate(hovedtype),
                object=party, source_id=SOURCE_ID,
                epistemic_tier=EpistemicTier.REGISTERED, raw_ref=raw_ref,
                valid_from=frm, valid_to=to, observed_at=observed_at,
                qualifiers={"role": org_name, "hovedtype": hovedtype,
                            "function": function},
            )
    elif not emitted_share:
        # Listed in the ownership register with no percentage filed. This is
        # not "owns 0%" — it is an unquantified ownership assertion, and the
        # difference matters when aggregating toward a 50% test.
        frm, to = _period(member)
        yield Claim(
            subject=party, predicate=Predicate.OWNS, object=subject,
            source_id=SOURCE_ID,
            epistemic_tier=EpistemicTier.SELF_DECLARED_TO_REGISTRY,
            raw_ref=raw_ref, valid_from=frm, valid_to=to,
            observed_at=observed_at,
            qualifiers={"share": None, "register": org_name},
        )


def _role_predicate(hovedtype: str | None) -> Predicate:
    return {
        "STIFTERE": Predicate.FOUNDED_BY,
        "REVISION": Predicate.AUDITED_BY,
    }.get(hovedtype or "", Predicate.HAS_ROLE)


def _later(a: date | None, b: date | None) -> date | None:
    """The later of two open lower bounds; None means unbounded."""
    return b if a is None else a if b is None else max(a, b)


def _earlier(a: date | None, b: date | None) -> date | None:
    """The earlier of two open upper bounds; None means unbounded."""
    return b if a is None else a if b is None else min(a, b)


def _function_periods(member: dict[str, Any]) -> list[tuple[date | None, date | None, str | None]]:
    """Every FUNKTION value on a membership, with the period it was held for.

    A person whose function changed — member of the board, then chair — holds
    two dated roles, not one undated one. Returns a single undated entry when
    no FUNKTION is filed, so a membership never disappears: an undated role is
    still a role, and dropping it would lose a board seat to fix a date.
    """
    outer_from, outer_to = _period(member)
    out: list[tuple[date | None, date | None, str | None]] = []
    for attr in member.get("attributter") or []:
        if (attr.get("type") or "").upper() != "FUNKTION":
            continue
        for v in attr.get("vaerdier") or []:
            frm, to = _period(v)
            # Intersected with the membership's own period, not substituted for
            # it. A board seat recorded as ending 2023-04-01 whose FUNKTION row
            # is still open ended in 2023: the narrower of the two bounds is the
            # one the register asserts, and taking only the inner period
            # reported a departed chair as sitting.
            out.append((_later(frm, outer_from), _earlier(to, outer_to),
                        v.get("vaerdi")))
    if not out:
        return [(outer_from, outer_to, None)]
    # Total order, so two same-day functions never swap between runs.
    return sorted(out, key=lambda t: (t[0] or date.min, str(t[2])))


def _function_name(member: dict[str, Any]) -> str | None:
    for attr in member.get("attributter") or []:
        if (attr.get("type") or "").upper() == "FUNKTION":
            vals = attr.get("vaerdier") or []
            if vals:
                return vals[-1].get("vaerdi")
    return None


def _as_fraction(raw: Any) -> Decimal | None:
    """CVR files ownership as a fraction (0.05 == 5%), not a percentage.

    **Decimal, parsed from the filed string.** Binary floats make the bunching
    comparison inconsistent across thresholds — `0.05 - 0.02` is
    `0.030000000000000002` while `0.10 - 0.02` is exactly `0.08`, so a share
    exactly 2pp below a threshold hits at five thresholds and misses at two.
    Decimal arithmetic on the filed decimal string removes that entire class of
    defect, and ownership fractions are decimal quantities by nature.

    Kept as the fraction rather than multiplied to a percentage because the
    estimator in docs/predicate-selection.md measures excess mass just below
    0.25 / 0.3333 / 0.50 / 0.6667, and a trip through percent would blur exactly
    the margin it looks at.
    """
    if raw is None:
        return None
    try:
        return Decimal(str(raw).strip())
    except (InvalidOperation, ValueError):
        return None


def _address_key(adr: dict[str, Any]) -> str:
    parts = [
        str(adr.get("vejkode") or ""), str(adr.get("husnummerFra") or ""),
        str(adr.get("etage") or ""), str(adr.get("sidedoer") or ""),
        str(adr.get("postnummer") or ""),
        str((adr.get("kommune") or {}).get("kommuneKode") or ""),
    ]
    return "|".join(parts)


def _address_label(adr: dict[str, Any]) -> str | None:
    bits = [adr.get("vejnavn"), str(adr.get("husnummerFra") or "").strip(),
            adr.get("postnummer") and str(adr["postnummer"]), adr.get("postdistrikt")]
    label = " ".join(str(b) for b in bits if b)
    return label or None


def _unwrap(payload: dict[str, Any]) -> dict[str, Any]:
    """Accept an ES response, a hit, a _source, or a bare Vrvirksomhed."""
    if "hits" in payload:
        hits = payload.get("hits", {}).get("hits", [])
        if not hits:
            return {}
        payload = hits[0]
    if "_source" in payload:
        payload = payload["_source"]
    return payload.get("Vrvirksomhed", payload)
