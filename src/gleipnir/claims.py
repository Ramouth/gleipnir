"""Layer 4 — the claim model (architecture.md §5).

The atom is not a company record. It is a **claim**: one source asserting one
thing about one subject, over a stated period, with provenance back to the exact
bytes it came from. Claims are append-only and never merged; two sources
disagreeing is a `Contradiction`, not an error.

`epistemic_tier` is the axis that makes contradictions useful, and it is
per *field*, never per source — see `EpistemicTier` for why one CVR document
carries three different tiers at once.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum
from typing import Any


class EpistemicTier(StrEnum):
    """What *kind* of statement this is — not how likely it is to be true.

    Keeping these as distinct kinds rather than confidence levels is what lets
    the system say "a self-declared claim contradicts a registered fact", which
    is a finding, instead of "low-confidence value lost to high-confidence
    value", which throws the finding away.

    The useful axis is **who bears legal consequence if this is false**:

    REGISTERED   the state records it as a constitutive act. Company name,
                 status, legal form, registered address, appointed directors.
                 Falsity here is the registrar's problem, not the filer's.

    FILED        subject-authored, state-collected, sometimes auditor-attested,
                 not state-verified. Annual accounts.

    SELF_DECLARED_TO_REGISTRY
                 the subject tells the registrar and nobody checks — but a
                 false filing is a criminal offence with a named responsible
                 person. Denmark's *ejerregister* and *reelle ejere* live here.
                 This matters more than any other tier decision in the system:
                 the field closest to the mission is the one the adversary
                 authors. A concealed structure will have a clean, complete,
                 plausible ownership registration. That is the point of it.

    SELF_DECLARED
                 websites, marketing, pitch decks. No consequence for falsity.

    THIRD_PARTY  press, research, commercial databases.

    ANALYST      a human on our side recorded an observation.
    """

    REGISTERED = "registered"
    FILED = "filed"
    SELF_DECLARED_TO_REGISTRY = "self_declared_to_registry"
    SELF_DECLARED = "self_declared"
    THIRD_PARTY = "third_party"
    ANALYST = "analyst"


class Predicate(StrEnum):
    """Closed vocabulary. Adding one is a deliberate act, not a string literal
    invented at a call site."""

    HAS_NAME = "has_name"
    HAS_STATUS = "has_status"
    HAS_LIFECYCLE = "has_lifecycle"
    HAS_LEGAL_FORM = "has_legal_form"
    REGISTERED_AT = "registered_at"
    HAS_INDUSTRY = "has_industry"
    HAS_CAPITAL = "has_capital"
    HAS_PURPOSE = "has_purpose"

    #: Ownership and control are separate edge types with separate semantics.
    #: threat-model.md §2.2 — effective control must be computed, never read off
    #: a single field, and a minority stake paired with voting control is the
    #: classic evasion of the >50% test.
    OWNS = "owns"
    HAS_VOTING_RIGHTS = "has_voting_rights"

    HAS_ROLE = "has_role"
    FOUNDED_BY = "founded_by"
    AUDITED_BY = "audited_by"

    #: Who may legally bind the company. A control signal in the EU
    #: ownership-and-control sense, and a plain CVR field.
    SIGNING_RULE = "signing_rule"
    AUDIT_WAIVED = "audit_waived"
    AML_OBLIGED_ENTITY = "aml_obliged_entity"

    #: Publicly listed. Direct evidence of disclosure obligations, and the
    #: answer to the XR-Turbo case: a parent outside CVR's reach is not
    #: thereby opaque if it is listed on an exchange.
    IS_LISTED = "is_listed"
    #: Reinstated after compulsory dissolution — a lifecycle event the
    #: criminal-enterprise research named as a predicate.
    REINSTATED_AFTER_DISSOLUTION = "reinstated_after_dissolution"
    #: `OPLØSNINGSTRUSSEL_SENESTE` — the registrar's formal warning that the
    #: company will be struck off. An authority's dated act against the entity,
    #: not a structural property, and found by `scripts/questions.py` rather
    #: than authored.
    DISSOLUTION_THREAT = "dissolution_threat"
    #: Publishes its shareholder register. Positive transparency evidence.
    PUBLISHES_SHAREHOLDER_REGISTER = "publishes_shareholder_register"
    #: Under financial-sector supervision.
    SUPERVISORY_CATEGORY = "supervisory_category"
    HAS_WEBSITE = "has_website"
    HAS_EMAIL_DOMAIN = "has_email_domain"

    #: Self-declared identity assertion — "CVR 12345678" printed on a page.
    #: Danish companies are legally required to display it, which turns a
    #: website into a *deterministically joinable* source. Company-level entity
    #: resolution largely disappears; only the person-level problem remains.
    CLAIMS_IDENTIFIER = "claims_identifier"
    FOUNDED_ON = "founded_on"
    HAS_LOCATION = "has_location"
    MEMBER_OF_GROUP = "member_of_group"

    #: A human judgement about an entity, at ANALYST tier. Carries its own basis
    #: and the data state it was made against — see `analyst.py`.
    ANALYST_VERDICT = "analyst_verdict"

    #: An entity an official designating act NAMES but does not itself designate.
    #: The EU wrote the name down; it drew no legal consequence. A lead, and
    #: explicitly not a designation — see `oracle.to_claims` on why it may not
    #: propagate.
    NAMED_IN_DESIGNATION = "named_in_designation"
    EMPLOYS = "employs"

    #: Applicant on a published patent application (EPO OPS). A register of a
    #: different kind: filed, dated, examined activity that a company either has
    #: or does not. Matched on APPLICANT NAME, so a hit is a candidate and never
    #: an identity — the same discipline the designation matcher runs under.
    #:
    #: **Absence means almost nothing.** Patent-holding is rare among ordinary
    #: Danish companies, so "no filings" is the overwhelming default and is not
    #: evidence of anything. It becomes information only against a self-declared
    #: claim to hold patents, which is a `narrative.py` gap and not a predicate.
    HOLDS_PATENT_APPLICATION = "holds_patent_application"
    #: An INPADOC legal event recording a change of applicant or proprietor —
    #: chain of title. An ownership transfer recorded in a register that is not
    #: CVR, dated, and by a party with no Danish filing obligation.
    PATENT_RIGHTS_TRANSFERRED = "patent_rights_transferred"
    #: A person's stated institutional affiliation, as printed on a publication
    #: and aggregated by OpenAlex. Dated, and carrying the institution's ROR id,
    #: country and type — so "university" and "company" are the same claim with
    #: a different qualifier, which is what makes one query answer both.
    #:
    #: **Nationality-neutral by construction.** The claim records an institution
    #: a person named on a paper. It records nothing about the person's
    #: nationality, ethnicity or origin, and no predicate may derive one.
    HAS_AFFILIATION = "has_affiliation"
    #: Two people named on the same publication. Collaboration, dated, and
    #: nothing more: co-authorship is not endorsement, association or control.
    CO_AUTHORED_WITH = "co_authored_with"
    #: Self-reported peer-review activity for a named journal or organisation.
    #: Coverage is poor and self-selected — see `extract/openalex.py`.
    PEER_REVIEWED_FOR = "peer_reviewed_for"

    #: A natural person named as inventor. Kept separate from ownership because
    #: it is neither: `analyst.py` records that inventor search cannot
    #: disambiguate a common name, so this is a lead for a human and nothing a
    #: predicate may threshold.
    NAMED_AS_INVENTOR = "named_as_inventor"


@dataclass(frozen=True)
class EntityRef:
    """A reference to something the claim is about.

    `key` is source-scoped and may be unresolved — that is expected. Entity
    resolution happens later and never by overwriting a claim.
    """

    kind: str          # 'company' | 'person' | 'other' | 'address' | 'value'
    key: str           # CVR number, CVR enhedsNummer, or a normalised literal
    label: str | None = None

    def __str__(self) -> str:
        return f"{self.kind}:{self.key}" + (f" ({self.label})" if self.label else "")


@dataclass(frozen=True)
class Claim:
    subject: EntityRef
    predicate: Predicate
    object: EntityRef | str | float | bool | None
    source_id: str
    epistemic_tier: EpistemicTier
    raw_ref: str                      # content hash of the payload it came from
    valid_from: date | None = None    # when the claim says it was true
    valid_to: date | None = None
    observed_at: str | None = None    # when we fetched it
    #: Extraction confidence — "did the parser read this correctly" — and never
    #: truth confidence. The moment these blend into one float the tiers stop
    #: meaning anything.
    confidence: float = 1.0
    qualifiers: dict[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:
        period = ""
        if self.valid_from or self.valid_to:
            period = f" [{self.valid_from or '…'} → {self.valid_to or 'now'}]"
        return f"{self.subject} --{self.predicate}--> {self.object}{period}"


#: Predicates whose claims legitimately carry no `valid_from`. Both are
#: point-in-time facts with no period to have: a company is founded once, and a
#: headcount is filed for a year rather than holding from a date.
#:
#: Everything NOT in this set must be dated, and `test_import_contract.py`
#: fails if a new undated predicate appears. That test exists because three
#: separate hindsight leaks all had the same cause and none was found by
#: review — `sammensatStatus` filed with no period, `medlemsData` whose dates
#: sit one level down on each FUNKTION value (100% of `has_role` undated, so a
#: 2024 board appointment was visible to a 2016 screen), and a liquidator
#: appointed BY a bankruptcy showing as in office a year before it.
UNDATED_BY_NATURE: frozenset["Predicate"] = frozenset()   # set after Predicate


def _undated_by_nature() -> frozenset:
    return frozenset({Predicate.FOUNDED_ON, Predicate.EMPLOYS})


def at(claims: "list[Claim]", predicate: Predicate, on: date | None = None,
       *, require_period: bool = False) -> "list[Claim]":
    """Claims of one predicate whose validity period covers `on`.

    Exists because the obvious thing — `next(c for c in claims if ...)` — silently
    returns the *oldest* version, since CVR lists history oldest-first. That bug
    was live in a diagnostic script and reported Example Shipping A/S under a name it
    held before 2014 and Example Bank A/S as NORMAL. Every consumer would hit it.

    `on=None` means "no date filter", not "today": an implicit now would make
    every result non-reproducible, which architecture.md §9 forbids.

    **`require_period` is the backtest's setting, and the default is not.**
    A claim with no `valid_from` is admitted at every as-of date, which is
    correct for a present-day screen — an undated board seat is still a board
    seat — and is hindsight in a backtest. The two callers want opposite
    things, so the choice is explicit rather than global: `docs/measurement.md`
    §5 records what happened when it was neither.
    """
    out = []
    for c in claims:
        if c.predicate is not predicate:
            continue
        if require_period and on is not None and c.valid_from is None:
            continue
        if on is not None:
            if c.valid_from and c.valid_from > on:
                continue
            if c.valid_to and c.valid_to < on:
                continue
        out.append(c)
    return out


def latest(claims: "list[Claim]", predicate: Predicate, on: date | None = None):
    """The version with the newest `valid_from` covering `on`, or None.

    Ties on `valid_from` are broken by `str(object)` so the choice is stable
    across runs rather than dependent on list order.
    """
    candidates = at(claims, predicate, on)
    if not candidates:
        return None
    return max(candidates, key=lambda c: (c.valid_from or date.min, str(c.object)))
