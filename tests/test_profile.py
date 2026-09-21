"""Deduplication and owner-count stratification over the bulk scans."""
from decimal import Decimal

from gleipnir.profile import PREDICATES, Row, build_rows, stratified_rates


def page(*companies):
    return [{"_source": {"Vrvirksomhed": c}} for c in companies]


def company(cvr, owners=(), directors=(), anden=()):
    rels = []
    for key, share, votes in owners:
        attrs = [{"type": "EJERANDEL_PROCENT",
                  "vaerdier": [{"vaerdi": share, "periode": {"gyldigTil": None}}]}]
        if votes is not None:
            attrs.append({"type": "EJERANDEL_STEMMERET_PROCENT",
                          "vaerdier": [{"vaerdi": votes, "periode": {"gyldigTil": None}}]})
        rels.append({
            "deltager": {"enhedsNummer": key,
                         "enhedstype": "ANDEN_DELTAGER" if key in anden else "VIRKSOMHED"},
            "organisationer": [{"hovedtype": "REGISTER",
                                "organisationsNavn": [{"navn": "EJERREGISTER"}],
                                "medlemsData": [{"attributter": attrs}]}]})
    for d in directors:
        rels.append({"deltager": {"enhedsNummer": d, "enhedstype": "PERSON"},
                     "organisationer": [{"hovedtype": "LEDELSESORGAN",
                                         "organisationsNavn": [{"navn": "Direktion"}],
                                         "medlemsData": [{}]}]})
    return {"cvrNummer": int(cvr), "deltagerRelation": rels}


class FakeStore:
    def __init__(self, pages):
        self._pages = pages

    def fetches(self):
        class F:
            def __init__(s, i):
                s.source = "cvr"; s.resource_type = "bulk_page"
                s.resource_id = f"calib:{i:04d}"; s.content_hash = str(i)
        return [F(i) for i in range(len(self._pages))]

    def get_json(self, h):
        return self._pages[int(h)]


def test_a_company_appearing_in_two_scans_is_counted_once():
    """The stratified scan's first starting points fell inside the ascending
    scan's range: 8,000 of 49,000 rows were the same company twice, and every
    rate double-weighted the overlap."""
    c = company("99000147", owners=[("111", "1.0", "1.0")])
    rows = build_rows(FakeStore([page(c), page(c)]))
    assert len(rows) == 1


def test_owner_count_and_predicates_are_extracted():
    rows = build_rows(FakeStore([page(company(
        "99000147", owners=[("111", "0.4", "0.9"), ("222", "0.4", "0.4")]))]))
    r = rows[0]
    assert r.owners == 2
    assert r.fired["voting_exceeds_equity"]
    assert r.fired["ownership_residual_unaccounted"]      # sums to 0.8, not 1.0
    # Both holders are under 50% and together exceed it — which is exactly what
    # the predicate says, and exactly why iteration 6 found it fires on 91.4% of
    # six-owner companies and demoted it to structural.
    assert r.fired["subthreshold_aggregate_over_50"]


def test_subthreshold_needs_two_holders_under_the_threshold():
    """A single majority holder leaves nothing to aggregate."""
    rows = build_rows(FakeStore([page(company(
        "99000147", owners=[("111", "0.8", None), ("222", "0.2", None)]))]))
    assert not rows[0].fired["subthreshold_aggregate_over_50"]


def test_an_anden_deltager_majority_holder_is_flagged():
    rows = build_rows(FakeStore([page(company(
        "99000147", owners=[("111", "0.9", None)], anden={"111"}))]))
    assert rows[0].fired["majority_owner_unresolvable"]


def test_a_company_with_no_filed_percentages_is_excluded_entirely():
    """No ownership register means no denominator — such a company must not
    dilute a rate computed over companies that have one."""
    assert build_rows(FakeStore([page(company("99000147", directors=["p1"]))])) == []


def test_stratified_rates_bucket_six_or_more_together():
    rows = [Row("1", 1, {p: False for p in PREDICATES}),
            Row("2", 9, {p: p == "nominee_density" for p in PREDICATES}),
            Row("3", 7, {p: False for p in PREDICATES})]
    rates = stratified_rates(rows)
    assert set(rates) == {1, 6}
    assert rates[6]["_n"] == 2
    assert rates[6]["nominee_density"] == 0.5


def test_infrastructure_directors_are_excluded_from_nominee_density():
    """12 enhedsNummer values hold 200+ directorships each and sit on 13.8% of
    all Danish companies. Counting them made the predicate fire on a quarter of
    Denmark."""
    pages = [page(*[company(str(10000000 + i), owners=[("9", "1.0", None)],
                            directors=["infra"]) for i in range(250)])]
    rows = build_rows(FakeStore(pages))
    assert rows, "sanity: rows were built"
    assert not any(r.fired["nominee_density"] for r in rows)
