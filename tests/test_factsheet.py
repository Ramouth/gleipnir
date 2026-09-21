"""The sheet as a value, not as printed output.

`scripts/factsheet.py` and the workbench are both projections of `Sheet`. These
test the thing they project, and one regression that mattered: a payload shape
the reader could not open was being reported as *not in the raw store* — a
parser gap dressed as a coverage statement, which is the inversion the whole
four-valued apparatus exists to prevent.
"""
import json
from datetime import date

import pytest

from gleipnir.factsheet import (
    NotInStore, SECTIONS, build, question_for, resolve, unwrap_hit,
)
from gleipnir.predicates.core import V
from gleipnir.rawstore import RawStore

AS_OF = date(2020, 1, 1)


def doc(cvr="99000147", name="Fixture ApS"):
    return {"_source": {"Vrvirksomhed": {
        "cvrNummer": int(cvr),
        "navne": [{"navn": name, "periode": {"gyldigFra": "2015-01-01", "gyldigTil": None}}],
        "virksomhedsstatus": [
            {"status": "NORMAL", "periode": {"gyldigFra": "2015-01-01",
                                             "gyldigTil": "2021-06-30"}},
            {"status": "UNDER KONKURS", "periode": {"gyldigFra": "2021-07-01",
                                                    "gyldigTil": None}}],
        "virksomhedMetadata": {
            "sammensatStatus": "NORMAL", "stiftelsesDato": "2015-01-01",
            "nyesteNavn": {"navn": name},
            "nyesteVirksomhedsform": {"langBeskrivelse": "Anpartsselskab"}},
        "attributter": [],
        "deltagerRelation": [],
    }}}


def store_with(tmp_path, payload, cvr="99000147"):
    store = RawStore(tmp_path)
    store.put(payload=json.dumps(payload).encode(), source="cvr",
              resource_type="virksomhed", resource_id=cvr, http_status=200,
              request_params={})
    return store


# ── the payload shapes actually on file ─────────────────────────────────────

def test_unwrap_handles_every_shape_the_store_holds():
    """Three shapes exist because the adapter was written more than once, and
    the store is append-only: the old ones are permanent."""
    bare = doc()
    assert unwrap_hit(bare) is bare
    assert unwrap_hit([bare]) is bare
    assert unwrap_hit({"hits": {"hits": [bare]}}) is bare


def test_unwrap_reports_nothing_rather_than_guessing():
    assert unwrap_hit([]) is None
    assert unwrap_hit({"hits": {"hits": []}}) is None


@pytest.mark.parametrize("wrap", [
    lambda d: d,
    lambda d: [d],
    lambda d: {"hits": {"hits": [d]}},
])
def test_an_elasticsearch_envelope_is_not_a_missing_company(tmp_path, wrap):
    """The regression. 19 of 329 cached companies were stored as full ES
    responses, and the sheet reported every one of them as absent from a store
    that held them."""
    store = store_with(tmp_path, wrap(doc()))
    sheet = build(store, "99000147", None, AS_OF)
    assert sheet.name == "Fixture ApS"
    assert sheet.legal_form == "Anpartsselskab"


def test_a_company_never_fetched_still_raises(tmp_path):
    with pytest.raises(NotInStore):
        build(RawStore(tmp_path), "99000147", None, AS_OF)


def test_resolve_is_quiet_about_an_unfetched_company(tmp_path):
    assert resolve(RawStore(tmp_path), "99000147") == (None, None, None)


# ── the sheet itself ────────────────────────────────────────────────────────

def test_the_as_of_date_reads_before_the_transition(tmp_path):
    """With no date given, a company that later failed is read a year before it
    failed — so nothing on the sheet is hindsight."""
    sheet = build(store_with(tmp_path, doc()), "99000147", None, None)
    assert sheet.as_of == date(2020, 7, 1)
    assert "nothing below is hindsight" in sheet.as_of_reason
    assert sheet.status_at(sheet.as_of) == "NORMAL"


def test_an_explicit_date_says_so(tmp_path):
    sheet = build(store_with(tmp_path, doc()), "99000147", None, AS_OF)
    assert (sheet.as_of, sheet.as_of_reason) == (AS_OF, "as specified")


def test_the_as_of_marker_falls_in_exactly_one_period(tmp_path):
    sheet = build(store_with(tmp_path, doc()), "99000147", None, AS_OF)
    assert sum(sheet.covers(frm, to) for frm, to, _ in sheet.status_history) == 1


def test_every_result_lands_in_exactly_one_section(tmp_path):
    sheet = build(store_with(tmp_path, doc()), "99000147", None, AS_OF)
    placed = [row.predicate for s in sheet.sections for row in s.rows]
    assert sorted(placed) == sorted(r.predicate for r in sheet.results)
    assert len(placed) == len(set(placed))


def test_sections_keep_the_four_values_apart(tmp_path):
    """UNKNOWN and UNKNOWABLE never share a heading: one is a research task and
    the other is a coverage statement."""
    sheet = build(store_with(tmp_path, doc()), "99000147", None, AS_OF)
    for section in sheet.sections:
        assert section.heading == SECTIONS[section.value]
        assert {row.value for row in section.rows} == {section.value}
    assert SECTIONS[V.UNKNOWN] != SECTIONS[V.UNKNOWABLE]


def test_only_established_facts_carry_a_comparator(tmp_path):
    """A rate for something that did not happen is not a denominator."""
    sheet = build(store_with(tmp_path, doc()), "99000147", None, AS_OF)
    for section in sheet.sections:
        for row in section.rows:
            if row.value is not V.TRUE:
                assert row.comparator == ()


def test_a_fact_without_a_measured_rate_says_so(tmp_path):
    sheet = build(store_with(tmp_path, doc()), "99000147", None, AS_OF)
    for section in sheet.sections:
        for row in section.rows:
            for line in row.comparator:
                assert line.startswith(("seen in", "no measured comparator",
                                        "no denominator"))


def test_a_predicate_with_no_wording_appears_as_itself():
    assert question_for("audit_waived") == "whether audit was waived"
    assert question_for("newly_added_predicate") == "newly_added_predicate"


def test_the_register_note_fires_only_before_june_2015(tmp_path):
    store = store_with(tmp_path, doc())
    assert build(store, "99000147", None, date(2015, 1, 1)).register_note
    assert not build(store, "99000147", None, date(2016, 1, 1)).register_note


def test_no_rule_in_the_sheet_raises_red(tmp_path):
    """Restated here because the sheet is where a reader meets the rule table."""
    sheet = build(store_with(tmp_path, doc()), "99000147", None, AS_OF)
    for rule, _ in sheet.raised:
        assert rule.colour is not None and rule.colour != "red"
