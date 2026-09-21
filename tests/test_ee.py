"""Estonia: identifier-strength ownership, and the limits stated as limits.

The measurement in `docs/jurisdictions.md` decided the scope: company-to-company
edges join on registry codes and are worth following; person-level matching does
not work and must not be attempted by name. These assert the scope holds.
"""
import csv
import io
import json
import zipfile
from datetime import date

import pytest

from gleipnir.adapters.ee_ariregister import (
    COUNTRY, DATASETS, SOURCE_ID, Company, EeClient, EeError, Holder, holders,
    lookup,
)
from gleipnir.claims import EpistemicTier, Predicate
from gleipnir.extract.ee import (
    extract_company, extract_holders, unresolvable_holders,
)
from gleipnir.rawstore import RawStore

COLUMNS = ["nimi", "ariregistri_kood", "ettevotja_oiguslik_vorm",
           "ettevotja_oigusliku_vormi_alaliik", "kmkr_nr", "ettevotja_staatus",
           "ettevotja_staatus_tekstina", "ettevotja_esmakande_kpv",
           "ettevotja_aadress", "asukoht_ettevotja_aadressis", "asukoha_ehak_kood",
           "asukoha_ehak_tekstina", "indeks_ettevotja_aadressis", "ads_adr_id",
           "ads_ads_oid", "ads_normaliseeritud_taisaadress", "teabesysteemi_link"]


def zipped(name: str, payload: bytes) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(name, payload)
    return buf.getvalue()


def identity_csv(*rows: dict) -> bytes:
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=COLUMNS, delimiter=";")
    writer.writeheader()
    for row in rows:
        writer.writerow({c: row.get(c, "") for c in COLUMNS})
    return out.getvalue().encode("utf-8-sig")


def company_row(code="70000017", name="Tallinn Logistics OÜ", status="R"):
    return {"nimi": name, "ariregistri_kood": code,
            "ettevotja_oiguslik_vorm": "Osaühing", "ettevotja_staatus": status,
            "ettevotja_staatus_tekstina": "Registrisse kantud",
            "ettevotja_esmakande_kpv": "07.02.2013",
            "asukoha_ehak_tekstina": "Tallinn, Harju maakond", "kmkr_nr": "EE101"}


def owners_json(code="70000017", owners=None):
    return json.dumps([{
        "ariregistri_kood": int(code), "nimi": "Tallinn Logistics OÜ",
        "osanikud": owners if owners is not None else [
            {"isiku_tyyp": "J", "eesnimi": "", "nimi_arinimi": "Holding OÜ",
             "isikukood_registrikood": "70000025", "osaluse_protsent": "60.00",
             "algus_kpv": "01.01.2019", "lopp_kpv": None},
            {"isiku_tyyp": "F", "eesnimi": "Jaan", "nimi_arinimi": "Tamm",
             "isikukood_registrikood": None, "osaluse_protsent": "40.00",
             "algus_kpv": "01.01.2019", "lopp_kpv": None},
        ]}], ensure_ascii=False).encode()


@pytest.fixture
def store(tmp_path):
    s = RawStore(tmp_path)
    s.put(payload=zipped("lihtandmed.csv", identity_csv(company_row())),
          source=SOURCE_ID, resource_type="lihtandmed", resource_id="lihtandmed",
          http_status=200, request_params={})
    s.put(payload=zipped("osanikud.json", owners_json()),
          source=SOURCE_ID, resource_type="osanikud", resource_id="osanikud",
          http_status=200, request_params={})
    return s


# ── lookup ──────────────────────────────────────────────────────────────────

def test_a_company_is_found_by_name(store):
    found, blob = lookup(store, names=["Tallinn Logistics OÜ"])
    assert list(found) == ["70000017"]
    assert found["70000017"].legal_form == "Osaühing"
    assert found["70000017"].registered_on == date(2013, 2, 7)
    assert blob, "the dataset hash must ride along so claims can cite it"


def test_a_company_is_found_by_code(store):
    assert list(lookup(store, codes=["70000017"])[0]) == ["70000017"]


def test_name_matching_ignores_case_and_spacing(store):
    assert lookup(store, names=["  tallinn   logistics oü "])[0]


def test_asking_for_nothing_reads_nothing(store):
    assert lookup(store) == ({}, "")


def test_a_missing_dataset_is_empty_rather_than_an_error(tmp_path):
    """Not ingested and not present are the same to a reader, and neither is
    'this company does not exist'."""
    assert lookup(RawStore(tmp_path), names=["Anything"]) == ({}, "")


def test_an_unknown_dataset_is_refused_before_any_request():
    with pytest.raises(EeError, match="unknown dataset"):
        EeClient().url_for("nonsense")


