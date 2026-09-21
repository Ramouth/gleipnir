"""End-to-end: a document in, a Screen out.

The finding path is the highest-consequence code in the system and no live
screen has ever exercised it — every real company tested came back green. These
build the case deliberately, using a name taken from the pinned designation
list, and assert what the screen is allowed to say about it.
"""
from datetime import date

import pytest

from gleipnir.adapters.opensanctions import SanctionsIndex, Target
from gleipnir.finding import Colour, RedGround
from gleipnir.rawstore import RawStore
from gleipnir.screen import Sources, render, run

AS_OF = date(2026, 8, 27)


def target(name, country="ru", programmes=("EU-UKR",), identifiers=()):
    return Target(id=f"NK-{name[:6]}", schema="Company", name=name, aliases=(),
                  countries=(country,), identifiers=identifiers, datasets=("EU OJ",),
                  programs=programmes, first_seen=None, last_change=None)


def attr(t, *values):
    return {"type": t, "vaerdier": [{"vaerdi": v, "periode": {"gyldigFra": f, "gyldigTil": None}}
                                    for v, f in values]}


def company(cvr, owner_name, owner_type="ANDEN_DELTAGER", share="1.0"):
    return {"hits": {"hits": [{"_source": {"Vrvirksomhed": {
        "cvrNummer": int(cvr),
        "navne": [{"navn": "Target Danish Co ApS",
                   "periode": {"gyldigFra": "2019-01-01", "gyldigTil": None}}],
        "virksomhedsstatus": [{"status": "NORMAL",
                               "periode": {"gyldigFra": "2019-01-01", "gyldigTil": None}}],
        "virksomhedMetadata": {"sammensatStatus": "NORMAL",
                               "stiftelsesDato": "2019-01-01"},
        "attributter": [attr("KAPITAL", ("40000.00", "2019-01-01"))],
        "deltagerRelation": [{
            "deltager": {"enhedsNummer": 991, "enhedstype": owner_type,
                         "forretningsnoegle": None,
                         "navne": [{"navn": owner_name}]},
            "organisationer": [{
                "hovedtype": "REGISTER",
                "organisationsNavn": [{"navn": "EJERREGISTER"}],
                "medlemsData": [{"attributter": [
                    attr("EJERANDEL_PROCENT", (share, "2019-01-01")),
                    attr("EJERANDEL_STEMMERET_PROCENT", (share, "2019-01-01"))]}]}]}],
    }}}]}}


def screen_with(tmp_path, owner_name, targets, **kw):
    import json
    store = RawStore(tmp_path)
    store.put(payload=json.dumps(company("99000147", owner_name, **kw)).encode(),
              source="cvr", resource_type="virksomhed", resource_id="99000147",
              http_status=200, request_params={})
    return run("99000147",
               Sources(store=store, sanctions=SanctionsIndex(targets), budget=0),
               as_of=AS_OF)


# ── the finding path ────────────────────────────────────────────────────────

def test_a_designated_owner_produces_a_finding(tmp_path):
    s = screen_with(tmp_path, "Vologda Optical and Mechanical Plant",
                    [target("Vologda Optical and Mechanical Plant")])
    assert s.findings, "a designated owner must not pass silently"
    assert s.colour is Colour.AMBER


def test_a_designation_finding_is_amber_and_says_why(tmp_path):
    """Corporate nodes match by NAME, and a live name lookup returned a
    different person of the same surname on the first attempt. Amber until a
    verdict resolves the identity — nothing promotes it silently."""
    s = screen_with(tmp_path, "Vologda Optical and Mechanical Plant",
                    [target("Vologda Optical and Mechanical Plant")])
    f = s.findings[0]
    assert f.colour is Colour.AMBER
    assert f.ground is RedGround.DESIGNATION
    assert f.qualifier == "unadjudicated_name_match"
    assert "NOT adjudicated" in f.statement


def test_a_designation_never_becomes_red_without_adjudication(tmp_path):
    s = screen_with(tmp_path, "Vologda Optical and Mechanical Plant",
                    [target("Vologda Optical and Mechanical Plant")])
    assert all(f.colour is not Colour.RED for f in s.findings)
    assert s.colour is not Colour.RED


def test_an_undesignated_owner_leaves_the_screen_green(tmp_path):
    s = screen_with(tmp_path, "Perfectly Ordinary Holding ApS",
                    [target("Some Other Entity")])
    assert s.findings == []
    assert s.colour is Colour.GREEN


def test_an_adversary_issued_listing_does_not_produce_a_finding(tmp_path):
    """The bulk list carries sanctions issued BY Russia, China and Iran.
    Example Defence Corp appears under a Chinese programme; screening unfiltered
    would flag a Western defence contractor."""
    s = screen_with(tmp_path, "Example Defence Corp",
                    [target("Example Defence Corp", country="us", programmes=("CN-CML",))])
    assert s.findings == []
    assert s.colour is Colour.GREEN


# ── what the screen says about structure ────────────────────────────────────

def test_structure_never_becomes_a_finding(tmp_path):
    """A wholly-owned chain terminating at an unresolvable foreign party is the
    strongest structural signal there is, and it is still a chain fact."""
    s = screen_with(tmp_path, "Some Opaque Holding Sarl", [])
    preds = {c.predicate for c in s.chain}
    assert "majority_owner_unresolvable" in preds
    assert s.findings == []
    assert s.colour is Colour.GREEN


def test_rated_facts_carry_their_denominator(tmp_path):
    s = screen_with(tmp_path, "Some Opaque Holding Sarl", [])
    f = next(c for c in s.chain if c.predicate == "majority_owner_unresolvable")
    assert f.comparator.ordinary is not None
    assert "of ordinary Danish companies" in f.comparator.line()


def test_an_unrated_fact_says_so_rather_than_implying_rarity(tmp_path):
    s = screen_with(tmp_path, "Some Opaque Holding Sarl", [])
    unrated = [c for c in s.chain if c.comparator.ordinary is None]
    assert unrated, "sanity: some facts have no measured comparator"
    assert all("no measured comparator" in c.comparator.line() for c in unrated)


# ── rendering ───────────────────────────────────────────────────────────────

def test_the_rendered_screen_states_why_it_is_clear(tmp_path):
    """An empty findings block must read as a conclusion, not a blank."""
    out = render(screen_with(tmp_path, "Ordinary Holding ApS", []))
    assert "no designation, adjudicated fraud" in out


def test_a_company_absent_from_the_store_is_not_reported_clean(tmp_path):
    s = run("99999999", Sources(store=RawStore(tmp_path), budget=0), as_of=AS_OF)
    assert s.colour is Colour.GREY
    assert s.unknowable and "not in the raw store" in s.unknowable[0]
