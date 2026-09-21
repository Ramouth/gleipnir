"""OpenSanctions — the definition of the target (poc.md §6.2).

A designation nexus is *what concealed hostile-state ownership means*. Without
this source there is no detector, only a shell-company finder — so this is not
late enrichment, it is Tier A's other half.

**We use the bulk `sanctions` collection, not the match API.** 291k entities,
68 MB as `targets.simple.csv`, no credential. Bulk over API because every
Tier A and Tier B predicate walks a whole ownership chain against the full list
at once, and doing that as N round-trips would be slow, rate-limited and
pointless when the list fits in memory.

**Licence.** The free tier is non-commercial (`sources.Licence.NON_COMMERCIAL`).
Analysis is fine; a report sold to a bank quoting it is not. `redistributable()`
enforces that at report generation — budget the commercial licence before
revenue.

**Provenance reaches the authority, not the aggregator.** A hit is never
"OpenSanctions says designated". It is "EU Consolidated List, per OpenSanctions
export of <date>", and the `datasets` field carries that. Aggregators launder
provenance and a report citing one is a report you cannot defend.
"""
from __future__ import annotations

import csv
import io
import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

import httpx

INDEX_URL = "https://data.opensanctions.org/datasets/latest/sanctions/index.json"
RESOURCE = "targets.simple.csv"



#: Programme prefixes that constitute a *designation* for this threat model.
#:
#: The bulk dataset is not a designation list. It aggregates every restrictive
#: measure anyone publishes — including sanctions issued **by** Russia (2,141
#: programme entries), China (476) and Iran (405) against Western targets, and
#: non-sanctions regulatory listings such as `EU-ESMA`.
#:
#: Screening a Danish company against the unfiltered list is not merely noisy,
#: it inverts the signal. Observed in the live export: the Alliance of
#: Democracies Foundation — a Danish NGO — appears because China sanctioned it.
#: Treating that as a designation nexus would flag a democracy foundation as
#: sanctions-linked. Being designated by the adversary is, if anything,
#: exculpatory here.
#:
#: Allowlist rather than blocklist: a new issuing country appearing in a future
#: export must fail closed, not silently become a red flag.
DESIGNATING_ISSUERS: frozenset[str] = frozenset({
    "EU",      # EU Council restrictive measures
    "UN",      # UN Security Council
    "US",      # OFAC and related US programmes
    "GB",      # UK OFSI
    "CA", "AU", "NZ", "JP", "CH", "SECO",   # allied regimes
    "UA",      # Ukrainian sanctions — aligned, but see caveat below
})

#: Programmes that are listings but not sanctions, and must never colour a
#: finding. `EU-ESMA` is a securities-regulator register; a real Danish company
#: (EXAMPLETEK) appears under it in the live export.
NON_SANCTION_PROGRAMMES: frozenset[str] = frozenset({"EU-ESMA"})


def is_designation(programs: tuple[str, ...]) -> bool:
    """Does this target carry at least one programme we treat as a designation?

    UA is included because Ukrainian listings are aligned with the threat model,
    but they are far broader and less adjudicated than EU/OFAC ones — a UA-only
    hit should be reported as a UA-only hit, never as "sanctioned", and it does
    not by itself satisfy the Tier A legal test.
    """
    for p in programs:
        if p in NON_SANCTION_PROGRAMMES:
            continue
        if p.split("-", 1)[0] in DESIGNATING_ISSUERS:
            return True
    return False


@dataclass(frozen=True)
class Target:
    """One sanctioned entity, as OpenSanctions renders it."""

    id: str
    schema: str                 # Person | Organization | Company | Vessel | ...
    name: str
    aliases: tuple[str, ...]
    countries: tuple[str, ...]
    identifiers: tuple[str, ...]
    datasets: tuple[str, ...]   # the originating authorities — cite these
    programs: tuple[str, ...]
    first_seen: str | None
    last_change: str | None

    @property
    def authorities(self) -> str:
        return "; ".join(self.datasets)

    @property
    def designating_programmes(self) -> tuple[str, ...]:
        """Only the programmes that count as a designation here."""
        return tuple(
            p for p in self.programs
            if p not in NON_SANCTION_PROGRAMMES
            and p.split("-", 1)[0] in DESIGNATING_ISSUERS
        )

    @property
    def is_designated(self) -> bool:
        return bool(self.designating_programmes)

    @property
    def adversary_issued_only(self) -> bool:
        """Listed *only* by RU/CN/IR. Not a red flag — arguably the opposite."""
        issuers = {p.split("-", 1)[0] for p in self.programs}
        return bool(issuers) and issuers <= {"RU", "CN", "IR"}


def normalise_name(value: str) -> str:
    """Casefold, strip diacritics and punctuation, collapse whitespace.

    Deliberately crude. This is a *blocking* key for candidate generation, not
    a matching decision — architecture.md §6 puts person matching in tier 3 and
    routes each candidate through the adjudication engine. Transliteration
    (Ivanov / Ivanoff / Иванов) is exactly what this does not solve, and
    pretending otherwise here would let a bad merge through silently.
    """
    s = unicodedata.normalize("NFKD", value)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[^\w\s]", " ", s.casefold())
    return re.sub(r"\s+", " ", s).strip()


def normalise_identifier(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "", value).upper()


