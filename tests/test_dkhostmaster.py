"""The .dk registry: two surfaces, and the difference between them matters."""
from datetime import date

import pytest

from gleipnir.adapters.dkhostmaster import (
    DomainRecord, RestClient, parse_rest, parse_whois,
)

WHOIS = """\
# Hello 1.2.3.4. Your session has been logged.
# Copyright (c) 2002 - 2026 by Punktum dk A/S
#
Domain:               pharmaco.example
DNS:                  pharmaco.example
Registered:           2005-02-08
Expires:              2027-02-28
Registrar:            Safenames Ltd
Registration period:  1 year
DNSSEC:               Unsigned delegation
Status:               Active
Nameservers
Hostname:             dns1.examplehost.net
Hostname:             DNS2.EXAMPLEHOST.NET
"""

REST = {
    "domain": "example.dk", "createddate": "2011-04-01",
    "paiduntildate": "2027-04-30", "dnssec": "Signed delegation",
    "public_domain_status": "Active", "registrar": {"name": "One.com A/S"},
    "nameservers": [{"hostname": "ns1.one.com"}, {"hostname": "NS2.ONE.COM"}],
    "registrant": {"name": "Acme ApS", "city": "København", "zipcode": "1058",
                   "countryregionid": "DK", "id_status": "verified",
                   "email": "x@example.dk", "phone": "+45"},
}


# ── anonymous WHOIS ─────────────────────────────────────────────────────────

def test_whois_parses_the_facts_it_does_carry():
    r = parse_whois(WHOIS, "pharmaco.example")
    assert r.registered == date(2005, 2, 8)
    assert r.expires == date(2027, 2, 28)
    assert r.registrar == "Safenames Ltd"
    assert r.status == "Active"
    assert r.nameservers == ("dns1.examplehost.net", "dns2.examplehost.net")


def test_comment_lines_are_not_parsed_as_fields():
    r = parse_whois(WHOIS, "pharmaco.example")
    assert r.raw.startswith("# Hello")
    assert not any(k in (r.registrar or "") for k in ("Hello", "Copyright"))


def test_an_absent_holder_is_a_fact_about_our_access_not_the_domain():
    """DK Hostmaster removed the registrant from anonymous WHOIS. Absence here
    must never read as 'this domain has no holder'."""
    r = parse_whois(WHOIS, "pharmaco.example")
    assert r.registrant_name is None
    assert r.holder_known is False
    assert r.source == "whois"


def test_a_corporate_brand_registrar_is_recognised():
    """Pharmaco's .dk sits with Safenames; an ordinary Danish SME is on one.com
    or DanDomain. A scale indicator, not a risk one."""
    assert parse_whois(WHOIS, "pharmaco.example").corporate_registrar is True
    ordinary = parse_whois(WHOIS.replace("Safenames Ltd", "One.com A/S"), "x.dk")
    assert ordinary.corporate_registrar is False


# ── RESTful WHOIS ───────────────────────────────────────────────────────────

def test_rest_carries_the_registrant():
    r = parse_rest(REST, "example.dk")
    assert r.holder_known and r.registrant_name == "Acme ApS"
    assert r.registrant_zip == "1058" and r.registrant_country == "DK"
    assert r.registrant_id_status == "verified"
    assert r.source == "rest"


def test_rest_normalises_nameservers_the_same_way_whois_does():
    assert parse_rest(REST, "example.dk").nameservers == ("ns1.one.com", "ns2.one.com")


def test_a_refusal_raises_rather_than_returning_an_empty_holder():
    """Being unlisted is a fact about our access. Returning a record with no
    registrant would let a caller read it as 'no holder on file'."""
    class Http:
        def get(self, url, **kw):
            return type("R", (), {"status_code": 403})()

    with pytest.raises(PermissionError, match="IP-whitelisted"):
        RestClient(Http()).lookup("example.dk")


def test_a_successful_rest_lookup_returns_the_holder():
    class Http:
        def get(self, url, **kw):
            return type("R", (), {"status_code": 200, "json": lambda self=None: REST,
                                  "raise_for_status": lambda self=None: None})()

    assert RestClient(Http()).lookup("example.dk").registrant_name == "Acme ApS"


def test_a_record_round_trips_through_the_store_shape():
    r = parse_rest(REST, "example.dk")
    d = r.to_dict()
    assert d["registered"] == "2011-04-01" and d["expires"] == "2027-04-30"
    assert d["nameservers"] == ["ns1.one.com", "ns2.one.com"]


def test_a_missing_date_does_not_raise():
    r = parse_whois("Domain: x.dk\nRegistered:\nStatus: Active\n", "x.dk")
    assert r.registered is None and r.status == "Active"


# ── a refusal is about us, not about the domain ─────────────────────────────

def test_a_throttle_response_raises_rather_than_parsing_to_an_empty_record():
    """Punktum dk cut off a 199-domain run after five queries. Parsing the
    refusal produced a record with no registrar and no registration date —
    indistinguishable from a real domain with nothing on file — and 193
    companies were stored that way."""
    from gleipnir.adapters.dkhostmaster import Throttled
    with pytest.raises(Throttled):
        parse_whois("Too many requests.\n", "x.dk")


def test_other_refusal_shapes_are_recognised():
    from gleipnir.adapters.dkhostmaster import Throttled, is_refusal
    for text in ("Rate limit exceeded\n", "Service unavailable\n",
                 "# a comment\nAccess denied\n"):
        assert is_refusal(text), text
        with pytest.raises(Throttled):
            parse_whois(text, "x.dk")


def test_a_real_record_is_not_mistaken_for_a_refusal():
    from gleipnir.adapters.dkhostmaster import is_refusal
    assert not is_refusal(WHOIS)
    assert parse_whois(WHOIS, "pharmaco.example").registrar == "Safenames Ltd"


def test_records_already_written_from_a_throttle_are_recognised_on_read():
    """The raw store is append-only, so the poisoned records cannot be deleted.
    They are skipped instead."""
    poisoned = DomainRecord(domain="x.dk", observed_at="t", source="whois",
                            raw="Too many requests.\n")
    assert not poisoned.is_evidence
    assert parse_whois(WHOIS, "pharmaco.example").is_evidence


def test_a_domain_genuinely_carrying_nothing_is_also_not_evidence():
    """A record with no registration date, no registrar and no nameservers says
    nothing either way — it must not be read as a clean check."""
    assert not DomainRecord(domain="x.dk", observed_at="t", source="whois").is_evidence


def test_the_client_is_slow_by_default_rather_than_by_remembering():
    """Measured: five queries at 0.7s spacing before the registry cut us off.
    A screen needs one, so the default pace costs the product nothing."""
    from gleipnir.adapters.dkhostmaster import DEFAULT_DELAY, WhoisClient
    assert DEFAULT_DELAY >= 5.0
    assert WhoisClient().delay == DEFAULT_DELAY
