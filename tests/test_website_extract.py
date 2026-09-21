from datetime import date

from gleipnir.claims import EntityRef, EpistemicTier, Predicate
from gleipnir.extract.website import extract_page, text_blocks

SUBJECT = EntityRef(kind="company", key="99000147", label="Acme")

PAGE = b"""<html><body>
<h1>Acme International Vodka</h1>
<p>We are a leading provider of innovative, world-class synergistic solutions.</p>
<p>Founded in 2015 in Copenhagen. We have 200 employees across four countries.</p>
<div class="team"><p>Jane Doe, Chief Executive Officer</p></div>
<div class="team"><p>Lars Bo Nielsen &mdash; CTO</p></div>
<footer>Havnegade 12, 1058 K&oslash;benhavn K &middot; CVR-nr. 99000147</footer>
<p>Acme is part of Nordic Spirits Holding A/S.</p>
<script>var cvr = "11111111";</script>
</body></html>"""


def _claims():
    return extract_page(PAGE, url="https://acme.example/", subject=SUBJECT,
                        raw_ref="abc123", observed_at="2026-08-27")


def test_unfalsifiable_marketing_is_not_extracted():
    """'leading provider of innovative solutions' has no registry counterpart
    and must never enter the graph."""
    for c in _claims():
        assert "innovative" not in str(c.object).lower()
        assert "leading" not in str(c.object).lower()


def test_displayed_cvr_is_extracted_and_checksummed():
    c = next(c for c in _claims() if c.predicate is Predicate.CLAIMS_IDENTIFIER)
    assert c.object == "DK99000147"


def test_invalid_cvr_in_script_is_ignored():
    """11111111 fails mod-11 and sits inside a <script>. Two reasons to skip."""
    assert not any(str(c.object) == "DK11111111" for c in _claims())


def test_founding_year():
    c = next(c for c in _claims() if c.predicate is Predicate.FOUNDED_ON)
    assert c.object == date(2015, 1, 1)


def test_employee_count():
    c = next(c for c in _claims() if c.predicate is Predicate.EMPLOYS)
    assert c.object == 200


def test_person_and_role():
    roles = {c.qualifiers["role"]: c.object.label
             for c in _claims() if c.predicate is Predicate.HAS_ROLE}
    assert roles.get("chief executive officer") == "Jane Doe"
    assert roles.get("cto") == "Lars Bo Nielsen"


def test_group_membership():
    c = next(c for c in _claims() if c.predicate is Predicate.MEMBER_OF_GROUP)
    assert "Nordic Spirits Holding" in c.object.label


def test_address():
    c = next(c for c in _claims() if c.predicate is Predicate.HAS_LOCATION)
    assert c.qualifiers["postcode"] == "1058"


def test_every_claim_is_self_declared_and_carries_provenance():
    for c in _claims():
        assert c.epistemic_tier is EpistemicTier.SELF_DECLARED
        assert c.qualifiers["url"] == "https://acme.example/"
        assert c.qualifiers["source_text"]
        assert c.qualifiers["element"].startswith("/")
        assert c.qualifiers["pattern"]


def test_every_claim_declares_what_can_contradict_it():
    """The KT glue: a claim with no registry counterpart is not extractable."""
    for c in _claims():
        assert c.qualifiers["checks_against"], f"{c.predicate} has no counterpart"


def test_ambiguous_team_grid_emits_nothing():
    """Several names and several roles in one text node cannot be paired by
    position. Emitting nothing beats guessing a wrong role attribution."""
    grid = b"<html><body><div>Jane Doe CEO Lars Nielsen CTO Bo Hansen CFO</div></body></html>"
    out = extract_page(grid, url="u", subject=SUBJECT, raw_ref="r")
    assert not [c for c in out if c.predicate is Predicate.HAS_ROLE]


def test_script_and_style_are_stripped():
    assert not any("var cvr" in b.text for b in text_blocks(PAGE, "u"))


def test_group_phrase_requires_a_real_company_name():
    """Regression from a live site: a whole-pattern re.I made [A-ZÆØÅ] match
    lowercase, so 'part of a self-care regime' was reported as a parent."""
    page = b"<html><body><p>Sunlight is part of a self-care regime.</p></body></html>"
    out = extract_page(page, url="u", subject=SUBJECT, raw_ref="r")
    assert not [c for c in out if c.predicate is Predicate.MEMBER_OF_GROUP]


