"""Fetch sources and cut passages in code, so every passage is verbatim.

Web tools return a model's rendering of a page, not the page. Here the bytes
are fetched directly, stored content-addressed with a fetch record, and the
text is extracted by `research.source_text`. An agent only chooses URLs and
short anchor phrases; this script cuts the passage around each anchor from the
stored text, and fails if the anchor is not there.

  fetch_sources.py fetch    RUN_DIR --store RAW   (reads RUN_DIR/urls.json)
  fetch_sources.py passages RUN_DIR               (reads RUN_DIR/anchors.json)

urls.json:    [{"url": ..., "publisher": ...}]
anchors.json: [{"source_id": ..., "anchor": "exact words", "before": 300, "after": 600}]
"""
import argparse
import hashlib
import json
import re
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from gleipnir.rawstore import RawStore
from gleipnir.research import source_text

UA = 'Mozilla/5.0 (X11; Linux x86_64) gleipnir-research/0.1 (personal, non-commercial)'
DATE_META = re.compile(
    r'<meta[^>]+(?:property|name|itemprop)=["\'](?:article:published_time|datePublished|'
    r'og:published_time|publish[-_]?date|date|dc\.date|DC\.date\.issued)["\'][^>]*'
    r'content=["\'](\d{4}-\d{2}-\d{2})', re.I)
JSONLD_DATE = re.compile(r'"datePublished"\s*:\s*"(\d{4}-\d{2}-\d{2})')
#: Scholarly pages (arXiv, ACL Anthology) use Highwire tags with slashes.
CITATION_DATE = re.compile(
    r'<meta[^>]+name=["\'](?:citation_(?:online_)?date|citation_publication_date)["\'][^>]*'
    r'content=["\'](\d{4})[/-](\d{2})[/-](\d{2})', re.I)


def date_candidates(raw: str) -> dict[str, str]:
    found = {}
    if m := JSONLD_DATE.search(raw):
        found['json-ld datePublished'] = m.group(1)
    if m := CITATION_DATE.search(raw):
        found['citation meta'] = '-'.join(m.groups())
    if m := DATE_META.search(raw):
        found['meta tag'] = m.group(1)
    return found


def page_date(raw: str) -> str | None:
    """JSON-LD first: some sites render article:published_time at request
    time, so a 2025 article reads as published on the day it was fetched."""
    found = date_candidates(raw)
    return next(iter(found.values()), None)


def fetch(run: Path, store: RawStore):
    sources = []
    for item in json.loads((run / 'urls.json').read_text()):
        url = item['url']
        try:
            req = urllib.request.Request(url, headers={'User-Agent': UA})
            with urllib.request.urlopen(req, timeout=30) as resp:
                payload, status = resp.read(), resp.status
                ctype = resp.headers.get('Content-Type', '')
        except Exception as e:
            print(f'FAIL {url}: {e}', file=sys.stderr)
            continue
        media = 'text/html' if 'html' in ctype else 'text/plain' if 'text/plain' in ctype else None
        rec = store.put(payload=payload, source='web', resource_type='web_page', resource_id=url,
                        http_status=status, request_params={})
        if media is None:
            print(f'SKIP {url}: unsupported content type {ctype}', file=sys.stderr)
            continue
        try:
            text = source_text(payload, media)
        except Exception as e:
            print(f'SKIP {url}: {e}', file=sys.stderr)
            continue
        raw = payload.decode('utf-8', 'replace')
        date, candidates = page_date(raw), date_candidates(raw)
        sid = f"web:{urlparse(url).netloc.removeprefix('www.')}/{rec.content_hash[:10]}"
        (run / 'text').mkdir(exist_ok=True)
        (run / 'text' / f'{rec.content_hash[:16]}.txt').write_text(text)
        sources.append({'id': sid, 'url': url, 'publisher': item.get('publisher'),
                        'sha256': rec.content_hash, 'media_type': media, 'chars': len(text),
                        'published_on': date,
                        'date_basis': next(iter(candidates), 'none'),
                        'date_candidates': candidates,
                        'date_conflict': len(set(candidates.values())) > 1,
                        'text_file': f'text/{rec.content_hash[:16]}.txt',
                        'fetched_at': datetime.now(timezone.utc).isoformat(timespec='seconds')})
        print(f"ok   {sid}  {len(text):7} chars  date={sources[-1]['published_on']}  {url}")
    (run / 'sources.json').write_text(json.dumps(sources, indent=1))


def redate(run: Path, store: RawStore):
    """Re-read dates from the stored bytes (never the web) after a parser fix."""
    sources = json.loads((run / 'sources.json').read_text())
    for s in sources:
        raw = store.get(s['sha256']).decode('utf-8', 'replace')
        s['date_candidates'] = date_candidates(raw)
        s['date_conflict'] = len(set(s['date_candidates'].values())) > 1
        s['published_on'], s['date_basis'] = None, 'none'
        if True:
            date = page_date(raw)
            basis = next(iter(s['date_candidates']), 'none')
            arxiv = re.search(r'arxiv\.org/(?:abs|html|pdf)/(\d{2})(\d{2})\.\d{4,5}', s['url'])
            if not date and arxiv:
                # The identifier encodes the month of first submission.
                date, basis = f'20{arxiv.group(1)}-{arxiv.group(2)}', 'arxiv identifier (first version month)'
            if date:
                s['published_on'], s['date_basis'] = date, basis
                print(f"dated {s['id']} {date} ({basis}){'  CONFLICT ' + str(s['date_candidates']) if s['date_conflict'] else ''}")
    (run / 'sources.json').write_text(json.dumps(sources, indent=1))


def passages(run: Path):
    sources = {s['id']: s for s in json.loads((run / 'sources.json').read_text())}
    out, bad = [], 0
    for i, a in enumerate(json.loads((run / 'anchors.json').read_text())):
        s = sources.get(a['source_id'])
        if not s:
            print(f"FAIL unknown source {a['source_id']}", file=sys.stderr); bad += 1; continue
        text = (run / s['text_file']).read_text()
        at = text.find(' '.join(a['anchor'].split()))
        if at < 0:
            print(f"FAIL anchor not in {a['source_id']}: {a['anchor'][:60]!r}", file=sys.stderr); bad += 1; continue
        start = max(0, at - a.get('before', 300))
        end = min(len(text), at + len(a['anchor']) + a.get('after', 600))
        out.append({'id': f'p{i:03}', 'source_id': s['id'], 'source_date': s['published_on'],
                    'text': text[start:end], 'url': s['url'],
                    'sha256': s['sha256'], 'offsets': [start, end]})
    (run / 'inputs.json').write_text(json.dumps(out, indent=1))
    print(f'{len(out)} passages, {bad} anchors failed')


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('command', choices=['fetch', 'redate', 'passages'])
    ap.add_argument('run', type=Path)
    ap.add_argument('--store', type=Path, default=Path('raw'))
    args = ap.parse_args()
    if args.command == 'fetch':
        fetch(args.run, RawStore(args.store))
    elif args.command == 'redate':
        redate(args.run, RawStore(args.store))
    else:
        passages(args.run)


if __name__ == '__main__':
    main()
