"""DNS observation and provider classification.

The provider conditioning is the point: a predicate keyed on shared mail
infrastructure fires on everyone using Google Workspace unless the dominant
hosts are conditioned out — the same failure that made formation-agent addresses
useless as a signal.
"""
from gleipnir.adapters.dns import DnsObservation, classify_provider, domain_of


def obs(**kw):
    base = dict(domain="example.dk", observed_at="2026-08-27T00:00:00+00:00")
    base.update(kw)
    return DnsObservation(**base)


# ── provider conditioning ───────────────────────────────────────────────────

def test_bulk_providers_are_named_not_lumped_into_other():
    assert classify_provider(("alt1.aspmx.l.google.com",)) == "google"
    assert classify_provider(("x-com.mail.protection.outlook.com",)) == "microsoft"
    assert classify_provider(("mx.one.com",)) == "one.com"


def test_an_unusual_host_is_the_interesting_bucket():
    """Sharing one of the dozen hosts everybody uses is not a network edge.
    Sharing a host nobody else uses is."""
    assert classify_provider(("mail.examplehost.net",)) == "other"


def test_no_mx_is_distinct_from_an_unclassified_mx():
    assert classify_provider(()) == "none"
    assert classify_provider(("mail.obscure.example",)) == "other"


def test_classification_is_order_independent():
    mx = ("alt2.aspmx.l.google.com", "aspmx.l.google.com")
    assert classify_provider(mx) == classify_provider(tuple(reversed(mx))) == "google"


def test_a_trailing_dot_and_case_do_not_defeat_matching():
    assert classify_provider(("ASPMX.L.GOOGLE.COM.",)) == "google"


# ── change detection ────────────────────────────────────────────────────────

def test_mx_hash_is_stable_across_record_order():
    """An MX finding is usually a change. The hash has to be a property of the
    configuration, not of the order the resolver happened to return it in."""
    a = obs(mx=("a.mail.example", "b.mail.example"))
    b = obs(mx=("b.mail.example", "a.mail.example"))
    assert a.mx_hash == b.mx_hash


def test_a_changed_mail_host_changes_the_hash():
    assert obs(mx=("a.mail.example",)).mx_hash != obs(mx=("c.mail.example",)).mx_hash


def test_losing_mx_entirely_is_visible_as_a_hash_change():
    assert obs(mx=("a.mail.example",)).mx_hash != obs(mx=()).mx_hash
    assert not obs(mx=()).has_mx


def test_nameserver_hash_is_independent_of_mail():
    a = obs(mx=("a.mail.example",), ns=("ns1.host.example",))
    b = obs(mx=("z.mail.example",), ns=("ns1.host.example",))
    assert a.ns_hash == b.ns_hash and a.mx_hash != b.mx_hash


# ── domain extraction from what CVR actually holds ──────────────────────────

def test_domain_is_extracted_from_every_form_cvr_files():
    for raw, want in [
        ("www.pharmaco.example", "pharmaco.example"),
        ("https://www.xrturbo.example", "xrturbo.example"),
        ("http://example.dk/om-os", "example.dk"),
        ("eksempel.dk/lyngby", "eksempel.dk"),
        ("https://x.dk:8080/a?b=c", "x.dk"),
        ("  WWW.Example.DK  ", "example.dk"),
    ]:
        assert domain_of(raw) == want, raw


def test_an_empty_or_junk_value_yields_no_domain():
    assert domain_of("") == ""
    assert domain_of("   ") == ""


# ── observations are records, not state ─────────────────────────────────────

def test_an_observation_serialises_round_trip():
    o = obs(mx=("a.mail.example",), ns=("ns1.example",), a=("1.2.3.4",),
            spf=True, dmarc=False, errors=("MX:Timeout",))
    d = o.to_dict()
    assert d["observed_at"] == o.observed_at
    assert DnsObservation(**{**d, **{k: tuple(d[k]) for k in
                                     ("mx", "ns", "a", "errors")}}).mx_hash == o.mx_hash


def test_errors_are_recorded_rather_than_swallowed():
    """A timeout and an NXDOMAIN mean different things: one is our problem, the
    other is a fact about the domain."""
    assert "MX:Timeout" in obs(errors=("MX:Timeout",)).errors
    assert obs(errors=("MX:NXDOMAIN",)).errors[0].endswith("NXDOMAIN")


def test_the_website_field_contains_things_that_are_not_urls():
    """All three measured in 253 filed CVR values, and all three produced a
    false 'domain does not resolve' before being handled."""
    assert domain_of("name@example-mail.dk") == "example-mail.dk"
    assert domain_of("wwwexample-shop.dk") == "example-shop.dk"
    idn = domain_of("eksempelsmøreri.dk")
    assert idn.isascii() and idn.startswith("xn--") and idn.endswith(".dk")
    assert idn == "eksempelsmøreri.dk".encode("idna").decode()


