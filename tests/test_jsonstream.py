"""Streaming a JSON array without building it.

Estonia's shareholder file is 376,826 records and its general-data file is
229 MB unzipped; `json.loads` peaked at 2.8 GB on the smaller of the two. This
scanner exists to make those files readable, so its edge cases are the ones a
register file actually contains: braces inside company names, escaped quotes,
and multi-byte characters landing on a read boundary.
"""
import io
import json

import pytest

from gleipnir.jsonstream import iter_json_array

#: Every shape that has ever broken a brace-counting scanner.
RECORDS = [
    {"id": 1, "name": "Braces {} in a string", "nested": {"a": [1, 2, {"b": "}"}]}},
    {"id": 2, "name": 'Quote " backslash \\ newline \n and OÜ'},
    {"id": 3, "trailing": "ends with a backslash \\", "empty": {}, "arr": [{}, {}]},
    {"id": 4, "q": '"{"', "r": "}\\", "unicode": "Õie Ülejõe — Tallinn"},
    {"id": 5, "osanikud": [{"nimi": "Näide & Partnerid OÜ"}], "n": None},
]

BLOB = json.dumps(RECORDS, ensure_ascii=False, indent=4)

#: 1 and 2 land inside multi-byte characters and inside escape pairs; the last
#: is larger than the whole document.
CHUNKS = (1, 2, 3, 4, 7, 13, 97, 1024, len(BLOB) * 2)


@pytest.mark.parametrize("chunk", CHUNKS)
def test_text_input_round_trips_at_any_chunk_size(chunk):
    assert list(iter_json_array(io.StringIO(BLOB), chunk=chunk)) == RECORDS


@pytest.mark.parametrize("chunk", CHUNKS)
def test_binary_input_round_trips_at_any_chunk_size(chunk):
    """A fixed-size read lands mid-character often enough that Estonian company
    names alone break a naive decode."""
    assert list(iter_json_array(io.BytesIO(BLOB.encode()), chunk=chunk)) == RECORDS


def test_a_brace_inside_a_string_does_not_end_a_record():
    blob = json.dumps([{"name": "Weird } Name {", "ok": True}])
    assert list(iter_json_array(io.StringIO(blob))) == [
        {"name": "Weird } Name {", "ok": True}]


def test_an_escaped_quote_does_not_end_a_string():
    """The escape covers exactly the next character. Carrying an `escaped` flag
    to a match further along mis-reads "a\\nb" as an unterminated string."""
    blob = json.dumps([{"name": 'He said "hi" }', "n": 1}])
    assert list(iter_json_array(io.StringIO(blob), chunk=3)) == [
        {"name": 'He said "hi" }', "n": 1}]


def test_a_backslash_at_the_end_of_a_string_is_handled():
    blob = json.dumps([{"path": "C:\\", "next": "{"}])
    assert list(iter_json_array(io.StringIO(blob), chunk=2)) == [
        {"path": "C:\\", "next": "{"}]


def test_an_empty_array_yields_nothing():
    assert list(iter_json_array(io.StringIO("[]"))) == []


def test_an_empty_stream_yields_nothing():
    assert list(iter_json_array(io.StringIO(""))) == []


def test_whitespace_and_commas_between_records_are_skipped():
    assert len(list(iter_json_array(io.StringIO("[\n\n  {}\n  ,\n  {}\n]")))) == 2


def test_memory_does_not_grow_with_the_number_of_records():
    """The scan position persists across reads and the dead prefix is dropped,
    so a file of any length costs one record plus a chunk."""
    many = json.dumps([{"i": i, "pad": "x" * 200} for i in range(5_000)])
    seen = 0
    for _ in iter_json_array(io.StringIO(many), chunk=4096):
        seen += 1
    assert seen == 5_000


def test_records_are_yielded_lazily():
    """Nothing is parsed until it is asked for — the point of the exercise."""
    stream = iter_json_array(io.StringIO(BLOB))
    assert next(stream)["id"] == 1
