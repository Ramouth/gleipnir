"""The import contract: every field the source carries is accounted for.

A parser that silently drops a field passes every unit test written against a
synthetic fixture, because the fixture only contains what the author already
thought to parse. Six such failures happened in this project, and every one was
found by running against live data rather than by a test:

    hjemmeside read `vaerdi`, the field is `kontaktoplysning`
        -> the website was silently absent for every company
    stiftelsesDato never extracted
        -> every site stating "founded 1911" produced a false existence_conflict
    penheder never extracted
        -> a nine-branch business produced seven false address conflicts
    virksomhedsstatus read instead of sammensatStatus
        -> Example Bank A/S, dissolved after bankruptcy, reported NORMAL
    ANDEN_DELTAGER mapped to a generic "other"
        -> the chain-termination signal could not fire at all
    forretningsnoegle unused
        -> every company appeared twice and no chain ever joined up

The contract inverts the test. Rather than asserting that fields we remembered
produce claims, it enumerates what the **document actually contains** and fails
on anything carrying data that produced neither a claim nor a declared reason
for skipping it. A field added to CVR's response tomorrow surfaces as a failure
instead of a silent gap.

`IGNORED` is the other half and matters as much: a deliberate decision not to
import something, written down with its reason, is reviewable. A decision
implicit in what the parser happens to touch is not.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from gleipnir.claims import Claim, Predicate

#: source field -> the predicate it must produce when it carries data.
#: Keys are top-level names inside `Vrvirksomhed` unless prefixed `meta.`,
#: which reads from `virksomhedMetadata`.
CVR_REQUIRED: dict[str, Predicate] = {
    "navne": Predicate.HAS_NAME,
    "virksomhedsstatus": Predicate.HAS_STATUS,
    "virksomhedsform": Predicate.HAS_LEGAL_FORM,
    "beliggenhedsadresse": Predicate.REGISTERED_AT,
    "hovedbranche": Predicate.HAS_INDUSTRY,
    "hjemmeside": Predicate.HAS_WEBSITE,
    "elektroniskPost": Predicate.HAS_EMAIL_DOMAIN,
    "penheder": Predicate.REGISTERED_AT,
    "livsforloeb": Predicate.HAS_LIFECYCLE,
    "meta.sammensatStatus": Predicate.HAS_STATUS,
    "meta.stiftelsesDato": Predicate.FOUNDED_ON,
}

#: `attributter` entries carry their own type. Each type either maps to a
#: predicate or is declared ignored.
CVR_ATTRIBUTES: dict[str, Predicate] = {
    "KAPITAL": Predicate.HAS_CAPITAL,
    "TEGNINGSREGEL": Predicate.SIGNING_RULE,
    "FORMÅL": Predicate.HAS_PURPOSE,
    "REVISION_FRAVALGT": Predicate.AUDIT_WAIVED,
    "OMFATTET_AF_LOV_OM_HVIDVASK_OG_TERRORFINANSIERING": Predicate.AML_OBLIGED_ENTITY,
    "BØRSNOTERET": Predicate.IS_LISTED,
    "GENOPTAGELSE_TVANGSOPLØSNING": Predicate.REINSTATED_AFTER_DISSOLUTION,
    "OFFENTLIG_EJERBOG": Predicate.PUBLISHES_SHAREHOLDER_REGISTER,
    "TILSYN_KATEGORI": Predicate.SUPERVISORY_CATEGORY,
}

#: Deliberately not imported, each with the reason. Reviewable by construction.
IGNORED: dict[str, str] = {
    "cvrNummer": "the subject key itself, not a claim about the subject",
    "enhedsNummer": "CVR-internal id for the company; cvrNummer is the join key",
    "enhedstype": "always VIRKSOMHED on this index",
    "deltagerRelation": "handled separately — produces ownership, voting and role claims",
    "attributter": "handled per-type; see CVR_ATTRIBUTES and IGNORED_ATTRIBUTES",
    "virksomhedMetadata": "a projection of fields carried elsewhere; specific "
                          "members are required via the meta.* keys",
    "reklamebeskyttet": "advertising-protection flag — carried as policy, not a claim",
    "dataAdgang": "access-control metadata about the record, not about the company",
    "samtId": "replication sequence id",
    "sidstIndlaest": "when CVR loaded the record; our own observed_at supersedes it",
    "sidstOpdateret": "when CVR last touched the record; our observed_at is the timestamp a report reproduces from",
    "brancheAnsvarskode": "administrative code with no counterpart in any source we compare against",
    "fejlRegistreret": "erroneous-registration flag; no comparator yet — revisit",
    "fejlVedIndlaesning": "flag that CVR's own import failed; a fact about their pipeline, not about the company",
    "fejlBeskrivelse": "load-error text, internal to CVR",
    "naermesteFremtidigeDato": "scheduling hint for CVR's own publication",
    "virkningsAktoer": "which CVR actor effected the change",
    "regNummer": "financial-institution registration number; no comparator yet",
    "postadresse": "postal address where it differs from the registered office; "
                   "not compared against anything, and §11 minimisation says "
                   "do not retain what nothing reads",
    "binavne": "secondary trading names — SHOULD be imported; see the failing "
               "test. A trading name that differs from the registered name is "
               "exactly an entity_mismatch signal (architecture.md §10)",
    "obligatoriskEmail": "mandatory digital-post address, a public-sector channel",
    "telefonNummer": "personal data with no comparator; §11 minimisation",
    "sekundaertTelefonNummer": "personal contact data with no source to compare it against; §11 says do not retain what nothing reads",
    "telefaxNummer": "personal contact data with no comparator; same minimisation rule as the phone numbers",
    "sekundaertTelefaxNummer": "personal contact data with no comparator; same minimisation rule as the phone numbers",
    "bibranche1": "secondary industry codes — no comparator yet, revisit with NACE work",
    "bibranche2": "second secondary industry code; admit with the hovedbranche/NACE comparator work, not before",
    "bibranche3": "third secondary industry code; admit with the hovedbranche/NACE comparator work, not before",
    "aarsbeskaeftigelse": "employment history; the newest figure is taken from meta",
    "kvartalsbeskaeftigelse": "quarterly employment series; the newest figure is imported from virksomhedMetadata, and the series has no comparator yet",
    "maanedsbeskaeftigelse": "monthly employment series; as with the quarterly series, only the newest figure is imported",
    "erstMaanedsbeskaeftigelse": "Erhvervsstyrelsen's monthly employment series; only the newest figure is imported",
    "fusioner": "merger records — SHOULD be imported once a comparator exists; "
                "a merger is an ownership event",
    "spaltninger": "demerger records — SHOULD be imported once a comparator exists; a demerger is an ownership event",
    "status": "legacy status list; sammensatStatus is the authoritative one",
}

IGNORED_ATTRIBUTES: dict[str, str] = {
    "EJERREGISTRERING_UNDER_5_PROCENT": "read directly by ownership_residual as "
                                        "its denominator, not as a standalone claim",
    "KAPITALKLASSER": "share-class flag; no comparator yet",
    "KAPITAL_DELVIST": "partly-paid capital flag; no comparator yet",
    "KAPITALVALUTA": "currency of the capital figure; carried on the capital claim",
    "REGNSKABSÅR_START": "accounting-year boundary, not a fact about control",
    "REGNSKABSÅR_SLUT": "end of the accounting year; a bookkeeping boundary, not a fact about control",
    "FØRSTE_REGNSKABSPERIODE_START": "start of the first accounting period; a bookkeeping boundary, not a fact about control",
    "FØRSTE_REGNSKABSPERIODE_SLUT": "end of the first accounting period; a bookkeeping boundary, not a fact about control",
    "VEDTÆGT_SENESTE": "date of the latest articles; no comparator yet",
    "PSEUDOCVRNR": "flag for a pseudo CVR number; affects joins, handled at the "
                   "adapter boundary rather than as a claim",
    # Surfaced by the corpus audit as silent drops, then triaged. Administrative
    # or without a comparator — recorded here so the decision is reviewable.
    "ARKIV_REGISTRERINGSNUMMER": "CVR's own archive reference for the filing",
    "OMLÆGNINGSPERIODE_START": "accounting-period restatement boundary",
    "OMLÆGNINGSPERIODE_SLUT": "end of an accounting-period restatement; a bookkeeping boundary, not a fact about control",
    "FINANSIELT_FORMÅL": "financial-sector purpose text; TILSYN_KATEGORI carries "
                         "the structured supervisory fact",
    "FINANSIEL_DELTYPE": "financial-sector subtype text; TILSYN_KATEGORI carries the structured supervisory fact we compare against",
    "KONCESSIONSDATO": "concession date — revisit with sector-licence work",
    "TILLADELSESDATO_FONDSMYNDIGHED": "date a foundation authority granted permission; admit with the sector-licence comparator work",
    "STIFTET_FØR_1900": "flag for founding predating CVR's date range; the "
                        "founding date itself comes from meta.stiftelsesDato",
}


@dataclass(frozen=True)
class FieldCoverage:
    path: str
    present: bool          # the document carried data here
    imported: bool         # we produced at least one claim from it
    reason: str = ""       # why not, when declared

    @property
    def silent_drop(self) -> bool:
        """Carried data, produced nothing, and nobody wrote down why."""
        return self.present and not self.imported and not self.reason


def _has_data(value: Any) -> bool:
    if value is None or value is False:
        return False
    if isinstance(value, (list, dict, str)):
        return len(value) > 0
    return True


def unwrap(payload: dict[str, Any]) -> dict[str, Any]:
    if "hits" in payload:
        hits = payload.get("hits", {}).get("hits", [])
        if not hits:
            return {}
        payload = hits[0]
    if "_source" in payload:
        payload = payload["_source"]
    return payload.get("Vrvirksomhed", payload)


def audit_company(payload: dict[str, Any], claims: Iterable[Claim]) -> list[FieldCoverage]:
    """Every field the document carries, and whether it reached the graph."""
    doc = unwrap(payload)
    produced = {c.predicate for c in claims}
    out: list[FieldCoverage] = []

    for key, value in sorted(doc.items()):
        if key == "attributter":
            continue                       # audited per-type below
        present = _has_data(value)
        if key in CVR_REQUIRED:
            out.append(FieldCoverage(key, present,
                                     imported=CVR_REQUIRED[key] in produced or not present))
        elif key in IGNORED:
            out.append(FieldCoverage(key, present, imported=False, reason=IGNORED[key]))
        else:
            out.append(FieldCoverage(key, present, imported=False))

    meta = doc.get("virksomhedMetadata") or {}
    for key, pred in CVR_REQUIRED.items():
        if not key.startswith("meta."):
            continue
        present = _has_data(meta.get(key[5:]))
        out.append(FieldCoverage(key, present, imported=pred in produced or not present))

    for attr in doc.get("attributter") or []:
        t = attr.get("type") or "?"
        present = _has_data(attr.get("vaerdier"))
        path = f"attributter.{t}"
        if t in CVR_ATTRIBUTES:
            out.append(FieldCoverage(path, present,
                                     imported=CVR_ATTRIBUTES[t] in produced or not present))
        elif t in IGNORED_ATTRIBUTES:
            out.append(FieldCoverage(path, present, imported=False,
                                     reason=IGNORED_ATTRIBUTES[t]))
        else:
            out.append(FieldCoverage(path, present, imported=False))
    return out


def silent_drops(coverage: Iterable[FieldCoverage]) -> list[FieldCoverage]:
    return [c for c in coverage if c.silent_drop]
