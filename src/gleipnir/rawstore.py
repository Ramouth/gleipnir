"""Layer 2 — the immutable raw store (architecture.md §4).

Every payload we fetch is written once, under the SHA-256 of its bytes, next to
an append-only log of *how it was fetched*. Nothing downstream is authoritative:
claims, the graph, verdicts and reports are all projections that can be dropped
and rebuilt from what is here.

Two properties this buys, both load-bearing:

**A parser bug becomes a reparse, not a refetch.** This matters because refetching
costs quota (the CVR system-til-system agreement is rate-limited) and because
history is not always re-obtainable — an entity that changes between two fetches
cannot be un-changed.

**A report is reproducible.** `docs/architecture.md` §9 requires that, given a
report ID, we can reconstruct the exact data state that produced it. That is only
true if the inputs are addressed by content rather than overwritten in place.

Storage is a directory of blobs plus a JSONL log rather than a database because
this layer must survive every later storage decision (§8 leaves Postgres vs
Postgres+Neo4j deliberately open). A directory outlives both.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class FetchRecord:
    """What we asked for, when, and what came back.

    `request_params` is stored so a fetch can be replayed exactly. `http_status`
    is stored for non-200s too: a 404 is evidence — it is the difference between
    "this entity has no owners" and "we never successfully asked", which
    `docs/predicates.md` §5 requires the model to distinguish.
    """

    content_hash: str
    source: str            # 'cvr' | 'opensanctions' | 'epo' | ...
    resource_type: str     # 'virksomhed' | 'deltager' | ...
    resource_id: str       # CVR number, enhedsnummer, publication number
    fetched_at: str        # ISO 8601, UTC
    http_status: int
    request_params: dict[str, Any]
    byte_len: int


class RawStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.blobs = self.root / "blobs"
        self.log_path = self.root / "fetches.jsonl"
        self.blobs.mkdir(parents=True, exist_ok=True)

    def put(
        self,
        *,
        payload: bytes,
        source: str,
        resource_type: str,
        resource_id: str,
        http_status: int,
        request_params: dict[str, Any],
    ) -> FetchRecord:
        """Write a payload and log the fetch. Returns the record.

        The blob is written only if absent — identical bytes fetched twice
        occupy one file. The *log* line is always appended, because "we asked
        again on this date and nothing had changed" is itself a fact the
        bitemporal layer needs: it bounds when a value was still true.
        """
        content_hash = hashlib.sha256(payload).hexdigest()
        blob_path = self._blob_path(content_hash)
        if not blob_path.exists():
            blob_path.parent.mkdir(parents=True, exist_ok=True)
            _write_atomic(blob_path, payload)

        record = FetchRecord(
            content_hash=content_hash,
            source=source,
            resource_type=resource_type,
            resource_id=resource_id,
            fetched_at=datetime.now(timezone.utc).isoformat(),
            http_status=http_status,
            request_params=request_params,
            byte_len=len(payload),
        )
        with self.log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(asdict(record), ensure_ascii=False) + "\n")
        return record

    def put_stream(
        self,
        chunks,
        *,
        source: str,
        resource_type: str,
        resource_id: str,
        http_status: int,
        request_params: dict[str, Any],
    ) -> FetchRecord:
        """Same contract as `put`, for payloads too large to hold in memory.

        Bulk sanctions and patent datasets run to hundreds of megabytes. The
        hash is computed while streaming to a temp file, which is then renamed
        into place under its own hash — so an interrupted download leaves a
        `.tmp` file rather than a blob whose name lies about its contents.
        """
        digest = hashlib.sha256()
        total = 0
        fd, tmp = tempfile.mkstemp(dir=str(self.blobs), suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as fh:
                for chunk in chunks:
                    digest.update(chunk)
                    fh.write(chunk)
                    total += len(chunk)
            content_hash = digest.hexdigest()
            blob_path = self._blob_path(content_hash)
            blob_path.parent.mkdir(parents=True, exist_ok=True)
            if blob_path.exists():
                Path(tmp).unlink(missing_ok=True)
            else:
                os.replace(tmp, blob_path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

        record = FetchRecord(
            content_hash=content_hash, source=source, resource_type=resource_type,
            resource_id=resource_id,
            fetched_at=datetime.now(timezone.utc).isoformat(),
            http_status=http_status, request_params=request_params, byte_len=total,
        )
        with self.log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(asdict(record), ensure_ascii=False) + "\n")
        return record

    def path_of(self, content_hash: str) -> Path:
        """Filesystem path of a blob, for readers that want to stream it."""
        return self._blob_path(content_hash)

    def get(self, content_hash: str) -> bytes:
        return self._blob_path(content_hash).read_bytes()

    def get_json(self, content_hash: str) -> Any:
        return json.loads(self.get(content_hash))

    def fetches(self) -> list[FetchRecord]:
        """Replay the log. Small enough to hold in memory at PoC scale."""
        if not self.log_path.exists():
            return []
        out: list[FetchRecord] = []
        with self.log_path.open(encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    out.append(FetchRecord(**json.loads(line)))
        return out

    def latest(self, source: str, resource_type: str, resource_id: str) -> FetchRecord | None:
        """Most recent successful fetch of one resource, or None.

        Used as a read-through cache check before spending quota.
        """
        best: FetchRecord | None = None
        for rec in self.fetches():
            if (
                rec.source == source
                and rec.resource_type == resource_type
                and rec.resource_id == resource_id
                and rec.http_status == 200
            ):
                if best is None or rec.fetched_at > best.fetched_at:
                    best = rec
        return best

    def _blob_path(self, content_hash: str) -> Path:
        # Two-level fan-out: a flat directory of a million blobs is painful to
        # list, back up, or rsync.
        return self.blobs / content_hash[:2] / content_hash[2:4] / f"{content_hash}.json"


def _write_atomic(path: Path, data: bytes) -> None:
    """Write via a temp file in the same directory, then rename.

    An interrupted write must not leave a truncated blob under a hash that
    claims to describe its full contents — every later reparse would trust it.
    """
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
