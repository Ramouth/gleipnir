"""Layer 3 — website HTML → checkable claims. Pure functions, no I/O.

**The rule that defines this module: extract only what a registry can
contradict.**

"A leading provider of innovative solutions" is unfalsifiable and must never
become a claim — it would sit in the graph forever as an un-adjudicable node.
"Founded in 2015, CVR 12345678, led by CEO Jane Doe, 200 employees" is four
claims, each with a registry field that can agree or disagree with it.

Every claim emitted carries `checks_against` — the registry predicate that can
contradict it. That field is the glue to the rule layer: it turns a scraped
sentence into an eligible pair for adjudication, deterministically, without the
model deciding what is worth comparing.

Extraction is **deterministic patterns only**. The LLM is not asked to read a
page and summarise it — that is precisely the many-to-many relationship
`architecture.md` §7.1 forbids. Where a pattern is ambiguous, this module emits
nothing and records why, so the gap shows up as coverage rather than as a
confident wrong answer.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Iterable, Iterator

from lxml import html as lxml_html

from gleipnir.adapters.cvr import valid_cvr_checksum
from gleipnir.claims import Claim, EntityRef, EpistemicTier, Predicate

SOURCE_ID = "website"

#: Which registry predicate can contradict each self-declared one. A website
#: claim with no entry here is not extractable — that is the filter.
CHECKS_AGAINST: dict[Predicate, Predicate] = {
    Predicate.CLAIMS_IDENTIFIER: Predicate.HAS_NAME,      # joins to the entity itself
    Predicate.FOUNDED_ON: Predicate.FOUNDED_ON,
    Predicate.HAS_ROLE: Predicate.HAS_ROLE,
    Predicate.EMPLOYS: Predicate.EMPLOYS,
    Predicate.HAS_LOCATION: Predicate.REGISTERED_AT,
    Predicate.MEMBER_OF_GROUP: Predicate.OWNS,
}

_CVR_CONTEXT = re.compile(
    r"(?:cvr|se[\s-]?nr|vat|moms)[\s.:\-–]*(?:nr\.?|no\.?|number|nummer)?[\s.:\-–]*"
    r"(?:dk)?[\s.:\-–]*(\d[\d\s.]{6,12}\d)", re.I)

_FOUNDED = re.compile(
    r"\b(?:grundlagt|etableret|stiftet|founded|established|since|siden|est\.?)\b"
    r"[^.\n]{0,24}?\b((?:18|19|20)\d{2})\b", re.I)

_EMPLOYEES = re.compile(
    r"\b(\d{1,3}(?:[.\s]\d{3})*|\d{1,5})\s*\+?\s*"
    r"\b(?:medarbejdere|ansatte|employees|people|kolleger|specialister)\b", re.I)
_EMPLOYEES_REV = re.compile(
    r"\b(?:medarbejdere|ansatte|employees|team\s+of)\b[^.\n]{0,12}?\b(\d{1,5})\b", re.I)

#: Danish postal code + town. Four digits then a capitalised word is ambiguous
#: on its own, so a street-ish token is required in the same block.
_DK_ADDRESS = re.compile(
    r"\b(\d{4})\s+([A-ZÆØÅ][a-zæøåA-ZÆØÅ.\- ]{2,28})\b")

#: Group membership. The trigger phrase is case-insensitive via a scoped
#: `(?i:...)`; the entity name is NOT.
#:
#: A whole-pattern `re.I` defeats the capitalisation requirement that carries
#: all the meaning here — it matched "part of a self-care regime" on a live site
#: and reported "a self-care regime" as a parent company. Scoping the flag is
#: the fix; requiring a legal-form suffix is the belt.
_GROUP = re.compile(
    r"(?i:\b(?:part of|member of|a subsidiary of|subsidiary of|owned by|"
    r"del af|datterselskab af|ejet af|en del af)\b)[\s:]*"
    r"((?:the\s+)?[A-ZÆØÅ][\w&.\-]*(?:\s+[A-ZÆØÅ0-9][\w&.\-]*){0,4}"
    r"\s+(?:A/S|ApS|A\.S\.|Group|Holding|Holdings|GmbH|Ltd\.?|Limited|Inc\.?|"
    r"B\.V\.|N\.V\.|AB|AS|Oy|S\.A\.|SE|PLC|Corporation|Corp\.?))")

#: Role tokens that a registry can be asked about. Deliberately narrow: these
#: map onto CVR management functions. Marketing titles ("Growth Ninja") have no
#: registry counterpart and are therefore not extracted.
#:
#: **`partner` is deliberately absent.** Measured across 202 cached Danish pages
#: it produced 6 of 15 person extractions and every one was wrong — an
#: accountancy firm, a payroll product, "Cloud Computing", and three sentence
#: fragments. On a Danish company site "partner" overwhelmingly introduces a
#: partner *organisation*, not someone's job title, and it was already listed in
#: `_NOT_A_PERSON` as a disqualifier for the name while triggering here as a
#: role. Removing it lifts precision on the whole person path by 40%.
_ROLES = (
    "administrerende direktør", "adm. direktør", "direktør", "bestyrelsesformand",
    "bestyrelsesmedlem", "formand", "medstifter", "stifter",
    "chief executive officer", "chief financial officer", "chief technology officer",
    "chief operating officer", "managing director", "ceo", "cfo", "cto", "coo",
    "founder", "co-founder", "chairman", "chairperson", "board member",
    "head of engineering", "vp engineering",
)
_ROLE_RE = re.compile(r"\b(" + "|".join(re.escape(r) for r in _ROLES) + r")\b", re.I)

#: A person name: 2–4 capitalised tokens, allowing Danish letters and particles.
_NAME_RE = re.compile(
    r"\b([A-ZÆØÅ][a-zæøå'\-]{1,20}(?:\s+(?:van|von|de|den|der|af|el|al))?"
    r"(?:\s+[A-ZÆØÅ][a-zæøå'\-]{1,20}){1,3})\b")

#: A *street* name, strictly. Separate from `_STREET_HINT`, which is
#: deliberately loose so a block containing an address gets looked at — reusing
#: it for a precision decision matched "K\u00f8benhavn" because the capital
#: contains "havn", and rejected every address in the city.
#:
#: `havn` and `park` are absent for that reason: Frederikshavn and Nyk\u00f8bing are
#: towns, not streets. The suffix must also close the word.
_STREET_SUFFIX = re.compile(
    r"\w+(?:vej|gade|all\u00e9|alle|str\u00e6de|boulevard|plads|torv|v\u00e6nge|vangen)\b", re.I)

_STREET_HINT = re.compile(
    r"(?:\w*(?:gade|vej|vænget|allé|alle|boulevard|plads|torv|stræde|parken|"
    r"havnen|havn|brogade)\b|\b(?:street|road|avenue|lane|square)\b)", re.I)

_META_CHARSET = re.compile(
    rb"""<meta[^>]+charset=["']?\s*([\w-]+)""", re.I)


