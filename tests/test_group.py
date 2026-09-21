"""Classification of non-company, non-person owners."""
from gleipnir.group import GroupEvidence, PartyKind, classify_party, name_variants


def test_estate_is_not_an_opaque_owner():
    """'Boet efter Jens Peter Hansen' — a deceased Danish person's estate —
    was being reported as an owner beyond which ownership is not determinable.
    It resolves to a named person and conceals nothing."""
    assert classify_party("Boet efter Jens Peter Hansen") is PartyKind.ESTATE
    assert classify_party("Dødsboet efter Jens Hansen") is PartyKind.ESTATE


def test_public_bodies_are_not_opaque():
    assert classify_party("Københavns Kommune") is PartyKind.PUBLIC
    assert classify_party("Region Midtjylland") is PartyKind.PUBLIC


def test_partnership_forms_are_recognised():
    assert classify_party("Nordic Capital K/S") is PartyKind.PARTNERSHIP


def test_a_foreign_company_stays_other():
    assert classify_party("Eastport XR-TURBO Corp.") is PartyKind.OTHER
    assert classify_party("Bidco 7 (Luxembourg) Acquisition S.à.r.l.") is PartyKind.OTHER


def test_name_variants_strip_legal_form_and_parenthetical():
    """A verbatim GLEIF lookup missed a PE fund's Luxembourg vehicle; stripping the
    S.à.r.l. suffix found LEI 5493000EXAMPLE0LEI00."""
    v = name_variants("Bidco 7 (Luxembourg) Acquisition S.à.r.l.")
    assert "Bidco 7 (Luxembourg) Acquisition" in v
    assert "Bidco 7 Acquisition" in v


def test_only_documented_or_benign_suppresses():
    from gleipnir.group import GroupFinding
    assert GroupFinding(GroupEvidence.DOCUMENTED, "x").suppresses
    assert GroupFinding(GroupEvidence.BENIGN_FORM, "x").suppresses
    # A bare LEI says the entity is verified, not that it sits in a real group.
    assert not GroupFinding(GroupEvidence.REGISTERED, "x").suppresses
    assert not GroupFinding(GroupEvidence.NONE, "x").suppresses
