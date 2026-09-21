"""Punktum dk (DK Hostmaster) — the .dk registry.

Two surfaces, and the difference decides what a screen can say today.

**Anonymous WHOIS, port 43 — works now, no credential.** Returns the domain's
registration date, expiry, registrar, DNSSEC state and nameservers. It does
*not* return the holder: DK Hostmaster removed the registrant from the
anonymous service.

**RESTful WHOIS — carries the registrant, needs IP whitelisting.** The response
includes a `registrant` object with `name`, address, `email`, `phone` and
`id_status` — DK Hostmaster's identity-verification state for the holder. There
is no API key; access control is by IP, arranged with Punktum dk. Requests from
an unlisted address are refused at the transport layer.

**Why the registrant is worth the paperwork.** It is a second authoritative
register recording who holds an asset, checkable against CVR — the contradiction
engine's proper domain rather than an inference. A domain held by a person who
holds no registered function, or by a company that is not the one screened, is a
fact from a registry, not a structural guess.

`.dk` is unusual in publishing it at all; most TLDs went dark after GDPR.
"""
from __future__ import annotations

import re
import socket
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any

WHOIS_HOST = "whois.dk-hostmaster.dk"

#: Responses that are about *our access*, not about the domain. Punktum dk
#: throttles aggressively — a 199-domain run at 0.7s intervals was cut off after
#: five, and every response after that was the bare string below.
#:
#: Parsing one of these produced a DomainRecord with no registrar and no
#: registration date, which reads as "this domain has nothing on file". 193
#: companies were stored that way. A rate limit is a fact about us and must
#: never be recorded as a fact about the subject — the same rule the REST
#: client already follows for IP whitelisting.
REFUSALS = ("too many requests", "rate limit", "quota exceeded",
            "service unavailable", "access denied", "connection refused")


class Throttled(RuntimeError):
    """The registry refused us. Not a statement about the domain."""
REST_BASE = "https://whois-api.dk-hostmaster.dk"

#: Registrars whose presence indicates corporate brand protection rather than an
#: SME buying a domain. Pharmaco's .dk sits with Safenames; an ordinary Danish
#: company is on one.com or DanDomain. A scale indicator, not a risk one.
CORPORATE_REGISTRARS = re.compile(
    r"\b(?:safenames|markmonitor|cscglobal|csc corporate|com laude|nom-iq|"
    r"ascio|gandi corporate|brandshelter|ebrand)\b", re.I)


@dataclass(frozen=True)
class DomainRecord:
    """What the .dk registry says about a domain.

    `registrant_name` is None whenever the record came from anonymous WHOIS —
    absence here is a coverage fact about our access, never a fact about the
    domain, and the two must not be conflated.
    """

    domain: str
    observed_at: str
    source: str                      # 'whois' | 'rest'
    registered: date | None = None
    expires: date | None = None
    registrar: str | None = None
    dnssec: str | None = None
    status: str | None = None
    nameservers: tuple[str, ...] = ()
    registrant_name: str | None = None
    registrant_city: str | None = None
    registrant_zip: str | None = None
    registrant_country: str | None = None
    registrant_id_status: str | None = None
    raw: str = ""

    @property
    def holder_known(self) -> bool:
        return self.registrant_name is not None

    @property
    def is_evidence(self) -> bool:
        """Does this record say anything about the domain at all?

        Guards the 193 records already written from throttle responses before
        `Throttled` existed. The raw store is append-only, so they cannot be
        deleted — they are recognised and skipped on read instead.
        """
        if self.raw and is_refusal(self.raw):
            return False
        return bool(self.registered or self.registrar or self.nameservers)

    @property
    def corporate_registrar(self) -> bool:
        return bool(self.registrar and CORPORATE_REGISTRARS.search(self.registrar))

    def to_dict(self) -> dict[str, Any]:
        d = {k: v for k, v in self.__dict__.items()}
        for k in ("registered", "expires"):
            d[k] = d[k].isoformat() if d[k] else None
        d["nameservers"] = list(d["nameservers"])
        return d