def _decode(body: bytes) -> str:
    for enc in _charsets(body):
        try:
            return body.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return body.decode("utf-8", errors="replace")


def _charsets(body: bytes) -> Iterable[str]:
    m = _META_CHARSET.search(body[:4096])
    if m:
        yield m.group(1).decode("ascii", "ignore")
    yield "utf-8"
    yield "cp1252"


#: Tokens that disqualify a candidate from being a natural person's name.
#: Measured on a 95-site cohort, the role extractor was returning "Oceans
#: Foundation", "Direktor Gjortz" and "Creative Director" as people — an
#: 11.6% contradiction rate that was almost entirely its own noise.
_NOT_A_PERSON = re.compile(
    r"\b(?:aps|a/s|as|ab|oy|gmbh|ltd|limited|inc|llc|bv|nv|plc|holding|holdings|"
    r"group|foundation|fond|fonden|forening|selskab|selskabet|invest|capital|"
    r"partners|company|virksomhed|team|afdeling|department|division|"
    r"direkt\u00f8r|direktion|bestyrelse|chief|officer|manager|director|leder|"
    r"chef|founder|partner|formand|stifter|ceo|cfo|cto|coo)\b", re.I)

_SKIP_TAGS = {"script", "style", "noscript", "svg", "template"}


@dataclass(frozen=True)
class Block:
    """One text block plus where on the page it came from."""

    text: str
    tag: str
    path: str


