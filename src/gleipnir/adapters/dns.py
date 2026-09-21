"""DNS observations — free, instant, no auth, no legal exposure (poc.md §6.3).

**Observations, not state.** Each lookup is stored with the moment it was made
and never overwritten. An MX finding is usually a *change* — a mail provider
swapped, a record that disappeared — and a current-state table cannot see one.

The signal here is weak alone and the base-rate research says so plainly: a
quarter of genuine small EU enterprises have no website at all, and mail
provision concentrates on a handful of hosts. What is worth measuring is
**shared infrastructure across entities claiming no relationship**, and that only
means anything once the dominant providers are conditioned out.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

import dns.exception
import dns.resolver


@dataclass(frozen=True)
class DnsObservation:
    domain: str
    observed_at: str
    mx: tuple[str, ...] = ()
    ns: tuple[str, ...] = ()
    a: tuple[str, ...] = ()
    spf: bool = False
    dmarc: bool = False
    errors: tuple[str, ...] = ()

    @property
    def has_mx(self) -> bool:
        return bool(self.mx)

    @property
    def mx_hash(self) -> str:
        """Stable id for a mail configuration, so a change is detectable."""
        return hashlib.sha256("|".join(sorted(self.mx)).encode()).hexdigest()[:16]

    @property
    def ns_hash(self) -> str:
        return hashlib.sha256("|".join(sorted(self.ns)).encode()).hexdigest()[:16]

    def to_dict(self) -> dict:
        return {"domain": self.domain, "observed_at": self.observed_at,
                "mx": list(self.mx), "ns": list(self.ns), "a": list(self.a),
                "spf": self.spf, "dmarc": self.dmarc, "errors": list(self.errors)}


#: Mail providers common enough that sharing one carries no information.
#: The base-rate lesson from formation-agent addresses, applied before the fact:
#: a predicate keyed on shared infrastructure fires on everyone using Google
#: Workspace unless the provider is conditioned out.
BULK_PROVIDERS: dict[str, str] = {
    "google": r"(?:aspmx|googlemail|google)\.com$",
    "microsoft": r"(?:(?:outlook|protection\.outlook|office365|hotmail)\.com|mx\.microsoft)$",
    "one.com": r"one\.com$",
    "simply.com": r"(?:simply|unoeuro)\.com$",
    "dandomain": r"dandomain\.dk$",
    "gigahost": r"gigahost\.dk$",
    "curanet": r"(?:curanet|scannet)\.dk$",
    "mailprotect": r"mailprotect\.[a-z.]+$",
    "zoho": r"zoho(?:cloud)?\.(?:com|eu)$",
    "proofpoint": r"pphosted\.com$",
    "mimecast": r"mimecast\.com$",
    "hostnordic": r"hostnordic\.com$",
}
_COMPILED = {k: re.compile(v, re.I) for k, v in BULK_PROVIDERS.items()}


def classify_provider(mx: tuple[str, ...]) -> str:
    """Which bulk provider serves this domain, or 'other'/'none'.

    'other' is the interesting bucket: a mail host that is not one of the dozen
    everybody uses. Sharing one of those with an unrelated entity is a network
    edge; sharing Google is not.
    """
    if not mx:
        return "none"
    for host in sorted(mx):
        h = host.rstrip(".").lower()
        for name, rx in _COMPILED.items():
            if rx.search(h):
                return name
    return "other"


def domain_of(value: str) -> str:
    """Bare resolvable domain from whatever CVR actually holds in `hjemmeside`.

    Measured against 253 filed values, the field contains more than URLs, and
    every one of these produced a false "domain does not resolve":

      `name@example-mail.dk`          an email address in the website field
      `wwwexample-shop.dk`            "www" typed without its dot
      `eksempelsmøreri.dk`            an internationalised domain, which a
                                      resolver cannot query until it is punycoded

    A third of the dead-domain hits were this function's fault rather than the
    company's, which would have made a 4.8% signal out of our own parsing.
    """
    v = (value or "").strip().lower()
    if not v:
        return ""
    v = re.sub(r"^https?://", "", v)
    v = v.split("/")[0].split("?")[0].split(":")[0].strip(" .")
    if "@" in v:                       # an address, not a site
        v = v.rsplit("@", 1)[-1]
    if v.startswith("www."):
        v = v[4:]
    elif v.startswith("www") and "." in v[3:]:
        v = v[3:].lstrip(".-")         # "wwwexample-shop.dk" -> "example-shop.dk"
    if not v or "." not in v:
        return ""
    try:                               # æøå and friends must be punycoded
        v = v.encode("idna").decode("ascii")
    except (UnicodeError, UnicodeDecodeError):
        return ""
    return v


class DnsClient:
    def __init__(self, timeout: float = 4.0, lifetime: float = 6.0) -> None:
        self._r = dns.resolver.Resolver()
        self._r.timeout = timeout
        self._r.lifetime = lifetime

    def _q(self, name: str, rtype: str) -> tuple[list[str], str | None]:
        try:
            ans = self._r.resolve(name, rtype)
            return [r.to_text() for r in ans], None
        except dns.resolver.NXDOMAIN:
            return [], "NXDOMAIN"
        except dns.resolver.NoAnswer:
            return [], None                     # the name exists, this type does not
        except (dns.exception.Timeout, dns.resolver.NoNameservers) as e:
            return [], type(e).__name__
        except Exception as e:                  # noqa: BLE001 - never abort a run
            return [], type(e).__name__

    def observe(self, domain: str) -> DnsObservation:
        errors: list[str] = []
        mx_raw, err = self._q(domain, "MX")
        if err:
            errors.append(f"MX:{err}")
        # "10 aspmx.l.google.com." -> "aspmx.l.google.com"
        mx = tuple(sorted({m.split()[-1].rstrip(".").lower()
                           for m in mx_raw if m.split()}))
        ns_raw, err = self._q(domain, "NS")
        if err:
            errors.append(f"NS:{err}")
        a_raw, err = self._q(domain, "A")
        if err:
            errors.append(f"A:{err}")
        txt, _ = self._q(domain, "TXT")
        spf = any("v=spf1" in t.lower() for t in txt)
        dmarc_txt, _ = self._q(f"_dmarc.{domain}", "TXT")
        dmarc = any("v=dmarc1" in t.lower() for t in dmarc_txt)
        return DnsObservation(
            domain=domain,
            observed_at=datetime.now(timezone.utc).isoformat(),
            mx=mx,
            ns=tuple(sorted({n.rstrip(".").lower() for n in ns_raw})),
            a=tuple(sorted(a.strip() for a in a_raw)),
            spf=spf, dmarc=dmarc, errors=tuple(errors),
        )


# ─────────────────────────────────────────────────────────────────────────────
# Infrastructure jurisdiction
#
# Distinct from shared infrastructure, which the 253-company cohort showed is
# not computable at this scale. This asks a different question: **where do the
# servers sit?**
#
# It is a jurisdictional fact about a corporate technical choice — the axis
# `threat-model.md` §3 says to key on — and explicitly not a fact about any
# person. Hosting country is a property of an ASN, not of a name.
#
# Team Cymru publish IP→ASN→country over DNS: free, no credential, and designed
# for exactly this. `8.8.8.8` becomes a TXT query on
# `8.8.8.8.origin.asn.cymru.com` reversed, answering `15169 | 8.8.8.0/24 | US`.
# ─────────────────────────────────────────────────────────────────────────────

#: ASNs that terminate traffic on behalf of someone else. An A record pointing
#: at one of these says where the *edge* is and nothing about where the origin
#: is, so a country read off it is not evidence.
#:
#: Hosting providers are deliberately NOT in this set. Hetzner and OVH are where
#: the server actually is, so their country is the answer — lumping them in with
#: Cloudflare suppressed 14 legitimate DE readings in a 251-domain cohort and
#: inflated "origin unreadable" from 28% to 34%.
EDGE_ASNS: dict[str, str] = {
    "13335": "cloudflare", "209242": "cloudflare",
    "54113": "fastly", "394192": "fastly",
    "16625": "akamai", "20940": "akamai", "16509": "aws-cloudfront",
}

#: Where the machine is. Country is meaningful; the operator is not the owner.
HOSTING_ASNS: dict[str, str] = {
    "24940": "hetzner", "16276": "ovh", "14618": "aws", "8075": "azure",
    "15169": "google", "396982": "google-cloud", "13238": "yandex",
    "63949": "linode", "14061": "digitalocean", "20473": "vultr",
}

#: Kept for callers that only care whether the country is readable.
CDN_ASNS = EDGE_ASNS

@dataclass(frozen=True)
class NetBlock:
    ip: str
    asn: str = ""
    country: str = ""
    registry: str = ""
    prefix: str = ""

    @property
    def is_cdn(self) -> bool:
        """Does this ASN mask the origin? Hosting providers do not."""
        return self.asn in EDGE_ASNS

    @property
    def provider(self) -> str:
        return EDGE_ASNS.get(self.asn) or HOSTING_ASNS.get(self.asn, "")

    @property
    def hosted_by(self) -> str:
        return HOSTING_ASNS.get(self.asn, "")


class AsnClient:
    """IP → ASN and country, over Team Cymru's DNS interface."""

    def __init__(self, timeout: float = 4.0) -> None:
        self._r = dns.resolver.Resolver()
        self._r.timeout = timeout
        self._r.lifetime = timeout + 2
        self._cache: dict[str, NetBlock] = {}

    def lookup(self, ip: str) -> NetBlock:
        if ip in self._cache:
            return self._cache[ip]
        rev = ".".join(reversed(ip.split(".")))
        block = NetBlock(ip=ip)
        try:
            ans = self._r.resolve(f"{rev}.origin.asn.cymru.com", "TXT")
            parts = [p.strip() for p in ans[0].to_text().strip('"').split("|")]
            if len(parts) >= 4:
                block = NetBlock(ip=ip, asn=parts[0].split()[0], prefix=parts[1],
                                 country=parts[2].upper(), registry=parts[3])
        except Exception:                      # noqa: BLE001 - never abort a run
            pass
        self._cache[ip] = block
        return block

    def resolve_host(self, host: str) -> list[str]:
        try:
            return [r.to_text() for r in self._r.resolve(host, "A")]
        except Exception:                      # noqa: BLE001
            return []


def jurisdictions(obs: DnsObservation, asn: AsnClient) -> dict[str, object]:
    """Where this domain's web and mail infrastructure sits.

    Mail and web are reported separately and deliberately not merged: a site
    behind a CDN tells you nothing about its origin, while its MX almost always
    resolves to the host actually receiving the mail.
    """
    web = [asn.lookup(ip) for ip in obs.a]
    mail: list[NetBlock] = []
    for host in obs.mx:
        for ip in asn.resolve_host(host):
            mail.append(asn.lookup(ip))
    return {
        "web_countries": sorted({b.country for b in web if b.country}),
        "web_cdn": sorted({b.provider for b in web if b.is_cdn}),
        "web_hosting": sorted({b.hosted_by for b in web if b.hosted_by}),
        "mail_countries": sorted({b.country for b in mail if b.country}),
        "mail_asns": sorted({b.asn for b in mail if b.asn}),
        "web_behind_cdn": bool(web) and all(b.is_cdn for b in web),
    }