def test_a_value_that_cannot_be_a_domain_yields_nothing():
    for junk in ("n/a", "ingen", "-", "http://", "localhost"):
        assert domain_of(junk) == "", junk


def test_newer_microsoft_mx_formats_classify_as_microsoft():
    """Two live records fell into 'other' and inflated the interesting bucket."""
    assert classify_provider(("pamx1.hotmail.com",)) == "microsoft"
    assert classify_provider(("x-dk.l-v1.mx.microsoft",)) == "microsoft"


# ── infrastructure jurisdiction ─────────────────────────────────────────────

def test_a_cdn_edge_does_not_report_the_origin_country():
    """dr.dk resolves to NL because Akamai's edge is there. Reading a country
    off a CDN A record is not evidence about where anything actually sits."""
    from gleipnir.adapters.dns import NetBlock
    edge = NetBlock(ip="1.2.3.4", asn="16625", country="NL")
    assert edge.is_cdn and edge.provider == "akamai"
    direct = NetBlock(ip="5.6.7.8", asn="48287", country="RU")
    assert not direct.is_cdn and direct.provider == ""


def test_a_hosting_provider_is_not_a_cdn_and_its_country_counts():
    """Hetzner is where the server is, not an edge in front of it. Treating it
    as a CDN suppressed 14 legitimate DE readings in a 251-domain cohort."""
    from gleipnir.adapters.dns import NetBlock
    hetzner = NetBlock(ip="1.2.3.4", asn="24940", country="DE")
    assert not hetzner.is_cdn
    assert hetzner.hosted_by == "hetzner" and hetzner.provider == "hetzner"


def test_jurisdiction_separates_web_from_mail():
    """Mail is the honest read: MX hosts are rarely fronted by a CDN, so their
    country is the host actually receiving the mail."""
    from gleipnir.adapters.dns import NetBlock, jurisdictions

    class FakeAsn:
        def lookup(self, ip):
            return {"1.1.1.1": NetBlock("1.1.1.1", "13335", "US"),
                    "2.2.2.2": NetBlock("2.2.2.2", "48287", "RU")}[ip]

        def resolve_host(self, host):
            return ["2.2.2.2"]

    o = obs(a=("1.1.1.1",), mx=("mail.example",))
    j = jurisdictions(o, FakeAsn())
    assert j["web_countries"] == ["US"] and j["web_cdn"] == ["cloudflare"]
    assert j["web_behind_cdn"] is True
    assert j["mail_countries"] == ["RU"]


def test_a_domain_with_no_records_yields_no_jurisdiction_claim():
    from gleipnir.adapters.dns import jurisdictions

    class FakeAsn:
        def lookup(self, ip): raise AssertionError("should not be called")
        def resolve_host(self, host): return []

    j = jurisdictions(obs(), FakeAsn())
    assert j["web_countries"] == [] and j["mail_countries"] == []
    assert j["web_behind_cdn"] is False


def test_asn_lookup_parses_the_cymru_txt_format():
    """Team Cymru answer: '15169 | 8.8.8.0/24 | US | arin | 2023-12-28'.
    Multi-origin prefixes return several ASNs in the first field."""
    from gleipnir.adapters.dns import AsnClient

    class FakeTxt:
        def __init__(self, t): self._t = t
        def to_text(self): return self._t

    class FakeAns(list): pass

    c = AsnClient()
    c._r = type("R", (), {
        "resolve": lambda self, name, rtype: FakeAns([
            FakeTxt('"13238 208398 | 5.255.192.0/18 | RU | ripencc | 2012-09-14"')]),
        "timeout": 0, "lifetime": 0})()
    b = c.lookup("5.255.255.70")
    assert b.asn == "13238" and b.country == "RU" and b.registry == "ripencc"
    assert b.prefix == "5.255.192.0/18"


def test_asn_lookup_is_cached_per_ip():
    from gleipnir.adapters.dns import AsnClient
    calls = []

    class FakeTxt:
        def to_text(self): return '"15169 | 8.8.8.0/24 | US | arin | 2023"'

    c = AsnClient()
    c._r = type("R", (), {
        "resolve": lambda self, n, t: (calls.append(n), [FakeTxt()])[1],
        "timeout": 0, "lifetime": 0})()
    c.lookup("8.8.8.8"); c.lookup("8.8.8.8")
    assert len(calls) == 1


def test_an_unresolvable_ip_yields_an_empty_block_not_an_exception():
    """A lookup failure must never abort a run — the country is simply unknown."""
    from gleipnir.adapters.dns import AsnClient

    def boom(self, n, t): raise RuntimeError("servfail")

    c = AsnClient()
    c._r = type("R", (), {"resolve": boom, "timeout": 0, "lifetime": 0})()
    b = c.lookup("192.0.2.1")
    assert b.ip == "192.0.2.1" and b.country == "" and b.asn == ""
    assert not b.is_cdn
