"""What the workbench holds open, and what it refuses to compute.

The state here is a cache and nothing else. Every number on a page is produced
by the same library call the CLI makes — `factsheet.build`, `screen.run`,
`analyst.active_flag` — so a page cannot show a fact the terminal would not, and
a template cannot invent one. That is the whole design of this layer: a view
model assembled by code that already has tests, handed to a template that can
only iterate and escape.

Two things are cached because they are slow and immutable between fetches: the
directorship index (nine seconds over 115,000 officers) and the sanctions index.
Both are keyed on the raw store's fetch log, so ingesting anything new drops
them rather than serving a stale denominator.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from gleipnir import analyst as analyst_mod
from gleipnir.adapters.opensanctions import SanctionsIndex
from gleipnir.backtest import subject_from_hit
from gleipnir.calibration import build_directorship_index
from gleipnir.factsheet import Sheet, build as build_sheet, unwrap_hit
from gleipnir.investigation import ReviewOutcome, ReviewPacket, store_review_packet
from gleipnir.rawstore import RawStore
from gleipnir.screen import Sources, run as run_screen


@dataclass(frozen=True)
class CompanyRow:
    """One line of the index: enough to choose from, nothing interpretive.

    `event` is the register's own adverse transition where it recorded one — a
    filed fact with a date, not a verdict. It is on the index because a list of
    companies with no way to tell which ones the registrar has already acted
    against makes the reader open all of them.
    """

    cvr: str
    name: str
    status: str
    legal_form: str
    founded: date | None
    event: date | None
    event_status: str | None
    fetched_at: str
    content_hash: str

    @property
    def readable(self) -> bool:
        """Did the stored document parse? An unreadable payload stays on the
        index under its CVR number rather than vanishing from it."""
        return bool(self.name or self.legal_form)


@dataclass
class Workbench:
    """Everything a request may read. Nothing here decides anything."""

    store: RawStore
    #: Registry budget. Zero means the workbench never spends quota, which is
    #: the only safe default for a surface a click can drive.
    budget: int = 0
    _sanctions: Any = field(default=None, init=False, repr=False)
    _directorships: dict[str, int] | None = field(default=None, init=False, repr=False)
    _companies: list[CompanyRow] | None = field(default=None, init=False, repr=False)
    _stamp: tuple[int, int] | None = field(default=None, init=False, repr=False)

    # ── cache validity ───────────────────────────────────────────────────────
    def _current_stamp(self) -> tuple[int, int]:
        """Size and mtime of the append-only log. Cheaper than rereading it, and
        an append-only file cannot change without changing both."""
        p = Path(self.store.log_path)
        if not p.exists():
            return (0, 0)
        st = p.stat()
        return (st.st_size, int(st.st_mtime))

    def _fresh(self) -> None:
        stamp = self._current_stamp()
        if stamp != self._stamp:
            self._stamp = stamp
            self._sanctions = None
            self._directorships = None
            self._companies = None

    # ── the slow, cached indices ─────────────────────────────────────────────
    @property
    def sanctions(self):
        self._fresh()
        if self._sanctions is None:
            rec = self.store.latest("opensanctions", "targets.simple.csv", "sanctions")
            self._sanctions = (SanctionsIndex.from_csv(self.store.path_of(rec.content_hash))
                               if rec else False)
        return self._sanctions or None

    @property
    def directorships(self) -> dict[str, int] | None:
        self._fresh()
        if self._directorships is None:
            self._directorships = build_directorship_index(self.store)
        return self._directorships

    # ── the index page ───────────────────────────────────────────────────────
    def companies(self) -> list[CompanyRow]:
        """Every company in the raw store, newest fetch first.

        The name and status are read out of the cached document rather than
        recomputed, because this list is navigation. Anything a reader might
        act on is computed on the company's own page, under an as-of date.
        """
        self._fresh()
        if self._companies is not None:
            return self._companies
        latest: dict[str, Any] = {}
        for f in self.store.fetches():
            if f.source == "cvr" and f.resource_type == "virksomhed" and f.http_status == 200:
                prev = latest.get(f.resource_id)
                if prev is None or f.fetched_at > prev.fetched_at:
                    latest[f.resource_id] = f
        rows = [_row(self.store, cvr, f) for cvr, f in latest.items()]
        rows.sort(key=lambda r: (r.name or "￿", r.cvr))
        self._companies = rows
        return rows

    def stats(self) -> dict[str, Any]:
        fetches = self.store.fetches()
        by_source: dict[str, int] = {}
        for f in fetches:
            by_source[f"{f.source}/{f.resource_type}"] = by_source.get(
                f"{f.source}/{f.resource_type}", 0) + 1
        return {
            "fetches": len(fetches),
            "companies": len(self.companies()),
            "bytes": sum(f.byte_len for f in fetches),
            "by_source": sorted(by_source.items(), key=lambda kv: -kv[1]),
            "sanctions_loaded": self.sanctions is not None,
        }

    # ── one company ──────────────────────────────────────────────────────────
    def sheet(self, cvr: str, as_of: date | None) -> Sheet:
        return build_sheet(self.store, cvr, self.sanctions, as_of)

    def screen(self, cvr: str, as_of: date):
        return run_screen(cvr, Sources(store=self.store, cvr_client=None,
                                       sanctions=self.sanctions,
                                       directorships=self.directorships,
                                       budget=self.budget), as_of=as_of)

    def hashes_for(self, cvr: str) -> set[str]:
        """The documents in force about one company — latest per resource.

        This is what an analyst flag pins itself to, and the reason a flag goes
        stale on its own: the set moves when anything about the company is
        refetched and comes back different. Deliberately not every payload ever
        fetched, which in an append-only store is a set that never loses a
        member and therefore never goes stale.
        """
        return analyst_mod.current_hashes(self.store, cvr)

    def flag(self, cvr: str):
        return analyst_mod.active_flag(self.store, cvr, self.hashes_for(cvr))

    def observations(self, cvr: str) -> list[analyst_mod.Observation]:
        return analyst_mod.observations(self.store, cvr)

    def record_observation(self, *, cvr: str, verdict: str, basis: str,
                           who: str, as_of: date, sources: tuple[str, ...],
                           suppresses: tuple[str, ...]) -> str:
        """Append one analyst judgement, pinned to the documents on file now."""
        obs = analyst_mod.Observation(
            subject=cvr, verdict=verdict, basis=basis, analyst=who,
            observed_at=datetime.now(timezone.utc).isoformat(), as_of=str(as_of),
            sources=sources, pinned=tuple(sorted(self.hashes_for(cvr))),
            suppresses=suppresses)
        h = analyst_mod.record(self.store, obs)
        self._stamp = None          # the log moved; drop the caches
        return h

    # ── the review queue ─────────────────────────────────────────────────────
    def review_packets(self) -> list[tuple[str, ReviewPacket]]:
        """Every review packet, newest first, each with its content hash.

        Packets are append-only: a review writes a new record rather than
        editing the pending one, so the queue shows the latest record per
        (contract, subject, quote) and keeps the earlier ones addressable.
        """
        out: list[tuple[str, ReviewPacket]] = []
        for f in self.store.fetches():
            if f.source == "review" and f.resource_type == "review_packet":
                try:
                    out.append((f.content_hash,
                                ReviewPacket(**_coerce_outcome(
                                    self.store.get_json(f.content_hash)))))
                except (TypeError, ValueError, json.JSONDecodeError):
                    continue
        out.sort(key=lambda p: p[1].created_at, reverse=True)
        return out

    def resolve_review(self, content_hash: str, *, outcome: ReviewOutcome,
                       reviewer: str, rationale: str) -> str:
        packet = ReviewPacket(**_coerce_outcome(self.store.get_json(content_hash)))
        reviewed = packet.review(outcome=outcome, reviewer=reviewer, rationale=rationale)
        h = store_review_packet(self.store, reviewed)
        self._stamp = None
        return h


def _coerce_outcome(payload: dict[str, Any]) -> dict[str, Any]:
    payload = dict(payload)
    payload["outcome"] = ReviewOutcome(payload.get("outcome", "pending"))
    return payload


def _row(store: RawStore, cvr: str, fetch) -> CompanyRow:
    """One index row, read through the same parser the cohorts use.

    Deliberately tolerant: a payload this cannot read still appears, under its
    CVR number, with empty columns. A document that fails to parse is a thing to
    go and look at, and dropping it from the list is how it stays unlooked-at.
    """
    blank = CompanyRow(cvr=cvr, name="", status="", legal_form="", founded=None,
                       event=None, event_status=None, fetched_at=fetch.fetched_at,
                       content_hash=fetch.content_hash)
    try:
        hit = unwrap_hit(store.get_json(fetch.content_hash))
    except (OSError, json.JSONDecodeError):
        return blank
    if hit is None:
        return blank
    subject = subject_from_hit(hit, "workbench", fetch.content_hash)
    if subject is None:
        return blank
    current = ""
    today = datetime.now(timezone.utc).date()
    for frm, to, value in subject.status_history:
        if (frm is None or frm <= today) and (to is None or to >= today):
            current = value
    return CompanyRow(
        cvr=subject.cvr, name=subject.name or "", status=current,
        legal_form=subject.legal_form, founded=subject.founded,
        event=subject.event, event_status=subject.event_status,
        fetched_at=fetch.fetched_at, content_hash=fetch.content_hash)
