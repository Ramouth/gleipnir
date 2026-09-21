"""OpenAlex: affiliation and collaboration, and the lines this must not cross.

The domain is research-security screening for defence work, which makes the
refusals as load-bearing as the extraction. These assert both: that the dated
institutional facts come out, and that nothing derives a person's nationality,
that co-authorship asserts only co-authorship, and that a name search stays a
candidate set.
"""
from datetime import date

import httpx
import pytest

from gleipnir.adapters.openalex import (
    MAX_PER_PAGE, PEER_REVIEWED, OpenAlexClient, OpenAlexError, paged,
)
from gleipnir.claims import EpistemicTier, Predicate
from gleipnir.extract.openalex import (
    co_stated_countries, extract_author, extract_works,
)


def institution(name="Technical University of Denmark", cc="DK",
                kind="education", ror="04qtj9h94", oid="I11939039"):
    return {"id": f"https://openalex.org/{oid}", "ror": f"https://ror.org/{ror}",
            "display_name": name, "country_code": cc, "type": kind}


def author_record(name="Test Person", oid="A123", orcid=None, affiliations=None,
                  works_count=42):
    return {"id": f"https://openalex.org/{oid}", "display_name": name,
            "orcid": orcid, "works_count": works_count,
            "affiliations": affiliations if affiliations is not None else [
                {"institution": institution(), "years": [2023, 2024]}]}


def work(subject_id="A123", subject_name="Test Person", subject_insts=None,
         co_authors=(), when="2024-05-01", kind="article"):
    authorships = [{"author": {"id": f"https://openalex.org/{subject_id}",
                               "display_name": subject_name},
                    "institutions": subject_insts if subject_insts is not None
                    else [institution()]}]
    for co_id, co_name, insts in co_authors:
        authorships.append({"author": {"id": f"https://openalex.org/{co_id}",
                                       "display_name": co_name},
                            "institutions": insts})
    return {"id": "https://openalex.org/W1", "display_name": "A paper",
            "doi": "https://doi.org/10.1/x", "publication_date": when,
            "type": kind, "authorships": authorships}


# ── the client ──────────────────────────────────────────────────────────────

def client_with(handler, **kw):
    """Swap in a mock transport, keeping the headers the real client set —
    otherwise the polite-pool User-Agent is the thing under test and the
    harness is what removed it."""
    c = OpenAlexClient(**kw)
    c._c = httpx.Client(base_url="https://api.openalex.org",
                        headers=c._c.headers,
                        transport=httpx.MockTransport(handler))
    return c


def test_it_joins_the_polite_pool_when_given_an_address():
    seen = {}

    def handler(request):
        seen["ua"] = request.headers["User-Agent"]
        seen["mailto"] = request.url.params.get("mailto")
        return httpx.Response(200, json={})

    client_with(handler, mailto="a@b.invalid").search_author("x")
    assert "a@b.invalid" in seen["ua"] and seen["mailto"] == "a@b.invalid"


def test_it_works_without_an_address():
    def handler(request):
        assert "mailto" not in request.url.params
        return httpx.Response(200, json={})

    assert client_with(handler).search_author("x").http_status == 200


def test_a_404_is_returned_because_it_is_a_fact():
    """"No such author" is a statement about coverage and must be storable."""
    def handler(request):
        return httpx.Response(404, json={})

    assert client_with(handler).author("A404").http_status == 404


def test_a_server_error_is_raised():
    def handler(request):
        return httpx.Response(502)

    with pytest.raises(OpenAlexError):
        client_with(handler).search_author("x")


def test_works_are_restricted_to_peer_reviewed_journal_articles():
    """A preprint, a dataset and an erratum are all `works`; counting them as
    collaborations would inflate the number with things never reviewed."""
    seen = {}

    def handler(request):
        seen["filter"] = request.url.params.get("filter")
        return httpx.Response(200, json={"results": []})

    client_with(handler).works_by_author("A123")
    assert PEER_REVIEWED in seen["filter"]
    assert "authorships.author.id:A123" in seen["filter"]


