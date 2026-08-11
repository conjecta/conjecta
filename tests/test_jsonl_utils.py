from pathlib import Path

from math_agent.jsonl_utils import read_jsonl, write_jsonl


def test_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "test.jsonl"
    rows = [{"a": 1}, {"b": "two"}]
    write_jsonl(path, rows)
    assert read_jsonl(path) == rows


def test_read_missing(tmp_path: Path) -> None:
    assert read_jsonl(tmp_path / "missing.jsonl") == []


def test_write_creates_parent(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "dir" / "test.jsonl"
    write_jsonl(path, [{"x": 1}])
    assert read_jsonl(path) == [{"x": 1}]