def test_group_phrase_still_matches_a_real_parent():
    page = b"<html><body><p>Acme is part of Nordic Spirits Holding A/S.</p></body></html>"
    out = extract_page(page, url="u", subject=SUBJECT, raw_ref="r")
    c = next(c for c in out if c.predicate is Predicate.MEMBER_OF_GROUP)
    assert c.object.label == "Nordic Spirits Holding A/S"


def test_organisations_and_titles_are_not_extracted_as_people():
    """Measured on a 95-site cohort: the role extractor returned 'Oceans
    Foundation', 'Direktør Gjørtz' and 'Creative Director' as natural persons.
    An 11.6% contradiction rate that was almost entirely its own noise."""
    for text in (b"<p>Oceans Foundation, partner</p>",
                 b"<p>Creative Director, partner</p>",
                 b"<p>Nordic Capital Partners, founder</p>"):
        out = extract_page(b"<html><body>" + text + b"</body></html>",
                           url="u", subject=SUBJECT, raw_ref="r")
        assert not [c for c in out if c.predicate is Predicate.HAS_ROLE], text


def test_a_real_person_and_role_still_extracts():
    out = extract_page(b"<html><body><p>Jane Doe, Chief Executive Officer</p></body></html>",
                       url="u", subject=SUBJECT, raw_ref="r")
    c = next(c for c in out if c.predicate is Predicate.HAS_ROLE)
    assert c.object.label == "Jane Doe"


def test_partner_is_not_treated_as_a_person_role():
    """Measured across 202 Danish pages: 'partner' produced 6 of 15 person
    extractions and all 6 were wrong — an accountancy firm, a payroll product,
    'Cloud Computing' and three sentence fragments. On a Danish site it
    introduces a partner organisation, not a job title."""
    for text in (b"<p>Christensen Kjaerulff, partner</p>",
                 b"<p>Cloud Computing - partner</p>",
                 b"<p>Jane Doe, partner</p>"):
        out = extract_page(b"<html><body>" + text + b"</body></html>",
                           url="u", subject=SUBJECT, raw_ref="r")
        assert not [c for c in out if c.predicate is Predicate.HAS_ROLE], text


def test_registry_mappable_roles_still_extract():
    out = extract_page(b"<html><body><p>Jane Doe, adm. direkt&oslash;r</p></body></html>",
                       url="u", subject=SUBJECT, raw_ref="r")
    c = next(c for c in out if c.predicate is Predicate.HAS_ROLE)
    assert c.object.label == "Jane Doe"


def test_a_house_number_before_a_street_is_not_a_postcode():
    """'4060 Eliassensvej' was read as postcode 4060 in a town called
    Eliassensvej, then reported as a conflict against the real postcode."""
    page = b"<html><body><p>Kontor: 4060 Eliassensvej, Danmark</p></body></html>"
    out = extract_page(page, url="u", subject=SUBJECT, raw_ref="r")
    assert not [c for c in out if c.predicate is Predicate.HAS_LOCATION]


def test_a_real_postcode_and_town_still_extract():
    page = b"<html><body><p>Havnegade 12, 1058 K&oslash;benhavn K</p></body></html>"
    c = next(c for c in extract_page(page, url="u", subject=SUBJECT, raw_ref="r")
             if c.predicate is Predicate.HAS_LOCATION)
    assert c.qualifiers["postcode"] == "1058"


def test_a_town_containing_a_street_word_is_still_a_town():
    """The loose block-detection pattern matched 'København' because the capital
    contains 'havn', which rejected every address in the city. Frederikshavn is
    a town too."""
    from gleipnir.extract.website import _STREET_SUFFIX
    for town in ("København K", "Frederikshavn", "Nykøbing F", "Rønnede", "Aarup"):
        assert not _STREET_SUFFIX.search(town), town
    for street in ("Eliassensvej", "Vestergade", "Havnegade", "Industrivej"):
        assert _STREET_SUFFIX.search(street), street
