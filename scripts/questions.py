"""Generate candidate questions from the corpus, instead of authoring them.

    .venv/bin/python scripts/questions.py --lead=365

Every question in `flags.py` and `goals.py` was written by hand. Exactly one of
them has ever paid: `registered_audit_election_absent`, and it was not authored
— it was **found**, by noticing that one filed attribute is present on 99.9% of
active companies and 33.8% of struck-off ones. That procedure is mechanical, so
it should not be a thing a person happens to notice once.

This enumerates every fact the register files about a company, measures its
presence in matched case/control pairs, and ranks what separates them. It does
not produce findings. It produces **questions** — each one still needs a
mechanism before it can become a rule, and `REVISION_FRAVALGT` is the worked
example of why: the separation was real and the mechanism turned out to be
"this company never filed accounts", which is administrative death, not
concealment.

**Two tiers, kept apart, because one of them is hindsight.**

    AS-OF     filings that carry a validity period, evaluated at the as-of date.
              Sound: nothing the collapse caused can leak in.
    DOCUMENT  presence of a JSON path in today's document. A path that appears
              BECAUSE the company failed will separate perfectly and mean
              nothing. Ranked separately and never promoted without a period.

**Scanning hundreds of paths will produce spurious separations.** At 200 tests,
ten hit p<0.05 by chance. The interval is Bonferroni-corrected to the number of
paths actually tested, and the correction is printed, so a reader can see what
the threshold was rather than trusting that one was applied.
"""
from __future__ import annotations

import math
import sys
from collections import Counter, defaultdict
from datetime import date

from gleipnir.backtest import (MATCH_LEVELS, _parse_date, _unwrap, clean_controls,
                               katz_ci, load_cohorts, match_pairs)
from gleipnir.config import settings
from gleipnir.rawstore import RawStore

WINDOW = (date(2005, 1, 1), date(2025, 8, 27))

#: Already a predicate. Listed so the generator's output shows what it would
#: have found, which is the only available check that it works.
KNOWN = {"attributter:REVISION_FRAVALGT"}

#: Paths that identify the company rather than describe it. Including them
#: finds that struck-off companies have different CVR numbers than active ones.
BORING = ("cvrNummer", "nyesteNavn", "enhedsNummer", "samtId", "sidstOpdateret",
          "sidstIndlaest", "PSEUDOCVRNR", "ARKIV_REGISTRERINGSNUMMER",
          "NAVN_IDENTITET", "virksomhedsstatus", "sammensatStatus", "livsforloeb")


def _in_force(entry: dict, on: date) -> bool:
    """In force at `on`, and REQUIRING a start date to say so.

    A `periode` of `{gyldigFra: null, gyldigTil: null}` is not "valid forever";
    it is "the register did not date this". Admitting it at a past date is how
    the first run of this generator reported a **liquidator in office 365 days
    before bankruptcy** in 789 of 2,126 cases against 1 control — a ratio of
    789. All 862 hits carried a null period. The liquidator was appointed by
    the bankruptcy.

    Same defect as `sammensatStatus`, one layer down, and `claims.at()` has it
    too: it admits a claim with no `valid_from` at every as-of date. Closed here
    because a backtest is where it does damage, and it fails flattering.
    """
    period = entry.get("periode") or {}
    frm = _parse_date(period.get("gyldigFra"))
    if frm is None:
        return False
    to = _parse_date(period.get("gyldigTil"))
    return frm <= on and (to is None or to >= on)


def _rateable(raw: str) -> bool:
    """Is this value low-cardinality enough to be a question?

    `FØRSTE_REGNSKABSPERIODE_START=2018-11-01` took nine of the first fourteen
    rows. A specific first-accounting-period date is an incorporation-cohort
    artefact of how the strata were sampled, not a property of failing
    companies, and there are thousands of them so one always separates.
    """
    if _parse_date(raw) is not None:
        return False
    return raw.lower() in ("true", "false") or (len(raw) < 12 and not raw[:1].isdigit())


