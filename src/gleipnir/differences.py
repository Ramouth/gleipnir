"""Why do sources differ? Candidate explanations, for the LLM to test.

When sources disagree, the easy moves are to take the majority, to average, or
to list both. The reason they differ is often the finding: two counts with
different end points, a definition that differs, an announcement against the
date something took effect, a source quoting someone else, one outlet copying
another. Code cannot say which reason holds. It can lay out the usual ones,
with the evidence already in the atoms, and ask the LLM to test them against
the text before it chooses. An explanation is recorded only with a quote.
"""
from __future__ import annotations

import re
from itertools import combinations

WHY_QUESTION = (
    'These sources disagree. Before you choose between them or report both, explain why they '
    'differ. Consider: a different time (when each source spoke, and what time the claim is '
    'about); a different definition, scope or counting rule (start and end points, what is '
    'included); who is actually speaking (the source itself, or someone it quotes); one source '
    'copying another; a hedge or estimate; a plain error. Say which explanation the text supports '
    'and quote the words that show it. If nothing in the text explains the difference, say so: '
    'that is a finding too.')

REASONS = ('time', 'definition', 'speaker', 'copying', 'hedge', 'error', 'unexplained')
NUMBER = re.compile(r'(?<![\w.])(\d+(?:[.,]\d+)?)\s*(%|percent|procent|seats|mandater|days|dage|'
                    r'million|mio|billion|mia|bn|kr|dkk|usd|eur|\$)?', re.I)


def _numbers(text: str) -> list[tuple[float, str]]:
    out = []
    for value, unit in NUMBER.findall(text or ''):
        try:
            out.append((float(value.replace(',', '.')), (unit or '').lower()))
        except ValueError:
            pass
    return out


def _gap_days(a: str | None, b: str | None) -> int | None:
    from datetime import date
    if not a or not b:
        return None
    pa, pb = (x.split('-') + ['01', '01'] for x in (a, b))
    try:
        da = date(int(pa[0]), int(pa[1]), int(pa[2]))
        db = date(int(pb[0]), int(pb[1]), int(pb[2]))
    except ValueError:
        return None
    return abs((da - db).days)


def candidates(a: dict, b: dict) -> list[dict]:
    """Mechanical differences between two rows of a compare group. Each is a
    lead to check against the text, never a conclusion."""
    out = []
    gap = _gap_days(a.get('published'), b.get('published'))
    if gap:
        out.append({'kind': 'time', 'lead': f'the sources were published {gap} days apart '
                    f'({a.get("published")} vs {b.get("published")}); the later one may report a change'})
    ha, hb = a.get('holds') or {}, b.get('holds') or {}
    if (ha.get('start'), ha.get('end')) != (hb.get('start'), hb.get('end')) and \
            'open' not in (ha.get('basis'), hb.get('basis')):
        out.append({'kind': 'time', 'lead': f'the claims are about different times '
                    f'({ha.get("start")}–{ha.get("end")} vs {hb.get("start")}–{hb.get("end")})'})
    oa, ob = a.get('origin'), b.get('origin')
    if oa and oa == ob:
        out.append({'kind': 'copying', 'lead': f'both sources are declared as origin "{oa}": '
                    'one may repeat the other, so the difference may be an edit or an error in copying'})
    elif not oa or not ob:
        out.append({'kind': 'copying', 'lead': 'at least one source has no declared origin: '
                    'whether they are independent is unknown'})
    sa, sb = a.get('speakers') or [], b.get('speakers') or []
    if sa[1:] != sb[1:]:
        who = lambda s: ' → '.join(s[1:]) or 'the source itself'
        out.append({'kind': 'speaker', 'lead': f'different speakers: {who(sa)} vs {who(sb)}'})
    if bool(a.get('hedge')) != bool(b.get('hedge')):
        out.append({'kind': 'hedge', 'lead': 'one claim is hedged or estimated, the other is not'})
    if a.get('polarity') != b.get('polarity'):
        out.append({'kind': 'definition', 'lead': 'one affirms and one denies: check whether they '
                    'mean the same thing by the words (for example "majority" of what)'})
    na = _numbers(f"{a.get('value') or ''} {a.get('statement') or ''}")
    nb = _numbers(f"{b.get('value') or ''} {b.get('statement') or ''}")
    if na and nb:
        units_a, units_b = {u for _, u in na if u}, {u for _, u in nb if u}
        if units_a and units_b and not units_a & units_b:
            out.append({'kind': 'definition', 'lead': f'the numbers have different units '
                        f'({", ".join(sorted(units_a))} vs {", ".join(sorted(units_b))})'})
        va, vb = na[0][0], nb[0][0]
        if va != vb and max(va, vb) and abs(va - vb) / max(va, vb) <= 0.1:
            out.append({'kind': 'definition', 'lead': f'{va:g} and {vb:g} are close: a different start '
                        'or end point, rounding, or counting rule is likely'})
    out.append({'kind': 'error', 'lead': 'one of them may simply be wrong; say what would show which'})
    return out


def why_differ(rows: list[dict]) -> dict | None:
    """For a compare group whose sources disagree: candidate explanations per
    pair, and the question the LLM must answer before choosing."""
    values = {((r.get('value') or '').casefold().strip(), r.get('polarity')) for r in rows}
    if len(values) < 2 or len({r['source'] for r in rows}) < 2:
        return None
    pairs = []
    for a, b in combinations(rows, 2):
        if a['source'] == b['source'] or ((a.get('value') or '').casefold().strip(), a.get('polarity')) == \
                ((b.get('value') or '').casefold().strip(), b.get('polarity')):
            continue
        pairs.append({'sources': [a['source'], b['source']],
                      'values': [a.get('value'), b.get('value')],
                      'candidates': candidates(a, b)})
    return {'pairs': pairs[:6], 'question': WHY_QUESTION} if pairs else None