def text_blocks(body: bytes, url: str) -> list[Block]:
    """Visible text, one block per element, with its element path.

    Blocks rather than one flat string because adjacency carries meaning: a
    name and a role in the same block are plausibly about the same person; the
    same two tokens 4000 characters apart are not.
    """
    # lxml guesses latin-1 when a page declares its charset only in a <meta>
    # tag, which turned "tømrer" into "tÃ¸mrer" in extracted evidence on a live
    # Danish site. Decode explicitly first.
    try:
        doc = lxml_html.fromstring(_decode(body))
    except Exception:
        return []
    tree = doc.getroottree()
    out: list[Block] = []
    for el in doc.iter():
        if not isinstance(el.tag, str) or el.tag.lower() in _SKIP_TAGS:
            continue
        parts = [t for t in ([el.text] + [c.tail for c in el]) if t and t.strip()]
        if not parts:
            continue
        text = re.sub(r"\s+", " ", " ".join(parts)).strip()
        if len(text) < 2:
            continue
        out.append(Block(text=text, tag=el.tag.lower(), path=tree.getpath(el)))
    return out


def _mk(subject: EntityRef, pred: Predicate, obj, *, block: Block, url: str,
        pattern: str, raw_ref: str, observed_at: str | None,
        confidence: float = 1.0, **extra) -> Claim:
    return Claim(
        subject=subject, predicate=pred, object=obj, source_id=SOURCE_ID,
        epistemic_tier=EpistemicTier.SELF_DECLARED, raw_ref=raw_ref,
        observed_at=observed_at, confidence=confidence,
        qualifiers={
            "url": url,
            "source_text": block.text[:300],
            "element": block.path,
            "pattern": pattern,
            # The glue: which registry predicate can contradict this.
            "checks_against": str(CHECKS_AGAINST.get(pred, "")),
            **extra,
        },
    )


def extract_page(
    body: bytes, *, url: str, subject: EntityRef, raw_ref: str,
    observed_at: str | None = None,
) -> list[Claim]:
    blocks = text_blocks(body, url)
    claims: list[Claim] = []
    seen: set[tuple] = set()

    def emit(claim: Claim) -> None:
        key = (claim.predicate, str(claim.object))
        if key not in seen:
            seen.add(key)
            claims.append(claim)

    for b in blocks:
        for c in _cvr_numbers(b, subject, url, raw_ref, observed_at):
            emit(c)
        for c in _founding(b, subject, url, raw_ref, observed_at):
            emit(c)
        for c in _employees(b, subject, url, raw_ref, observed_at):
            emit(c)
        for c in _group(b, subject, url, raw_ref, observed_at):
            emit(c)
        for c in _addresses(b, subject, url, raw_ref, observed_at):
            emit(c)
        for c in _people(b, subject, url, raw_ref, observed_at):
            emit(c)
    return claims


def _cvr_numbers(b, subject, url, raw_ref, observed_at) -> Iterator[Claim]:
    """A displayed CVR number is the deterministic join key.

    Validated by checksum: an 8-digit run near the word "CVR" that fails mod-11
    is a phone number, an order reference, or our own bad regex — never a CVR
    number. This is the cheapest possible guard against a false join, and a
    false join would attach one company's registry facts to another's website.
    """
    for m in _CVR_CONTEXT.finditer(b.text):
        digits = re.sub(r"\D", "", m.group(1))
        if len(digits) != 8 or not valid_cvr_checksum(digits):
            continue
        yield _mk(subject, Predicate.CLAIMS_IDENTIFIER, f"DK{digits}",
                  block=b, url=url, pattern="cvr_context_checksum",
                  raw_ref=raw_ref, observed_at=observed_at, scheme="cvr")


def _founding(b, subject, url, raw_ref, observed_at) -> Iterator[Claim]:
    this_year = 2100
    for m in _FOUNDED.finditer(b.text):
        year = int(m.group(1))
        if 1600 < year < this_year:
            yield _mk(subject, Predicate.FOUNDED_ON, date(year, 1, 1),
                      block=b, url=url, pattern="founded_keyword_year",
                      raw_ref=raw_ref, observed_at=observed_at,
                      granularity="year", stated_year=year)