def test_every_declared_dataset_has_a_url():
    client = EeClient()
    for dataset in DATASETS:
        assert client.url_for(dataset).endswith(".zip")


# ── holders ─────────────────────────────────────────────────────────────────

def test_holders_are_streamed_for_the_requested_company(store):
    got, blob = holders(store, ["70000017"])
    assert len(got["70000017"]) == 2
    assert blob


def test_a_legal_holder_with_a_code_can_be_followed(store):
    legal = [h for h in holders(store, ["70000017"])[0]["70000017"] if h.is_company][0]
    assert legal.code == "70000025"
    assert legal.resolvable


def test_a_natural_person_without_an_identifier_cannot_be_followed(store):
    """Estonia stripped UBO identifiers in 2025. A name is not an identity, and
    `docs/jurisdictions.md` measured what pretending otherwise costs."""
    person = [h for h in holders(store, ["70000017"])[0]["70000017"]
              if not h.is_company][0]
    assert person.code == ""
    assert not person.resolvable


def test_holders_of_an_unlisted_company_are_absent_not_empty(store):
    assert holders(store, ["99999999"])[0] == {}


# ── extraction ──────────────────────────────────────────────────────────────

def company():
    return Company(code="70000017", name="Tallinn Logistics OÜ",
                   legal_form="Osaühing", status="R",
                   status_text="Registrisse kantud",
                   registered_on=date(2013, 2, 7), address="Tallinn")


def test_identity_claims_match_the_shapes_cvr_uses():
    """Nothing downstream should have to know Estonia exists."""
    claims = extract_company(company(), raw_ref="h")
    assert {c.predicate for c in claims} >= {
        Predicate.HAS_NAME, Predicate.HAS_STATUS, Predicate.HAS_LEGAL_FORM,
        Predicate.FOUNDED_ON, Predicate.REGISTERED_AT}
    assert all(c.source_id == SOURCE_ID for c in claims)


def test_the_jurisdiction_rides_on_the_claim():
    """`cv.py` reads the country off a source that states it, never a guess."""
    status = [c for c in extract_company(company(), raw_ref="h")
              if c.predicate is Predicate.HAS_STATUS][0]
    assert status.qualifiers["jurisdiction"] == COUNTRY


def test_an_ownership_edge_points_holder_to_company():
    """Direction matches extract/cvr.py so chain.py walks it unchanged."""
    holder = Holder(person_type="J", name="Holding OÜ", code="70000025",
                    share_percent="60.00", valid_from=date(2019, 1, 1))
    claim = extract_holders(company(), [holder], raw_ref="h")[0]
    assert claim.predicate is Predicate.OWNS
    assert claim.subject.key == "ee:70000025"
    assert claim.object.key == "ee:70000017"
    assert claim.valid_from == date(2019, 1, 1)


def test_a_share_becomes_a_fraction():
    holder = Holder(person_type="J", name="X", code="1", share_percent="60.00")
    assert extract_holders(company(), [holder], raw_ref="h")[0].qualifiers["share"] == 0.6


def test_a_missing_share_is_none_rather_than_zero():
    """Zero and unfiled are different facts; conflating them understates a chain."""
    holder = Holder(person_type="J", name="X", code="1", share_percent="")
    assert extract_holders(company(), [holder], raw_ref="h")[0].qualifiers["share"] is None


def test_the_ownership_tier_matches_the_danish_equivalent():
    """Estonia's shareholder list has the same standing as the ejerregister: a
    filing obligation on the subject, checked by nobody."""
    holder = Holder(person_type="J", name="X", code="1", share_percent="10")
    claim = extract_holders(company(), [holder], raw_ref="h")[0]
    assert claim.epistemic_tier is EpistemicTier.SELF_DECLARED_TO_REGISTRY


def test_an_unidentified_person_holder_is_keyed_on_a_name_and_says_so():
    holder = Holder(person_type="F", name="Jaan Tamm", share_percent="40")
    claim = extract_holders(company(), [holder], raw_ref="h")[0]
    assert claim.subject.key.startswith("name:")
    assert claim.qualifiers["resolvable"] is False
    assert claim.qualifiers["holder_type"] == "natural"


def test_unfollowable_holders_are_reported_not_dropped():
    """A terminating edge is a fact about coverage and belongs in the screen."""
    hs = [Holder(person_type="J", name="Followable", code="1"),
          Holder(person_type="F", name="Jaan Tamm"),
          Holder(person_type="J", name="Foreign SIA", foreign_country="LV")]
    assert len(extract_holders(company(), hs, raw_ref="h")) == 3
    assert len(unresolvable_holders(hs)) == 2
