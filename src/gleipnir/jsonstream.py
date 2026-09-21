"""Read a huge JSON array one element at a time.

Estonia publishes its register as single JSON arrays: the shareholder file is
376,826 records and the general-data file is 229 MB unzipped. `json.loads` on
either builds the whole object graph first — measured at **2.8 GB peak** for the
shareholder file alone, which is not a thing a workbench process may do to a
laptop.

This yields each top-level object as it is read, so memory stays proportional to
one record plus whatever the caller keeps.

**It is a scanner, not a parser.** It finds object boundaries by counting braces
while respecting strings and escapes, then hands each slice to `json.loads`,
which does the actual parsing. That keeps the delicate part — decoding — in the
standard library, and leaves this file responsible only for knowing where a
record ends.

The bug worth remembering: an earlier version restarted its scan at the
beginning of the retained buffer on every chunk, re-counting the braces it had
already seen, so depth drifted and records were sliced in half. The scan
position has to persist across reads, and it is why `pos` exists.
"""
from __future__ import annotations

import codecs
import json
import re
from typing import Any, Iterator

CHUNK = 1 << 20

#: The only characters that can change the scanner's state. Everything else is
#: payload and is skipped in one jump.
_STRUCTURAL = re.compile(r'["{}\\]')


def iter_json_array(fp: Any, *, chunk: int = CHUNK) -> Iterator[Any]:
    """Yield each top-level object of a JSON array read from `fp`.

    `fp` may be binary or text. Anything outside a top-level object — the
    enclosing brackets, the commas, whitespace — is skipped.

    Binary input is decoded incrementally: a fixed-size read lands mid-character
    often enough that Estonian company names alone will break a naive decode.
    """
    decoder = codecs.getincrementaldecoder("utf-8")()
    buf = ""
    pos = 0            # how far into `buf` the scanner has already looked
    depth = 0
    start: int | None = None
    in_string = False

    while True:
        data = fp.read(chunk)
        if not data:
            break
        buf += decoder.decode(data) if isinstance(data, bytes) else data

        while pos < len(buf):
            # Jump straight to the next character that can change the state.
            # Almost everything in a register file is ordinary text, and
            # stepping over it one character at a time in Python is the whole
            # cost of this loop.
            match = _STRUCTURAL.search(buf, pos)
            if match is None:
                pos = len(buf)
                break
            pos = match.start()
            ch = match.group()

            if in_string:
                if ch == "\\":
                    # The escape covers exactly the next character, whatever it
                    # is. Stepping over both here is what makes it safe to jump
                    # between structural characters: carrying an "escaped" flag
                    # to a match further along would mis-read `"a\\nb"` as an
                    # unterminated string.
                    pos += 2
                    continue
                if ch == '"':
                    in_string = False
            elif ch == '"':
                in_string = True
            elif ch == "{":
                if depth == 0:
                    start = pos
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0 and start is not None:
                    yield json.loads(buf[start:pos + 1])
                    start = None
                    pos += 1
                    # Compacting after every record makes this quadratic in the
                    # file: 376,826 slices of a live buffer took 94 seconds
                    # against 8 for the whole-file parse. Dropping the dead
                    # prefix only once it is worth dropping costs one extra
                    # chunk of memory and gives the time back.
                    if pos >= chunk:
                        buf = buf[pos:]
                        pos = 0
                    continue
            pos += 1

        # Nothing is retained once every complete record has been handed over,
        # so a file of any size costs one record's worth of memory.
        if depth == 0 and start is None:
            buf = ""
            pos = 0