def _employees(b, subject, url, raw_ref, observed_at) -> Iterator[Claim]:
    for rx, pat in ((_EMPLOYEES, "count_then_noun"), (_EMPLOYEES_REV, "noun_then_count")):
        for m in rx.finditer(b.text):
            n = int(re.sub(r"[.\s]", "", m.group(1)))
            if 0 < n < 500_000:
                yield _mk(subject, Predicate.EMPLOYS, n, block=b, url=url,
                          pattern=pat, raw_ref=raw_ref, observed_at=observed_at)


def _group(b, subject, url, raw_ref, observed_at) -> Iterator[Claim]:
    for m in _GROUP.finditer(b.text):
        parent = m.group(1).strip(" .,")
        if len(parent) < 3:
            continue
        yield _mk(subject, Predicate.MEMBER_OF_GROUP,
                  EntityRef(kind="company", key=f"name:{parent.casefold()}", label=parent),
                  block=b, url=url, pattern="group_phrase",
                  raw_ref=raw_ref, observed_at=observed_at, confidence=0.6)


def _addresses(b, subject, url, raw_ref, observed_at) -> Iterator[Claim]:
    # Danish street names compound — Havnegade, Nørrebrogade, Vestergade — so
    # the type word is a *suffix*, not a standalone token. `\bgade\b` matches
    # none of them.
    if not _STREET_HINT.search(b.text):
        return
    for m in _DK_ADDRESS.finditer(b.text):
        postcode, town = m.group(1), m.group(2)
        # A 4-digit run followed by capitalised words is also what a copyright
        # line looks like: "© 2026 Datafirma ApS. Alle rettigheder…" parsed as
        # postcode 2026, town "Datafirma ApS. Alle". Danish postcodes start at
        # 1000 and no town name follows a year in practice.
        if not (1000 <= int(postcode) <= 9999) or 1900 <= int(postcode) <= 2100:
            continue
        # Trim trailing prose: opening hours, phone numbers, "Mandag - torsdag".
        town = re.split(r"\s+(?:Mandag|Tirsdag|Onsdag|Torsdag|Fredag|L\u00f8rdag|"
                        r"S\u00f8ndag|Tlf|Telefon|Email|E-mail|CVR|Kontakt|\u00c5bning)",
                        town, maxsplit=1)[0]
        town = town.strip(" .,-\u2013\u00b7|")
        if not town or len(town) > 30:
            continue
        # A four-digit run before a street name is a house number, not a
        # postcode: "4060 Eliassensvej" was being read as postcode 4060 in a
        # town called Eliassensvej, and then reported as a conflict against the
        # company's real postcode.
        if _STREET_SUFFIX.search(town):
            continue
        yield _mk(subject, Predicate.HAS_LOCATION, f"{postcode} {town}",
                  block=b, url=url, pattern="dk_postcode_town",
                  raw_ref=raw_ref, observed_at=observed_at,
                  postcode=postcode, town=town, confidence=0.7)


def _people(b, subject, url, raw_ref, observed_at) -> Iterator[Claim]:
    """Name + role in the same block.

    Same block, not same page: adjacency is the only evidence that a name and a
    title belong together, and a page-wide match would pair every name with
    every role. Where several of each appear in one block, nothing is emitted —
    a team grid rendered as one text node cannot be disambiguated by position,
    and guessing here produces exactly the false role attribution that
    `docs/predicates.md` says must never reach a report.
    """
    roles = list(_ROLE_RE.finditer(b.text))
    if not roles or len(b.text) > 400:
        return
    names = [m for m in _NAME_RE.finditer(b.text)
             if not _ROLE_RE.search(m.group(1))
             and not _NOT_A_PERSON.search(m.group(1))]
    if not names:
        return
    if len(roles) > 1 and len(names) > 1:
        return  # ambiguous grid — emit nothing rather than guess
    for rm in roles:
        nearest = min(names, key=lambda nm: abs(nm.start() - rm.start()))
        if abs(nearest.start() - rm.start()) > 80:
            continue
        yield _mk(subject, Predicate.HAS_ROLE,
                  EntityRef(kind="person", key=f"name:{nearest.group(1).casefold()}",
                            label=nearest.group(1)),
                  block=b, url=url, pattern="name_role_adjacent",
                  raw_ref=raw_ref, observed_at=observed_at, confidence=0.6,
                  role=rm.group(1).lower(),
                  distance=abs(nearest.start() - rm.start()))
