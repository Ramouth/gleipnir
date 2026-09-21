"""One company, one date, facts only.

    .venv/bin/python scripts/factsheet.py 99000325
    .venv/bin/python scripts/factsheet.py 99000325 --as-of=2013-11-07

The booleans are scaffolding. They decide which section a line lands in and
they do not appear in the output: `TRUE`/`FALSE`/`UNKNOWN`/`UNKNOWABLE` become
"established", "checked, positively absent", "answerable by spending more" and
"no connected source covers it". What the reader sees is the register's own
facts, each with the denominator that makes it information.

With no `--as-of`, a company that later failed is read **365 days before its
first forced-dissolution or bankruptcy transition**, so nothing on the sheet is
hindsight. The header says which date was used and why.

No score. No ranking. No adjective. The sheet does not say what the facts mean.

The sheet itself is assembled by `gleipnir.factsheet`; this file only prints it,
so the terminal and the workbench cannot drift apart about what the register
says.
"""
from __future__ import annotations

import sys
from datetime import date

from gleipnir.adapters.opensanctions import SanctionsIndex
from gleipnir.config import settings
from gleipnir.factsheet import NotInStore, Sheet, build
from gleipnir.rawstore import RawStore


def render(sheet: Sheet) -> str:
    out: list[str] = []
    p = out.append

    p(f"\n{'=' * 104}")
    p(f"CVR {sheet.cvr}   {sheet.name}")
    p(f"{sheet.legal_form}, registered {sheet.founded}   ·   blob {sheet.blob[:12]}")
    p(f"register read as of {sheet.as_of} — {sheet.as_of_reason}")
    p("=" * 104)

    p("\n  REGISTRY STATUS, AS FILED")
    for frm, to, value in sheet.status_history:
        marker = "  <- as-of date falls here" if sheet.covers(frm, to) else ""
        p(f"      {str(frm):<12}{str(to or 'open'):<12}{value}{marker}")

    for section in sheet.sections:
        p(f"\n  {section.heading}  ({section.count} of {section.of_total})")
        for row in section.rows:
            # The question always leads. An evidence string alone is not a fact:
            # "2018-04-30  [1 date(s)]" says nothing without what it answers.
            raw = f"  [{row.raw}]" if row.raw is not None else ""
            p(f"      {row.question} — {row.evidence}{raw}")
            for line in row.comparator:
                p(f"        {line}")

    # The rule layer. Facts above, flags here — and where a fired boolean can
    # raise neither, the named question that would settle it.
    p(f"\n  FLAGS  ({len(sheet.raised)} raised)")
    if not sheet.raised:
        p("      none. No fired boolean is documented involvement, and structure")
        p("      is never a colour — finding.py refuses a red without a ground,")
        p("      an authority and a citation.")
    for rule, r in sheet.raised:
        p(f"      {rule.colour.upper()}  {rule.ground}  ({rule.qualifier})")
        p(f"        {r.evidence}")

    if sheet.escalate:
        p(f"\n  NEXT QUESTIONS  ({len(sheet.escalate)}) — what the model is allowed to answer")
        for rule, r in sheet.escalate:
            p(f"      [{rule.source}] {rule.question}")
            p(f"        raised by: {r.predicate}  [{r.raw}]")
            p(f"        would change it: {rule.would_change}")

    if sheet.facts:
        p(f"\n  RATED, MAY NEVER BECOME A COLOUR  ({len(sheet.facts)})")
        for rule, r in sheet.facts:
            p(f"      {r.predicate} — {rule.holds_as}")

    if sheet.ungoverned:
        p(f"\n  NOT COVERED BY THE RULE TABLE  ({len(sheet.ungoverned)}) — a gap in flags.py")
        for _, r in sheet.ungoverned:
            p(f"      {r.predicate} = {r.value}")

    if sheet.blind:
        p(f"\n  {len(sheet.blind)} of {len(sheet.results)} questions have no answer in "
          f"CVR at this date.")
    if sheet.register_note:
        p("  Denmark's ownership register opened in June 2015. Read before then, a "
          "company\n  with no filed owners is a company the register did not yet "
          "ask about.")
    return "\n".join(out)


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = {a.split("=", 1)[0]: a.split("=", 1)[-1] for a in sys.argv[1:] if a.startswith("--")}
    if not args:
        print(__doc__)
        return 2
    as_of = date.fromisoformat(flags["--as-of"]) if "--as-of" in flags else None

    store = RawStore(settings.raw_store_path)
    rec = store.latest("opensanctions", "targets.simple.csv", "sanctions")
    index = SanctionsIndex.from_csv(store.path_of(rec.content_hash)) if rec else None
    status = 0
    for cvr in args:
        try:
            print(render(build(store, cvr, index, as_of)))
        except NotInStore:
            print(f"\nCVR {cvr}  not in the raw store")
            status = 1
    return status


if __name__ == "__main__":
    raise SystemExit(main())