def test_an_id_url_is_reduced_to_a_bare_id():
    seen = {}

    def handler(request):
        seen["path"] = request.url.path
        return httpx.Response(200, json={})

    client_with(handler).author("https://openalex.org/<openalex-author-id>")
    assert seen["path"].endswith("/authors/<openalex-author-id>")


def test_paging_stops_on_a_short_page():
    pages = {"n": 0}

    def handler(request):
        pages["n"] += 1
        count = MAX_PER_PAGE if pages["n"] == 1 else 3
        return httpx.Response(200, json={"results": [work()] * count})

    assert len(list(paged(client_with(handler), "A123", pages=5))) == 2


def test_paging_is_bounded_even_for_a_prolific_author():
    def handler(request):
        return httpx.Response(200, json={"results": [work()] * MAX_PER_PAGE})

    assert len(list(paged(client_with(handler), "A123", pages=3))) == 3


# ── affiliations ────────────────────────────────────────────────────────────

def test_an_author_record_yields_one_claim_per_institution_year():
    """An affiliation stated in 2023 and again in 2024 is two statements, and a
    gap between them is a fact about the record rather than noise to smooth."""
    claims = extract_author(author_record(), raw_ref="h")
    assert len(claims) == 2
    assert {c.valid_from for c in claims} == {date(2023, 1, 1), date(2024, 1, 1)}
    assert all(c.predicate is Predicate.HAS_AFFILIATION for c in claims)


def test_an_affiliation_is_third_party_and_can_never_colour():
    """Nobody bears legal consequence for an affiliation line on a paper."""
    for c in extract_author(author_record(), raw_ref="h"):
        assert c.epistemic_tier is EpistemicTier.THIRD_PARTY


def test_the_institution_is_keyed_on_its_ror_identifier():
    c = extract_author(author_record(), raw_ref="h")[0]
    assert c.object.key == "ror:04qtj9h94"
    assert c.qualifiers["country_code"] == "DK"
    assert c.qualifiers["institution_type"] == "education"


def test_university_and_company_are_the_same_claim():
    """One query answers both halves of the question; the type is a qualifier."""
    record = author_record(affiliations=[
        {"institution": institution("Some University", "CN", "education", "r1"),
         "years": [2024]},
        {"institution": institution("Some Company", "DK", "company", "r2"),
         "years": [2024]}])
    claims = extract_author(record, raw_ref="h")
    assert {c.predicate for c in claims} == {Predicate.HAS_AFFILIATION}
    assert {c.qualifiers["institution_type"] for c in claims} == {"education", "company"}


def test_a_name_search_produces_candidates_not_identities():
    """OpenAlex author clustering collects other people's papers under a common
    name. A hit is never resolved silently."""
    search = {"results": [author_record("A Person", "A1"),
                          author_record("A Person", "A2")]}
    claims = extract_author(search, raw_ref="h", query="A Person")
    assert claims
    assert {c.qualifiers["match"] for c in claims} == {"author_name"}
    assert {c.qualifiers["query"] for c in claims} == {"A Person"}
    # Two distinct author records must not collapse into one subject.
    assert len({c.subject.key for c in claims}) == 2


def test_expanding_a_known_id_is_marked_as_such():
    claims = extract_author(author_record(), raw_ref="h")
    assert {c.qualifiers["match"] for c in claims} == {"author_id"}


def test_an_author_without_a_name_yields_nothing():
    assert extract_author({"id": "https://openalex.org/A1"}, raw_ref="h") == []


def test_an_institution_without_a_name_is_skipped():
    record = author_record(affiliations=[{"institution": {"id": "x"}, "years": [2024]}])
    assert extract_author(record, raw_ref="h") == []


# ── collaboration ───────────────────────────────────────────────────────────

