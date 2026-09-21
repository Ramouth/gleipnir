"""Internet Archive — what a company's own account of itself said before.

`poc.md` §3's retrospective backtest requires reconstructing a structure **as it
looked before exposure**. CVR supplies the registry half of that; this supplies
the self-declared half, which exists nowhere else once a page is edited.

**The CDX server is the useful surface, not the snapshots.** Each capture row
carries a `digest` — a content hash — so `collapse=digest` returns one row per
*distinct version* of a page. A single request yields the whole content-change
timeline without fetching a single page, which makes staleness computable at
cohort scale.

Two things that turns on:

**Staleness.** `architecture.md` §10 calls time-since-registry-change the single
best free discriminator: a stale page three weeks after a director change is
expected; four years after, it is telling a different story deliberately. That
needs the date the page last changed, which is what the digest timeline gives.

**Disappeared claims.** §9 notes that a changed self-declared claim is the
distinctive signal almost nobody watches — someone quietly editing a title after
a deal goes wrong. Running the website extractor over an old snapshot and
diffing against today turns that into claims with valid-time bounds.

Free, no credential. Slow — first queries have taken ~17s — and rate-limited, so
everything here is one-at-a-time and cached hard.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any

CDX = "http://web.archive.org/cdx/search/cdx"
SNAPSHOT = "https://web.archive.org/web/{ts}id_/{url}"


@dataclass(frozen=True)
class Capture:
    timestamp: str                  # YYYYMMDDhhmmss, as the archive stores it
    url: str
    status: str
    digest: str                     # content hash — equal digests are equal bytes
    length: int = 0

    @property
    def when(self) -> date:
        return date(int(self.timestamp[:4]), int(self.timestamp[4:6]),
                    int(self.timestamp[6:8]))

    @property
    def ok(self) -> bool:
        return self.status == "200"


@dataclass(frozen=True)
class History:
    """A domain's archived content timeline."""

    url: str
    observed_at: str
    captures: tuple[Capture, ...] = ()

    @property
    def versions(self) -> tuple[Capture, ...]:
        """Successful captures, one per distinct content version, oldest first.

        Redirect and error captures are excluded: a 301 changing its byte length
        is not the page changing, and counting it as a version would put a false
        edit date on the timeline.
        """
        seen, out = set(), []
        for c in sorted(self.captures, key=lambda c: c.timestamp):
            if not c.ok or c.digest in seen:
                continue
            seen.add(c.digest)
            out.append(c)
        return tuple(out)

    @property
    def last_changed(self) -> date | None:
        v = self.versions
        return v[-1].when if v else None

    @property
    def first_seen(self) -> date | None:
        v = self.versions
        return v[0].when if v else None

    def as_of(self, when: date) -> Capture | None:
        """The version in force on a date — the newest capture at or before it.

        Not the nearest capture. The archive may hold nothing for months around
        a date, and answering with a *later* snapshot would report content that
        did not exist yet, which is the one thing a backtest must never do.
        """
        stamp = when.strftime("%Y%m%d999999")
        prior = [c for c in self.versions if c.timestamp <= stamp]
        return prior[-1] if prior else None

    def stale_days(self, registry_changed: date, as_of: date) -> int | None:
        """Days the page has been unchanged since the registry moved.

        Negative means the page changed *after* the registry did, which is the
        ordinary case and not a finding.
        """
        lc = self.last_changed
        if lc is None:
            return None
        return (as_of - max(lc, registry_changed)).days if lc < registry_changed \
            else -( (lc - registry_changed).days )

    def to_dict(self) -> dict[str, Any]:
        return {"url": self.url, "observed_at": self.observed_at,
                "captures": [c.__dict__ for c in self.captures]}


class WaybackClient:
    def __init__(self, http, timeout: float = 60.0) -> None:
        self._http, self.timeout = http, timeout

    def history(self, url: str, *, limit: int = 500) -> History:
        """One CDX request -> the whole content-change timeline.

        `collapse=digest` drops consecutive identical versions server-side, so a
        page captured daily for a decade returns its handful of real edits
        rather than 3,000 rows.
        """
        params = {"url": url, "output": "json", "collapse": "digest",
                  "fl": "timestamp,original,statuscode,digest,length",
                  "limit": str(limit)}
        r = self._http.get(CDX, params=params, timeout=self.timeout)
        r.raise_for_status()
        try:
            rows = r.json()
        except (json.JSONDecodeError, ValueError):
            rows = []
        caps = []
        for row in rows[1:] if rows else []:                # row 0 is the header
            if len(row) < 4:
                continue
            ts, orig, status, digest = row[0], row[1], row[2], row[3]
            try:
                length = int(row[4]) if len(row) > 4 and str(row[4]).isdigit() else 0
            except (TypeError, ValueError):
                length = 0
            caps.append(Capture(timestamp=ts, url=orig, status=status,
                                digest=digest, length=length))
        return History(url=url,
                       observed_at=datetime.now(timezone.utc).isoformat(),
                       captures=tuple(caps))

    def snapshot(self, capture: Capture) -> bytes:
        """The archived bytes of one capture.

        `id_` in the path asks for the original response without the archive's
        own navigation banner injected — otherwise every extraction would find
        Internet Archive markup in the page it is parsing.
        """
        r = self._http.get(SNAPSHOT.format(ts=capture.timestamp, url=capture.url),
                           timeout=self.timeout)
        r.raise_for_status()
        return r.content
