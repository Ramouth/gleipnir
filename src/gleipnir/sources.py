"""The source registry — what we may fetch, and under what terms.

`architecture.md` §11 requires per-source licence terms to be *data*, not prose
in a README, so redistribution compliance can be checked programmatically when a
report is generated. This is that table.

Two fields do real work at report time:

`licence` decides whether a claim's underlying value may be **redistributed** to
a client, as opposed to merely informing a finding. Most registries permit
lookup and analysis while forbidding bulk redistribution, and OpenSanctions'
free tier is non-commercial — a report sold to a bank cannot quote it without a
licence.

`auth` decides what a missing credential means. A source that is
`AuthMode.NONE` and fails is broken; a source that is `AuthMode.REGISTER` and
fails is simply not set up yet, and the difference should never reach the
analyst as the same message.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class AuthMode(StrEnum):
    NONE = "none"                  # open, no credential
    REGISTER = "register"          # free but requires signing up
    AGREEMENT = "agreement"        # requires accepting terms / an agreement
    COMMERCIAL = "commercial"      # paid


class Licence(StrEnum):
    OPEN = "open"                          # redistributable
    ATTRIBUTION = "attribution"            # redistributable with credit
    NON_COMMERCIAL = "non_commercial"      # analysis yes, paid report no
    LOOKUP_ONLY = "lookup_only"            # no bulk redistribution
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class SourceSpec:
    id: str
    name: str
    base_url: str
    auth: AuthMode
    licence: Licence
    #: A cheap request proving the source answers. Kept alongside the spec so
    #: `scripts/check_sources.py` never has to hardcode endpoint knowledge.
    health_path: str | None = None
    health_method: str = "GET"
    notes: str = ""
    #: Which epistemic tier claims from this source default to. Per-field
    #: overrides live in the extractor — one CVR document carries three tiers.
    default_tier: str = "registered"


REGISTRY: dict[str, SourceSpec] = {s.id: s for s in [
    # ── Connected, working ────────────────────────────────────────────────
    SourceSpec(
        id="cvr", name="CVR (Erhvervsstyrelsen)",
        base_url="http://distribution.virk.dk",
        auth=AuthMode.AGREEMENT, licence=Licence.LOOKUP_ONLY,
        health_path="/cvr-permanent/virksomhed/_search", health_method="POST",
        notes="System-til-system. Quota is per request, not per record — batch statistics.",
    ),
    SourceSpec(
        id="regnskaber", name="Virk offentliggørelser (annual reports)",
        base_url="http://distribution.virk.dk",
        auth=AuthMode.NONE, licence=Licence.LOOKUP_ONLY,
        health_path="/offentliggoerelser/_search", health_method="POST",
        notes="Unauthenticated, 6.4M publications. XBRL documents behind each.",
        default_tier="filed",
    ),
    SourceSpec(
        id="gleif", name="GLEIF LEI (incl. Level 2 parents)",
        base_url="https://api.gleif.org/api/v1",
        auth=AuthMode.NONE, licence=Licence.OPEN,
        health_path="/lei-records?page%5Bsize%5D=1",
        notes="Authoritative cross-border ownership. Trust above our own matching (§6 tier 2).",
    ),
    SourceSpec(
        id="brreg", name="Brønnøysundregistrene (NO)",
        base_url="https://data.brreg.no/enhetsregisteret/api",
        auth=AuthMode.NONE, licence=Licence.OPEN,
        health_path="/enheter?size=1",
        notes="Chains leaving Denmark northward do not have to go opaque.",
    ),
    SourceSpec(
        id="prh", name="PRH / YTJ (FI)",
        base_url="https://avoindata.prh.fi/opendata-ytj-api/v3",
        auth=AuthMode.NONE, licence=Licence.OPEN,
        health_path="/companies?limit=1",
        notes="Beneficial owners restricted; entity data good.",
    ),
    SourceSpec(
        id="dawa", name="Danmarks Adresser (DAWA)",
        base_url="https://api.dataforsyningen.dk",
        auth=AuthMode.NONE, licence=Licence.OPEN,
        health_path="/adresser?q=Havnegade&per_side=1",
        notes="Address normalisation — the join key for registered-address clustering.",
    ),
    SourceSpec(
        id="openalex", name="OpenAlex (publications)",
        base_url="https://api.openalex.org",
        auth=AuthMode.NONE, licence=Licence.OPEN,
        health_path="/works?per-page=1",
        notes="Connected. Dated affiliations with ROR ids, country and institution "
              "type, plus co-authorship. One call answers both the university and "
              "the company axis. Denominator measured: 11,290 DK+CN works since "
              "2024-01-01, so co-affiliation is ordinary and is a chain fact, "
              "never a colour. Coverage is the limit — most company officers do "
              "not publish, so empty is the default and means nothing.",
        default_tier="third_party",
    ),
    SourceSpec(
        id="opensanctions_bulk", name="OpenSanctions (bulk, sanctions collection)",
        base_url="https://data.opensanctions.org",
        auth=AuthMode.NONE, licence=Licence.NON_COMMERCIAL,
        health_path="/datasets/latest/sanctions/index.json",
        notes="291k entities, 353MB FtM. NON-COMMERCIAL — licence needed before revenue.",
    ),
    SourceSpec(
        id="ofac_sdn", name="OFAC SDN list",
        base_url="https://sanctionslistservice.ofac.treas.gov",
        auth=AuthMode.NONE, licence=Licence.OPEN,
        health_path="/api/PublicationPreview/exports/SDN.XML",
        notes="Primary authority. Cite this, not an aggregator that republishes it.",
    ),
    SourceSpec(
        id="eu_fsf", name="EU Consolidated Financial Sanctions List",
        base_url="https://webgate.ec.europa.eu",
        auth=AuthMode.NONE, licence=Licence.OPEN,
        health_path="/fsd/fsf/public/files/xmlFullSanctionsList_1_1/content?token=dG9rZW4tMjAxNw",
        notes="Public token is documented, not a secret. Primary EU authority.",
    ),

    SourceSpec(
        id="wayback", name="Internet Archive (CDX + snapshots)",
        base_url="http://web.archive.org",
        auth=AuthMode.NONE, licence=Licence.ATTRIBUTION,
        health_path="/cdx/search/cdx?url=dr.dk&output=json&limit=1",
        notes="CDX rows carry a content digest, so collapse=digest returns a "
              "page's change timeline in one request. The only source for what "
              "a company's own account of itself said BEFORE an event — which "
              "is what poc.md §3's backtest requires. Slow (~17s) and "
              "rate-limited; cache hard.",
        default_tier="self_declared",
    ),
    SourceSpec(
        id="dkhm_whois", name="Punktum dk / DK Hostmaster (anonymous WHOIS)",
        base_url="http://whois.dk-hostmaster.dk",
        auth=AuthMode.NONE, licence=Licence.LOOKUP_ONLY,
        health_path=None,
        notes="Port 43. Registration date, expiry, registrar, DNSSEC, nameservers. "
              "Does NOT carry the holder — that is the REST service. "
              "THROTTLES HARD: measured at ~5 queries before 'Too many requests'. "
              "Fine for screening (one domain per company); unusable for bulk "
              "calibration, so its facts are reportable but unrated.",
    ),

    # ── Reachable, needs registration ─────────────────────────────────────
    SourceSpec(
        id="dkhm_rest", name="Punktum dk RESTful WHOIS (carries the registrant)",
        base_url="https://whois-api.dk-hostmaster.dk",
        auth=AuthMode.AGREEMENT, licence=Licence.LOOKUP_ONLY,
        health_path="/domain/dk-hostmaster.dk",
        notes="IP-whitelisted, no API key — arrange with Punktum dk. Returns the "
              "registrant name, address and id_status. A second authoritative "
              "register of who holds an asset, checkable against CVR.",
    ),
    SourceSpec(
        id="statstidende", name="Statstidende (Danish official gazette)",
        base_url="https://api.statstidende.dk",
        auth=AuthMode.REGISTER, licence=Licence.UNKNOWN,
        health_path="/messages?page=1",
        notes="API exists and returns 401. Konkurs/tvangsopløsning notices, dated. "
              "Registered tier, free, high value — worth the paperwork.",
    ),
    SourceSpec(
        id="epo_ops", name="EPO Open Patent Services",
        base_url="https://ops.epo.org/3.2/rest-services",
        auth=AuthMode.REGISTER, licence=Licence.ATTRIBUTION,
        health_path="/published-data/publication/epodoc/EP1000000/biblio",
        notes="OAuth2, connected. 4GB/week free. Corroborates a self-declared "
              "claim to hold patents; INPADOC legal events carry chain of title. "
              "Absence of filings is the default and means nothing on its own.",
    ),
    SourceSpec(
        id="orcid", name="ORCID (public record)",
        base_url="https://pub.orcid.org/v3.0",
        auth=AuthMode.NONE, licence=Licence.OPEN,
        health_path="/0000-0002-1825-0097/record",
        notes="Self-asserted employment, education and peer-review activity, "
              "keyed on an ORCID iD — identifier strength where present. Peer "
              "review records name the journal reviewed for, never who reviewed "
              "whom, and are self-selected: absence proves nothing.",
        default_tier="self_declared",
    ),
    SourceSpec(
        id="ee_ariregister", name="Estonia e-Äriregister (open data)",
        base_url="https://avaandmed.ariregister.rik.ee",
        auth=AuthMode.NONE, licence=Licence.ATTRIBUTION,
        health_path="/sites/default/files/avaandmed/"
                    "ettevotja_rekvisiidid__osanikud.json.zip",
        notes="Free bulk JSON/XML, daily, no credential. 376,826 companies; "
              "shareholders carry holding size on 100% of records and validity "
              "dates. Company-to-company edges join at identifier strength "
              "(44,894 legal-entity holders). Person-level matching does NOT "
              "work: UBO identifiers were stripped after the 2025 restriction, "
              "and name matching is Ivanov/Petrov collision — see "
              "docs/jurisdictions.md for the measurement.",
    ),
    SourceSpec(
        id="gr_gemi", name="Greece ΓΕΜΗ / GEMI",
        base_url="https://publicity.businessportal.gr",
        auth=AuthMode.REGISTER, licence=Licence.UNKNOWN,
        notes="Open-data API exists but is undocumented and its access tier is "
              "aimed at public bodies and AML-obliged entities. Shareholders for "
              "some legal forms only. Second candidate after Estonia.",
    ),
    SourceSpec(
        id="tr_mersis", name="Turkey MERSİS / Ticaret Sicili Gazetesi",
        base_url="https://www.ticaretsicil.gov.tr",
        auth=AuthMode.AGREEMENT, licence=Licence.UNKNOWN,
        notes="No API and no bulk data. Free gazette search is Turkish-only; a "
              "certified extract requires attendance in person or a notarised "
              "power of attorney. Recorded as having no programmatic route "
              "rather than left as an open intention.",
    ),
    SourceSpec(
        id="opensanctions_api", name="OpenSanctions match API",
        base_url="https://api.opensanctions.org",
        auth=AuthMode.REGISTER, licence=Licence.NON_COMMERCIAL,
        health_path="/search/default?q=test&limit=1",
        notes="Only needed if the bulk dataset proves too coarse for matching.",
    ),
]}


def by_auth(mode: AuthMode) -> list[SourceSpec]:
    return [s for s in REGISTRY.values() if s.auth is mode]


def redistributable(source_id: str) -> bool:
    """May a claim's underlying value be quoted in a client-facing report?

    Deliberately conservative: UNKNOWN is treated as no. A source whose terms
    nobody has read is not a source whose terms permit resale.
    """
    spec = REGISTRY.get(source_id)
    return bool(spec and spec.licence in (Licence.OPEN, Licence.ATTRIBUTION))