class SanctionsIndex:
    """In-memory lookup over the bulk export.

    Two indexes: normalised name (including aliases) and normalised
    identifier. The identifier index is the valuable one — a registration
    number match is deterministic, needs no adjudication, and is the Tier A
    path. Name matches are candidates only.
    """

    def __init__(self, targets: list[Target], *, designations_only: bool = True) -> None:
        """`designations_only` defaults to True and should stay that way.

        The unfiltered list includes adversary-issued sanctions and regulatory
        registers; see `DESIGNATING_ISSUERS`. Pass False only to analyse the
        raw export, never to screen a counterparty.
        """
        self.excluded = [t for t in targets if designations_only and not t.is_designated]
        self.targets = [t for t in targets if not designations_only or t.is_designated]
        self._by_name: dict[str, list[Target]] = defaultdict(list)
        self._by_ident: dict[str, list[Target]] = defaultdict(list)
        # Built from the *filtered* list: an excluded target must be
        # unreachable by every lookup path, not merely absent from `.targets`.
        for t in self.targets:
            for n in (t.name, *t.aliases):
                if n:
                    self._by_name[normalise_name(n)].append(t)
            for ident in t.identifiers:
                if not ident:
                    continue
                key = normalise_identifier(ident)
                self._by_ident[key].append(t)
                # Identifiers arrive prefixed ("IMO9000001", "DK12345678") and
                # are looked up bare (a CVR number off a registry record), so
                # index the digits-only form too. Bare-number collisions are
                # possible across schemes, which is why a hit still carries its
                # full identifier list for the caller to check.
                digits = re.sub(r"\D", "", ident)
                if digits and digits != key:
                    self._by_ident[digits].append(t)

    def __len__(self) -> int:
        return len(self.targets)

    def by_identifier(self, value: str, *, country: str | None = None) -> list[Target]:
        """Match on a registration number. **`country` is not optional in
        practice**, and omitting it on a bare numeric lookup is a bug.

        An identifier is only deterministic within its own scheme. Measured
        against 41,302 real Danish CVR numbers and 21,953 designated legal
        entities: **six collide** — a Serbian company, an Emirati company, a
        Russian company, a Colombian person and two Iranian persons whose
        national registration numbers are the same eight digits as a Danish
        company's, and which pass the Danish mod-11 checksum by coincidence
        (roughly one in eleven 8-digit strings does).

        That is 1 in 6,900, which at screening volume means reporting a Danish
        company as designated because an unrelated foreign number matched. It is
        precisely the false merge `architecture.md` §6 calls the worst output
        this system can produce.

        So a bare-number lookup filters on the target's own jurisdiction. A
        prefixed identifier (`IMO…`, `DK…`) carries its scheme and needs no
        filter.
        """
        key = normalise_identifier(value)
        hits = list(self._by_ident.get(key, ()))
        if country is None or not key.isdigit():
            return hits
        c = country.lower()
        return [t for t in hits
                if c in {x.lower() for x in t.countries}
                or any(normalise_identifier(i).lower().startswith(c)
                       for i in t.identifiers)]

    def by_name(self, value: str) -> list[Target]:
        """Candidates, never a conclusion. Feed to adjudication."""
        return list(self._by_name.get(normalise_name(value), ()))

    @classmethod
    def from_csv(cls, path: Path, **kw) -> "SanctionsIndex":
        with path.open("r", encoding="utf-8", newline="") as fh:
            return cls([_row_to_target(r) for r in csv.DictReader(fh)], **kw)

    @classmethod
    def from_bytes(cls, data: bytes, **kw) -> "SanctionsIndex":
        reader = csv.DictReader(io.StringIO(data.decode("utf-8")))
        return cls([_row_to_target(r) for r in reader], **kw)


def _split(value: str | None) -> tuple[str, ...]:
    return tuple(p.strip() for p in (value or "").split(";") if p.strip())


def _row_to_target(row: dict[str, str]) -> Target:
    return Target(
        id=row.get("id", ""),
        schema=row.get("schema", ""),
        name=row.get("name", ""),
        aliases=_split(row.get("aliases")),
        countries=_split(row.get("countries")),
        identifiers=_split(row.get("identifiers")),
        datasets=_split(row.get("dataset")),
        programs=_split(row.get("program_ids")),
        first_seen=row.get("first_seen") or None,
        last_change=row.get("last_change") or None,
    )


def resolve_resource_url(client: httpx.Client | None = None) -> tuple[str, str]:
    """Return (url, version) for the current bulk export.

    The artifact URL carries a build timestamp, so it changes on every refresh.
    Resolving it each time — rather than pinning — is what keeps a screen from
    silently running against a stale designation list, which for a timing
    predicate is the difference between a finding and a miss.
    """
    owns_client = client is None
    client = client or httpx.Client(timeout=60.0, follow_redirects=True)
    try:
        index = client.get(INDEX_URL).raise_for_status().json()
        url = next(r["url"] for r in index["resources"] if r["name"] == RESOURCE)
        return url, index.get("last_change") or index.get("version", "")
    finally:
        if owns_client:
            client.close()


def download(url: str, chunk_size: int = 1 << 20) -> Iterator[bytes]:
    with httpx.Client(timeout=300.0, follow_redirects=True) as client:
        with client.stream("GET", url) as resp:
            resp.raise_for_status()
            yield from resp.iter_bytes(chunk_size)