def test_works_yield_affiliations_for_the_subject_and_edges_for_the_rest():
    payload = {"results": [work(co_authors=[
        ("A999", "Co Author", [institution("Peking University", "CN", "education", "r9")])])]}
    claims = extract_works(payload, subject_author_id="A123", raw_ref="h")
    aff = [c for c in claims if c.predicate is Predicate.HAS_AFFILIATION]
    co = [c for c in claims if c.predicate is Predicate.CO_AUTHORED_WITH]
    assert len(aff) == 1 and aff[0].subject.key == "openalex:A123"
    assert len(co) == 1 and co[0].object.label == "Co Author"


def test_a_screen_does_not_open_a_file_on_every_co_author():
    """Recording each co-author's affiliations as standing claims would turn one
    screen into a permanent record about hundreds of uninvolved researchers."""
    payload = {"results": [work(co_authors=[
        ("A999", "Co Author", [institution("Peking University", "CN", "education", "r9")])])]}
    claims = extract_works(payload, subject_author_id="A123", raw_ref="h")
    subjects = {c.subject.key for c in claims if c.predicate is Predicate.HAS_AFFILIATION}
    assert subjects == {"openalex:A123"}


def test_co_authorship_asserts_only_co_authorship():
    payload = {"results": [work(co_authors=[("A999", "Co Author", [institution()])])]}
    co = [c for c in extract_works(payload, subject_author_id="A123", raw_ref="h")
          if c.predicate is Predicate.CO_AUTHORED_WITH][0]
    assert co.qualifiers["asserts"] == "co-authorship only"
    # Never rendered as ownership, control, or association.
    assert co.predicate not in (Predicate.OWNS, Predicate.HAS_ROLE,
                                Predicate.MEMBER_OF_GROUP)


def test_dual_affiliation_is_read_off_one_publication_not_inferred():
    """A person naming two countries on the SAME paper is asserting both at
    once. Two separate records that merely overlap in time are not that."""
    payload = {"results": [work(subject_insts=[
        institution("Technical University of Denmark", "DK", "education", "r1"),
        institution("Beihang University", "CN", "education", "r2")])]}
    claims = extract_works(payload, subject_author_id="A123", raw_ref="h")
    dual = co_stated_countries(claims, "openalex:A123")
    assert dual
    assert sorted(next(iter(dual.values()))[0]["countries"]) == ["CN", "DK"]


def test_a_single_country_publication_is_not_a_dual_affiliation():
    payload = {"results": [work()]}
    claims = extract_works(payload, subject_author_id="A123", raw_ref="h")
    assert co_stated_countries(claims, "openalex:A123") == {}


def test_the_publication_date_carries_onto_every_claim():
    payload = {"results": [work(when="2019-03-04",
                                co_authors=[("A999", "Co", [institution()])])]}
    claims = extract_works(payload, subject_author_id="A123", raw_ref="h")
    assert {c.valid_from for c in claims} == {date(2019, 3, 4)}


def test_an_empty_payload_yields_nothing_rather_than_raising():
    assert extract_works({}, subject_author_id="A123", raw_ref="h") == []
    assert extract_author({}, raw_ref="h") == []


# ── the line this must not cross ────────────────────────────────────────────

def test_nothing_records_or_derives_a_persons_nationality():
    """The unit is an institution a person named on a publication. A country
    code belongs to the institution and never to the human being."""
    payload = {"results": [work(subject_insts=[
        institution("Beihang University", "CN", "education", "r2")],
        co_authors=[("A999", "Co", [institution("Peking University", "CN",
                                                "education", "r9")])])]}
    claims = extract_works(payload, subject_author_id="A123", raw_ref="h")
    for c in claims:
        assert "nationality" not in c.qualifiers
        assert "ethnicity" not in c.qualifiers
        assert c.subject.kind == "person"
        # The country sits on the institution object, not on the person.
        assert not c.subject.key.startswith("cn:")
    aff = [c for c in claims if c.predicate is Predicate.HAS_AFFILIATION][0]
    assert aff.qualifiers["country_code"] == "CN"
    assert aff.object.label == "Beihang University"