def asof_features(doc: dict, on: date) -> set[str]:
    """Facts filed about the company that were in force at `on`."""
    out: set[str] = set()
    for attribute in doc.get("attributter") or []:
        kind = attribute.get("type")
        if not kind or kind in BORING:
            continue
        for value in attribute.get("vaerdier") or []:
            if not _in_force(value, on):
                continue
            out.add(f"attributter:{kind}")
            raw = str(value.get("vaerdi"))[:40]
            if _rateable(raw):
                out.add(f"attributter:{kind}={raw}")
            break
    for relation in doc.get("deltagerRelation") or []:
        kind = (relation.get("deltager") or {}).get("enhedstype")
        for org in relation.get("organisationer") or []:
            for name in org.get("organisationsNavn") or []:
                if not _in_force(name, on):
                    continue
                out.add(f"role:{org.get('hovedtype')}:{name.get('navn')}")
                if kind:
                    out.add(f"participant:{kind}")
    return out


def document_paths(doc: dict, prefix: str = "", depth: int = 0) -> set[str]:
    """Every JSON path present in the document, list indices collapsed."""
    if depth > 3:
        return set()
    out: set[str] = set()
    if isinstance(doc, dict):
        for key, value in doc.items():
            if key in BORING or value in (None, [], {}):
                continue
            path = f"{prefix}.{key}" if prefix else key
            out.add(path)
            out |= document_paths(value, path, depth + 1)
    elif isinstance(doc, list) and doc:
        out |= document_paths(doc[0], prefix, depth + 1)
    return out


def measure(pairs, extract, corrected_z):
    """-> (rows, n). Features separating identically are collapsed into one row.

    `TEGNINGSREGEL`, `FORMÅL` and `VEDTÆGT_SENESTE` came back at exactly
    90.8% vs 68.1% — three rows for one variable, because a company that filed
    articles filed all three. Reporting them separately triples the apparent
    number of questions and inflates the Bonferroni correction against the
    real ones.
    """
    case_n, ctrl_n = Counter(), Counter()
    membership: dict[str, tuple[frozenset, frozenset]] = {}
    case_sets: dict[str, set] = defaultdict(set)
    ctrl_sets: dict[str, set] = defaultdict(set)
    for i, pair in enumerate(pairs):
        for subject, counter, sets in ((pair.case, case_n, case_sets),
                                       (pair.control, ctrl_n, ctrl_sets)):
            for feature in extract(subject, pair.as_of):
                counter[feature] += 1
                sets[feature].add(i)

    by_signature: dict[tuple, list[str]] = defaultdict(list)
    for feature in set(case_n) | set(ctrl_n):
        by_signature[(frozenset(case_sets[feature]),
                      frozenset(ctrl_sets[feature]))].append(feature)

    n = len(pairs)
    rows = []
    for names in by_signature.values():
        names.sort()
        feature = names[0] + (f"  (+{len(names) - 1} identical)" if len(names) > 1 else "")
        a, c = case_n[names[0]], ctrl_n[names[0]]
        if a + c < 20:            # too rare to rate at this corpus size
            continue
        # Both directions are questions. A fact present in controls and absent
        # in cases is exactly the shape REVISION_FRAVALGT had.
        ratio = ((a or 0.5) / n) / ((c or 0.5) / n)
        ci = katz_ci(a, n, c, n, corrected_z)
        rows.append((ratio, feature, a, c, ci))
    return sorted(rows, key=lambda r: -max(r[0], 1 / r[0] if r[0] else 0)), n


