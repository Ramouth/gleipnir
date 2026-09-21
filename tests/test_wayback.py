"""The archive timeline: what the company's own account said, and when.

The properties pinned here are the ones a retrospective backtest depends on —
answer with the version actually in force, never a later one, and never let a
redirect masquerade as an edit.
"""
import json
from datetime import date

import pytest

from gleipnir.adapters.wayback import Capture, History, WaybackClient

ROWS = [
    ["timestamp", "original", "statuscode", "digest", "length"],
    ["20220103221455", "http://x.dk/", "301", "REDIRECTAAAAAAAAAAAAAAAAAAAAAAAA", "595"],
    ["20240517181749", "http://x.dk/", "301", "REDIRECTAAAAAAAAAAAAAAAAAAAAAAAA", "733"],
    ["20241209033936", "https://www.x.dk/", "200", "AAAA1111", "11017"],
    ["20250321201052", "https://www.x.dk/", "200", "BBBB2222", "11252"],
    ["20260725221843", "https://www.x.dk/", "200", "CCCC3333", "11365"],
]


class Http:
    def __init__(self, rows=ROWS, body=b"<html>archived</html>"):
        self.rows, self.body, self.calls = rows, body, []

    def get(self, url, **kw):
        self.calls.append((url, kw))
        payload, body = self.rows, self.body
        return type("R", (), {
            "status_code": 200, "content": body,
            "json": lambda self=None: payload,
            "raise_for_status": lambda self=None: None})()


def hist():
    return WaybackClient(Http()).history("x.dk")


# ── versions ────────────────────────────────────────────────────────────────

def test_redirects_are_not_counted_as_content_versions():
    """A 301 whose byte length changes is not the page changing, and counting
    it would put a false edit date on the timeline."""
    h = hist()
    assert len(h.captures) == 5
    assert [c.digest for c in h.versions] == ["AAAA1111", "BBBB2222", "CCCC3333"]


def test_identical_content_is_one_version():
    rows = ROWS + [["20260801000000", "https://www.x.dk/", "200", "CCCC3333", "9"]]
    h = WaybackClient(Http(rows)).history("x.dk")
    assert len(h.versions) == 3


def test_first_and_last_change_dates():
    h = hist()
    assert h.first_seen == date(2024, 12, 9)
    assert h.last_changed == date(2026, 7, 25)


def test_an_empty_archive_has_no_dates_rather_than_a_default():
    h = WaybackClient(Http([["timestamp", "original", "statuscode", "digest"]])).history("x.dk")
    assert h.versions == () and h.last_changed is None and h.first_seen is None


# ── as-of: the backtest primitive ───────────────────────────────────────────

def test_as_of_returns_the_version_in_force_not_the_nearest():
    """The archive may hold nothing for months around a date. Answering with a
    LATER snapshot would report content that did not exist yet — the one thing
    a backtest must never do."""
    h = hist()
    assert h.as_of(date(2025, 1, 1)).digest == "AAAA1111"
    assert h.as_of(date(2026, 1, 1)).digest == "BBBB2222"
    assert h.as_of(date(2026, 12, 31)).digest == "CCCC3333"


def test_as_of_before_the_first_capture_is_none():
    assert hist().as_of(date(2019, 1, 1)) is None


def test_as_of_on_the_exact_capture_day_includes_it():
    assert hist().as_of(date(2024, 12, 9)).digest == "AAAA1111"


# ── staleness ───────────────────────────────────────────────────────────────

def test_a_page_that_changed_after_the_registry_is_not_stale():
    """The ordinary case, and not a finding."""
    assert hist().stale_days(date(2025, 1, 1), date(2026, 8, 27)) < 0


def test_a_page_unchanged_since_before_a_registry_move_reports_its_age():
    rows = ROWS[:4]                       # last version 2024-12-09
    h = WaybackClient(Http(rows)).history("x.dk")
    assert h.stale_days(date(2025, 4, 1), date(2026, 8, 27)) == 513


def test_staleness_is_none_when_the_archive_holds_nothing():
    h = WaybackClient(Http([["timestamp"]])).history("x.dk")
    assert h.stale_days(date(2025, 1, 1), date(2026, 8, 27)) is None


# ── request shape ───────────────────────────────────────────────────────────

def test_the_timeline_costs_one_request_and_collapses_server_side():
    """A page captured daily for a decade must return its handful of real edits,
    not 3,000 rows."""
    http = Http()
    WaybackClient(http).history("x.dk")
    assert len(http.calls) == 1
    assert http.calls[0][1]["params"]["collapse"] == "digest"


def test_snapshots_are_requested_without_the_archive_banner():
    """`id_` asks for the original response. Without it every extraction would
    find Internet Archive markup in the page it is parsing."""
    http = Http()
    c = WaybackClient(http)
    c.snapshot(Capture("20241209033936", "https://www.x.dk/", "200", "AAAA1111"))
    assert "id_/" in http.calls[-1][0]


def test_a_malformed_cdx_payload_yields_an_empty_history():
    class Bad(Http):
        def get(self, url, **kw):
            def boom(self=None):
                raise json.JSONDecodeError("no", "", 0)
            return type("R", (), {"status_code": 200, "json": boom,
                                  "raise_for_status": lambda self=None: None})()
    assert WaybackClient(Bad()).history("x.dk").captures == ()


def test_short_cdx_rows_are_skipped_rather_than_raising():
    rows = [["timestamp", "original", "statuscode", "digest"], ["2024", "u"]]
    assert WaybackClient(Http(rows)).history("x.dk").captures == ()
