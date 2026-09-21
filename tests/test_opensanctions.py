"""Filtering tests for the sanctions index.

These encode findings from the live 2026-08-26 export. Each one describes a
real entry that would have produced a wrong finding.
"""
from gleipnir.adapters.opensanctions import (
    SanctionsIndex, Target, is_designation, normalise_identifier, normalise_name,
)


def _t(name, programs, identifiers=(), aliases=()):
    return Target(id=f"id-{name}", schema="Company", name=name, aliases=aliases,
                  countries=("dk",), identifiers=identifiers, datasets=("Some List",),
                  programs=programs, first_seen=None, last_change=None)


def test_adversary_issued_listings_are_excluded():
    """Example Defence Corp and Recorded Future appear in the live export solely
    under Chinese programmes. Screening against the unfiltered list would flag
    a Western defence contractor as sanctions-linked."""
    idx = SanctionsIndex([_t("Example Defence Corp", ("CN-CML",))])
    assert len(idx) == 0
    assert idx.excluded[0].adversary_issued_only


def test_regulatory_registers_are_not_sanctions():
    """EXAMPLETEK, a real Danish company, appears under EU-ESMA — a securities
    register, not a restrictive measure."""
    idx = SanctionsIndex([_t("EXAMPLETEK", ("EU-ESMA",))])
    assert idx.by_name("EXAMPLETEK") == []


def test_entries_with_no_programme_are_excluded():
    """The Example Democracy Foundation, a Danish NGO, carries no
    programme at all in the export. Absence must fail closed."""
    assert SanctionsIndex([_t("Example Democracy Foundation", ())]).targets == []


def test_excluded_targets_are_unreachable_by_every_lookup():
    """Regression: the name and identifier indexes were once built from the
    unfiltered list, so an excluded target stayed reachable via by_name."""
    idx = SanctionsIndex([_t("Blocked Co", ("CN-CML",), identifiers=("DK12345678",))])
    assert idx.by_name("Blocked Co") == []
    assert idx.by_identifier("12345678") == []


def test_allied_designations_are_kept():
    idx = SanctionsIndex([_t("Real Target", ("EU-IRN", "US-IRAN"))])
    assert len(idx) == 1
    assert idx.targets[0].is_designated


def test_unknown_issuer_fails_closed():
    """A new issuing country in a future export must not silently become a red
    flag — the allowlist is deliberate."""
    assert not is_designation(("ZZ-NEWPROGRAMME",))


def test_identifiers_match_prefixed_and_bare():
    """Identifiers arrive prefixed ('IMO9000001') and are looked up bare, from
    a registry record that has only the number."""
    idx = SanctionsIndex([_t("Vessel Co", ("EU-IRN",), identifiers=("IMO9000001",))])
    assert idx.by_identifier("9000001")
    assert idx.by_identifier("IMO9000001")


def test_aliases_are_searchable():
    idx = SanctionsIndex([_t("Primary Name", ("EU-IRN",), aliases=("Trading As Ltd",))])
    assert idx.by_name("trading as ltd")


def test_name_normalisation_strips_diacritics_and_punctuation():
    assert normalise_name("Ærø  Vodka, ApS.") == "ærø vodka aps"
    assert normalise_name("Ivanov") == normalise_name("IVANOV")


def test_identifier_normalisation():
    assert normalise_identifier("DK 12-345.678") == "DK12345678"


def test_a_bare_number_does_not_join_across_jurisdictions():
    """Six real Danish CVR numbers collide with designated entities' national
    registration numbers — roughly one in eleven 8-digit strings passes the
    Danish mod-11 checksum by chance. Joining on the digits alone reports a
    Danish company as designated because a Russian number matched."""
    idx = SanctionsIndex([_t("PRIMERNAYA, OOO", ("UA-SA1644",),
                             identifiers=("77000001",))])
    idx.targets[0].countries  # ('dk',) from the helper — override below
    ru = Target(id="ru1", schema="Company", name="PRIMERNAYA, OOO", aliases=(),
                countries=("ru",), identifiers=("77000001",),
                datasets=("x",), programs=("UA-SA1644",),
                first_seen=None, last_change=None)
    idx = SanctionsIndex([ru])
    assert idx.by_identifier("77000001") == [ru], "unfiltered still matches"
    assert idx.by_identifier("77000001", country="dk") == [], \
        "a Danish lookup must not match a Russian registration number"
    assert idx.by_identifier("77000001", country="ru") == [ru]


def test_a_prefixed_identifier_carries_its_own_scheme():
    """IMO9000001 is unambiguous — no jurisdiction filter needed."""
    v = Target(id="v", schema="Vessel", name="EXAMPLE VESSEL", aliases=(), countries=(),
               identifiers=("IMO9000001",), datasets=("x",), programs=("EU-IRN",),
               first_seen=None, last_change=None)
    idx = SanctionsIndex([v])
    assert idx.by_identifier("IMO9000001", country="dk") == [v]


def test_a_matching_jurisdiction_still_joins():
    dk = Target(id="dk1", schema="Company", name="Danish Target ApS", aliases=(),
                countries=("dk",), identifiers=("99000147",), datasets=("x",),
                programs=("EU-UKR",), first_seen=None, last_change=None)
    assert SanctionsIndex([dk]).by_identifier("99000147", country="dk") == [dk]