def report(title, rows, n, tested, alpha_note, limit=14):
    print(f"\n{title}   {n} matched pairs, {tested} facts tested")
    print(f"  {alpha_note}")
    print(f"  {'fact':<52}{'cases':>13}{'controls':>13}{'ratio':>9}{'corrected CI':>20}")
    print("  " + "-" * 105)
    shown = 0
    for ratio, feature, a, c, ci in rows:
        if ci is None or (ci[0] <= 1.0 <= ci[1]):
            continue                      # survives no correction; not a question
        mark = "  <- already a predicate" if feature in KNOWN else ""
        print(f"  {feature[:52]:<52}{f'{a}/{n} {a / n:5.1%}':>13}"
              f"{f'{c}/{n} {c / n:5.1%}':>13}{ratio:>9.2f}"
              f"{f'[{ci[0]:.2f}, {ci[1]:.2f}]':>20}{mark}")
        shown += 1
        if shown >= limit:
            break
    if not shown:
        print("  nothing survives the correction — no question at this corpus size")


def main() -> int:
    flags = {a.split("=", 1)[0]: a.split("=", 1)[-1] for a in sys.argv[1:] if a.startswith("--")}
    lead = int(flags.get("--lead", 365))
    tag = flags.get("--tag", "outcome")

    store = RawStore(settings.raw_store_path)
    cohorts = load_cohorts(store, tag)
    controls, _ = clean_controls(cohorts.get("control", []))
    docs = {}
    for fetch in store.fetches():
        if fetch.resource_type != "bulk_page" or not fetch.resource_id.startswith(f"{tag}:"):
            continue
        for hit in store.get_json(fetch.content_hash):
            doc = _unwrap(hit)
            docs[str(doc.get("cvrNummer")).zfill(8)] = doc

    print(f"question generator   lead {lead}d   matched on "
          f"{', '.join(MATCH_LEVELS['full'])}")
    print("Candidates, not findings. A separation is a question; it needs a")
    print("mechanism before it may become a rule.")

    for name, cases in sorted(cohorts.items()):
        if name == "control":
            continue
        pairs, _ = match_pairs(cases, controls, lead, WINDOW, MATCH_LEVELS["full"])
        if not pairs:
            continue

        # Two passes: count the tests first so the correction is honest rather
        # than fitted to whatever survived.
        def asof(subject, on):
            return asof_features(docs.get(subject.cvr, {}), on)

        def whole(subject, on):
            return document_paths(docs.get(subject.cvr, {}))

        for label, extract, caveat in (
                ("AS-OF — filings in force at the as-of date", asof, ""),
                ("DOCUMENT — path present today; HINDSIGHT-PRONE, never promote "
                 "without a period", whole, "")):
            rows, n = measure(pairs, extract, 1.96)
            tested = len(rows)
            z = 1.96 if tested <= 1 else abs(_z_for(0.05 / tested))
            rows, n = measure(pairs, extract, z)
            report(f"{name}  ·  {label}", rows, n, tested,
                   f"Bonferroni: {tested} tests, alpha 0.05 -> "
                   f"{0.05 / max(tested, 1):.2g} per test, z = {z:.2f}")
    return 0


def _z_for(p: float) -> float:
    """Two-sided normal quantile. Acklam's rational approximation.

    Written out rather than pulled in: scipy is not a dependency and the whole
    correction is one number, which must be reproducible from this file alone.
    """
    q = p / 2
    a = (-39.69683028665376, 220.9460984245205, -275.9285104469687,
         138.3577518672690, -30.66479806614716, 2.506628277459239)
    b = (-54.47609879822406, 161.5858368580409, -155.6989798598866,
         66.80131188771972, -13.28068155288572)
    c = (-0.007784894002430293, -0.3223964580411365, -2.400758277161838,
         -2.549732539343734, 4.374664141464968, 2.938163982698783)
    d = (0.007784695709041462, 0.3224671290700398, 2.445134137142996,
         3.754408661907416)
    if q < 0.02425:
        t = math.sqrt(-2 * math.log(q))
        return -(((((c[0] * t + c[1]) * t + c[2]) * t + c[3]) * t + c[4]) * t + c[5]) / \
            ((((d[0] * t + d[1]) * t + d[2]) * t + d[3]) * t + 1)
    t = q - 0.5
    r = t * t
    return -(((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * t / \
        (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1)


if __name__ == "__main__":
    raise SystemExit(main())