def _d(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def is_refusal(text: str) -> bool:
    """Did the registry decline to answer, rather than answer with nothing?"""
    head = "\n".join(l for l in text.splitlines()[:6]
                     if l.strip() and not l.strip().startswith("#")).lower()
    return any(r in head for r in REFUSALS)


def parse_whois(text: str, domain: str) -> DomainRecord:
    """Parse the port-43 response. Comment lines start with '#'.

    Raises `Throttled` on a refusal rather than returning an empty record: an
    empty record is indistinguishable from a real domain with nothing on file,
    and storing one turns our rate limit into the subject's silence.
    """
    if is_refusal(text):
        raise Throttled(text.strip()[:80])
    fields: dict[str, str] = {}
    nameservers: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        key, val = key.strip().lower(), val.strip()
        if key == "hostname":
            nameservers.append(val.lower())
        elif key and val:
            fields.setdefault(key, val)
    return DomainRecord(
        domain=domain,
        observed_at=datetime.now(timezone.utc).isoformat(),
        source="whois",
        registered=_d(fields.get("registered")),
        expires=_d(fields.get("expires")),
        registrar=fields.get("registrar"),
        dnssec=fields.get("dnssec"),
        status=fields.get("status"),
        nameservers=tuple(sorted(set(nameservers))),
        raw=text,
    )


def parse_rest(payload: dict[str, Any], domain: str) -> DomainRecord:
    """Parse the RESTful WHOIS response, which carries the holder."""
    reg = payload.get("registrant") or {}
    ns = payload.get("nameservers") or []
    names = []
    for n in ns:
        names.append(n.get("hostname", "") if isinstance(n, dict) else str(n))
    return DomainRecord(
        domain=domain,
        observed_at=datetime.now(timezone.utc).isoformat(),
        source="rest",
        registered=_d(payload.get("createddate")),
        expires=_d(payload.get("paiduntildate")),
        registrar=(payload.get("registrar") or {}).get("name")
        if isinstance(payload.get("registrar"), dict) else payload.get("registrar"),
        dnssec=payload.get("dnssec"),
        status=payload.get("public_domain_status") or payload.get("status"),
        nameservers=tuple(sorted(n.lower() for n in names if n)),
        registrant_name=reg.get("name") or None,
        registrant_city=reg.get("city") or None,
        registrant_zip=reg.get("zipcode") or None,
        registrant_country=reg.get("countryregionid") or None,
        registrant_id_status=reg.get("id_status") or None,
    )


#: Measured, not guessed: a 199-domain run at 0.7s spacing was cut off after
#: **five** queries. Punktum dk logs every session and throttles hard.
#:
#: That constrains calibration, not the product. A screen looks up one company,
#: so one domain — the operating profile never approaches this limit. A source
#: can be perfectly usable for screening and still refuse to yield a denominator,
#: and those are separate questions that were being conflated.
OBSERVED_BURST_LIMIT = 5
DEFAULT_DELAY = 5.0


class WhoisClient:
    """Anonymous port-43 lookup. Available now, no credential, no holder.

    Paced for the operating profile: one domain per screen. `delay` exists so a
    caller that does want several is slow by default rather than by remembering.
    """

    def __init__(self, host: str = WHOIS_HOST, timeout: float = 10.0,
                 delay: float = DEFAULT_DELAY) -> None:
        self.host, self.timeout, self.delay = host, timeout, delay
        self._last = 0.0

    def lookup(self, domain: str) -> DomainRecord | None:
        elapsed = time.monotonic() - self._last
        if self._last and elapsed < self.delay:
            time.sleep(self.delay - elapsed)
        self._last = time.monotonic()
        try:
            s = socket.create_connection((self.host, 43), timeout=self.timeout)
        except OSError:
            return None
        try:
            s.sendall((domain + "\r\n").encode())
            chunks = []
            while True:
                b = s.recv(4096)
                if not b:
                    break
                chunks.append(b)
        except OSError:
            return None
        finally:
            s.close()
        text = b"".join(chunks).decode("utf-8", "replace")
        if "no entries found" in text.lower():
            return None
        return parse_whois(text, domain)          # raises Throttled on a refusal


class RestClient:
    """RESTful WHOIS. Carries the registrant; requires IP whitelisting.

    A refusal here means *our address is not listed*, which is a fact about our
    access and never about the domain — so it raises rather than returning an
    empty record that a caller might read as "no holder".
    """

    def __init__(self, http, base: str = REST_BASE, timeout: float = 20.0) -> None:
        self._http, self.base, self.timeout = http, base.rstrip("/"), timeout

    def lookup(self, domain: str) -> DomainRecord:
        r = self._http.get(f"{self.base}/domain/{domain}",
                           headers={"Accept": "application/json"},
                           timeout=self.timeout)
        if r.status_code in (401, 403):
            raise PermissionError(
                "Punktum dk refused the request — the RESTful WHOIS service is "
                "IP-whitelisted. Arrange whitelisting; there is no API key.")
        r.raise_for_status()
        return parse_rest(r.json(), domain)
