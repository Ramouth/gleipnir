"""Estonia's e-Äriregister — the register a Danish CV keeps pointing at.

`docs/jurisdictions.md` measured why this one and not the others: free bulk
download, no credential, no rate limit, 376,826 companies, and shareholders
carrying a holding size on 100% of records with validity dates. It is a
structural analogue of Denmark's *ejerregister*, which is what lets `chain.py`
and the predicates run on it unchanged.

**Bulk, not lookup.** There is no free per-company endpoint, so the shape is:
fetch a whole dataset once, keep it in the raw store under its hash, and read it
there. A dataset is published daily; a re-fetch that returns identical bytes
costs one blob and one log line, which is exactly what the store is for.

**Read by streaming, always.** The shareholder file parses to 2.8 GB with
`json.loads`. `jsonstream.iter_json_array` reads it in 45 MB, and every reader
here goes through it.

**Targeted passes, not a resident index.** A screen asks about a handful of
Estonian companies named in somebody's CV, so `lookup` makes one pass and keeps
only what was asked for. Holding 376,826 companies in memory to answer three
questions is how a workbench becomes unusable on a laptop.

**What it will not give you.** Beneficial-owner identifiers were stripped after
Estonia restricted UBO access in 2025: the public extract carries 0% personal
IDs and 2.5% dates of birth. Company-to-company edges join at identifier
strength; person-level identification does not, and is reported as a coverage
statement rather than attempted by name.
"""
from __future__ import annotations

import csv
import io
import logging
import zipfile
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Iterable, Iterator

import httpx

from gleipnir.jsonstream import iter_json_array

log = logging.getLogger(__name__)

BASE = "https://avaandmed.ariregister.rik.ee"
PATH = "/sites/default/files/avaandmed/ettevotja_rekvisiidid__{dataset}.{fmt}.zip"

#: The datasets this project reads, and why each is here. The register
#: publishes more; these are the ones a chain needs.
DATASETS: dict[str, str] = {
    #: Identity: name, registry code, legal form, status, first registration.
    "lihtandmed": "csv",
    #: Ownership: holders with holding size and validity dates.
    "osanikud": "json",
    #: Beneficial owners. Kept for completeness; identifiers were stripped in
    #: 2025 and it is close to unusable for identification.
    "kasusaajad": "json",
}

SOURCE_ID = "ee_ariregister"
COUNTRY = "EE"

#: Estonian registry status codes. `R` is the live one.
STATUS_REGISTERED = "R"


class EeError(RuntimeError):
    """An upstream failure fetching a dataset."""


@dataclass(frozen=True)
class EeResponse:
    body: bytes
    http_status: int
    query: dict[str, Any]


@dataclass(frozen=True)
class Company:
    """One Estonian company, as the register records it."""

    code: str                    # ariregistri_kood — the identifier
    name: str
    legal_form: str = ""
    status: str = ""
    status_text: str = ""
    registered_on: date | None = None
    vat_number: str = ""
    address: str = ""

    @property
    def live(self) -> bool:
        return self.status == STATUS_REGISTERED


@dataclass(frozen=True)
class Holder:
    """One shareholder of an Estonian company, with the period it held."""

    #: `F` natural person, `J` legal entity. The distinction is the whole point:
    #: a legal-entity holder carries a registry code and continues the chain.
    person_type: str
    name: str
    #: Estonian registry or personal code. Present on ~10% of records.
    code: str = ""
    foreign_country: str = ""
    share_percent: str = ""
    valid_from: date | None = None
    valid_to: date | None = None

    @property
    def is_company(self) -> bool:
        return self.person_type == "J"

    @property
    def resolvable(self) -> bool:
        """Can this holder be followed? Only with an identifier."""
        return bool(self.code) and self.is_company


def _ee_date(value: str | None) -> date | None:
    """Estonian dates are `DD.MM.YYYY`."""
    if not value:
        return None
    parts = str(value).strip().split(".")
    if len(parts) != 3:
        return None
    try:
        return date(int(parts[2]), int(parts[1]), int(parts[0]))
    except ValueError:
        return None


class EeClient:
    """Fetches whole datasets. Knows nothing about claims or the graph."""

    def __init__(self, *, base_url: str = BASE, timeout: float = 300.0) -> None:
        self._c = httpx.Client(base_url=base_url, timeout=timeout,
                               follow_redirects=True,
                               headers={"User-Agent": "GleipnirBot/0.1 "
                                                      "(+corporate screening)"})

    def url_for(self, dataset: str) -> str:
        try:
            fmt = DATASETS[dataset]
        except KeyError:
            raise EeError(f"unknown dataset {dataset!r}; "
                          f"known: {sorted(DATASETS)}") from None
        return PATH.format(dataset=dataset, fmt=fmt)

    def fetch(self, dataset: str) -> EeResponse:
        """Download one dataset whole. Tens of megabytes; call it rarely."""
        r = self._c.get(self.url_for(dataset))
        if r.status_code >= 500:
            raise EeError(f"e-Äriregister returned {r.status_code} for {dataset}")
        return EeResponse(body=r.content, http_status=r.status_code,
                          query={"dataset": dataset})

    def stream(self, dataset: str) -> Iterator[bytes]:
        """Chunks of one dataset, for `RawStore.put_stream`."""
        with self._c.stream("GET", self.url_for(dataset)) as r:
            if r.status_code != 200:
                raise EeError(f"e-Äriregister returned {r.status_code} for {dataset}")
            yield from r.iter_bytes(1 << 20)

    def close(self) -> None:
        self._c.close()

    def __enter__(self) -> "EeClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def _member(store, dataset: str):
    """The stored zip for one dataset, opened on its single member."""
    rec = store.latest(SOURCE_ID, dataset, dataset)
    if rec is None:
        return None, None
    archive = zipfile.ZipFile(store.path_of(rec.content_hash))
    return archive, rec.content_hash


def _normalise(name: str) -> str:
    return " ".join((name or "").split()).casefold()


def lookup(store, *, names: Iterable[str] = (), codes: Iterable[str] = ()
           ) -> tuple[dict[str, Company], str]:
    """Resolve Estonian companies by name or code in one pass.

    Returns the companies found, keyed by registry code, and the content hash
    of the dataset they were read from — so every claim built from them can
    cite the exact bytes.

    One pass over 96 MB, keeping only what was asked for. A screen asks about
    the handful of companies a CV named, and that is what this is sized for.
    """
    wanted_names = {_normalise(n) for n in names if n}
    wanted_codes = {str(c).strip() for c in codes if c}
    if not (wanted_names or wanted_codes):
        return {}, ""

    archive, blob = _member(store, "lihtandmed")
    if archive is None:
        return {}, ""

    found: dict[str, Company] = {}
    with archive.open(archive.namelist()[0]) as raw:
        text = io.TextIOWrapper(raw, encoding="utf-8-sig", newline="")
        for row in csv.DictReader(text, delimiter=";"):
            code = (row.get("ariregistri_kood") or "").strip()
            name = row.get("nimi") or ""
            if code not in wanted_codes and _normalise(name) not in wanted_names:
                continue
            found[code] = Company(
                code=code, name=name,
                legal_form=row.get("ettevotja_oiguslik_vorm") or "",
                status=(row.get("ettevotja_staatus") or "").strip(),
                status_text=row.get("ettevotja_staatus_tekstina") or "",
                registered_on=_ee_date(row.get("ettevotja_esmakande_kpv")),
                vat_number=(row.get("kmkr_nr") or "").strip(),
                address=row.get("ads_normaliseeritud_taisaadress")
                or row.get("asukoha_ehak_tekstina") or "",
            )
    return found, blob


def holders(store, codes: Iterable[str]) -> tuple[dict[str, list[Holder]], str]:
    """Shareholders of the given companies, in one streaming pass.

    The shareholder file is the one that parses to 2.8 GB whole, so it is read
    through `jsonstream` and only the requested companies are retained.
    """
    wanted = {str(c).strip() for c in codes if c}
    if not wanted:
        return {}, ""

    archive, blob = _member(store, "osanikud")
    if archive is None:
        return {}, ""

    out: dict[str, list[Holder]] = {}
    with archive.open(archive.namelist()[0]) as raw:
        for record in iter_json_array(raw):
            code = str(record.get("ariregistri_kood") or "").strip()
            if code not in wanted:
                continue
            out[code] = [
                Holder(
                    person_type=(o.get("isiku_tyyp") or "").strip(),
                    name=" ".join(p for p in ((o.get("eesnimi") or "").strip(),
                                              (o.get("nimi_arinimi") or "").strip())
                                  if p),
                    code=str(o.get("isikukood_registrikood") or "").strip(),
                    foreign_country=(o.get("valis_kood_riik") or "").strip(),
                    share_percent=str(o.get("osaluse_protsent") or "").strip(),
                    valid_from=_ee_date(o.get("algus_kpv")),
                    valid_to=_ee_date(o.get("lopp_kpv")),
                )
                for o in record.get("osanikud") or []
            ]
            if len(out) == len(wanted):
                break        # everything asked for has been found
    return out, blob
