from __future__ import annotations

import shutil
from pathlib import Path

from math_agent.lean.mathlib_search import MathlibSearch
from math_agent.lean.premise_retriever import PremiseEntry, PremiseRetriever


def test_retriever_caches_and_retrieves():
    entries = [
        PremiseEntry(name="Nat.Prime", module="Mathlib.Data.Nat.Prime", type="Nat -> Prop", docstring=""),
        PremiseEntry(name="Real.sqrt", module="Mathlib.Data.Real.Sqrt", type="Real -> Real", docstring=""),
    ]
    retriever = PremiseRetriever(entries=entries)
    results = retriever.retrieve("prove n is prime", top_k=2)
    assert len(results) == 1
    assert results[0].name == "Nat.Prime"


def test_premise_entry_roundtrip_and_prompt():
    entry = PremiseEntry(
        name="Nat.Prime",
        module="Mathlib.Data.Nat.Prime",
        type="Nat -> Prop",
        docstring="A natural number is prime.",
        file="Mathlib/Data/Nat/Prime.lean",
        line=42,
    )
    data = entry.to_dict()
    assert data == {
        "name": "Nat.Prime",
        "module": "Mathlib.Data.Nat.Prime",
        "type": "Nat -> Prop",
        "docstring": "A natural number is prime.",
        "file": "Mathlib/Data/Nat/Prime.lean",
        "line": 42,
    }
    restored = PremiseEntry.from_dict(data)
    assert restored == entry

    prompt = entry.to_prompt_text()
    assert "Nat.Prime" in prompt
    assert "Mathlib.Data.Nat.Prime" in prompt
    assert "Nat -> Prop" in prompt
    assert "A natural number is prime" in prompt


def test_premise_entry_prompt_newlines_truncated():
    entry = PremiseEntry(
        name="Foo.bar",
        module="Mathlib.Foo.Bar",
        type="Nat\n-> Prop",
        docstring="Line1\nLine2 " + "x" * 200,
    )
    prompt = entry.to_prompt_text()
    # Newlines inside the type/docstring should be flattened to spaces.
    assert "Nat -> Prop" in prompt
    assert "Line1 Line2" in prompt
    # Long docstrings are truncated to 120 characters.
    assert "x" * 121 not in prompt
    assert "x" * 100 in prompt


def test_build_index_and_cache(tmp_path):
    root = tmp_path / "mathlib"
    (root / "Mathlib").mkdir(parents=True)

    (root / "Mathlib" / "Foo.lean").write_text("\n" * 2 + "theorem Foo.thm : Nat -> Prop := by\n", encoding="utf-8")
    (root / "Mathlib" / "Bar.lean").write_text("\n" * 6 + "lemma Bar.lem : Real -> Real := by\n", encoding="utf-8")

    decls = {
        ("Mathlib/Foo.lean", 3): {
            "file": "Mathlib/Foo.lean",
            "line": 3,
            "name": "Foo.thm",
            "module": "Mathlib.Foo",
            "type": "Nat -> Prop",
            "docstring": "",
        },
        ("Mathlib/Bar.lean", 7): {
            "file": "Mathlib/Bar.lean",
            "line": 7,
            "name": "Bar.lem",
            "module": "Mathlib.Bar",
            "type": "Real -> Real",
            "docstring": "",
        },
    }

    class FakeSearch:
        def __init__(self, root):
            self.root = Path(root)

        def all_declaration_files(self):
            return [self.root / "Mathlib/Foo.lean", self.root / "Mathlib/Bar.lean"]

        def _load_declaration(self, file, line_number, lines=None):
            return decls.get((file, line_number))

    search = FakeSearch(root)
    cache_dir = tmp_path / "cache"
    retriever = PremiseRetriever(mathlib_search=search, cache_dir=cache_dir)

    entries = retriever.build_index()
    assert len(entries) == 2
    assert {e.name for e in entries} == {"Foo.thm", "Bar.lem"}

    # A second build should return the same in-memory entries.
    assert retriever.build_index() is entries

    # A fresh retriever with the same cache should load cached entries.
    retriever2 = PremiseRetriever(mathlib_search=search, cache_dir=cache_dir)
    entries2 = retriever2.build_index()
    assert len(entries2) == 2
    assert {e.name for e in entries2} == {"Foo.thm", "Bar.lem"}

    # Forcing a rebuild should return a new list.
    entries3 = retriever2.build_index(force=True)
    assert len(entries3) == 2
    assert entries3 is not entries2


def test_build_index_includes_attributed_and_modified_declarations(tmp_path):
    root = tmp_path / "mathlib"
    (root / "Mathlib").mkdir(parents=True)
    source = root / "Mathlib" / "Modifiers.lean"
    source.write_text(
        "\n".join([
            "@[simp] theorem Foo.simp_thm : Nat := by sorry",
            "private theorem Foo.private_thm : Nat := by sorry",
            "protected def Foo.protected_def : Nat := 42",
            "noncomputable theorem Foo.noncomp_thm : Nat := by sorry",
            "partial def Foo.partial_def : Nat → Nat := fun n => n",
        ])
        + "\n",
        encoding="utf-8",
    )

    decls = {
        ("Mathlib/Modifiers.lean", 1): {
            "file": "Mathlib/Modifiers.lean",
            "line": 1,
            "name": "Foo.simp_thm",
            "module": "Mathlib.Modifiers",
            "type": "Nat",
            "docstring": "",
        },
        ("Mathlib/Modifiers.lean", 2): {
            "file": "Mathlib/Modifiers.lean",
            "line": 2,
            "name": "Foo.private_thm",
            "module": "Mathlib.Modifiers",
            "type": "Nat",
            "docstring": "",
        },
        ("Mathlib/Modifiers.lean", 3): {
            "file": "Mathlib/Modifiers.lean",
            "line": 3,
            "name": "Foo.protected_def",
            "module": "Mathlib.Modifiers",
            "type": "Nat",
            "docstring": "",
        },
        ("Mathlib/Modifiers.lean", 4): {
            "file": "Mathlib/Modifiers.lean",
            "line": 4,
            "name": "Foo.noncomp_thm",
            "module": "Mathlib.Modifiers",
            "type": "Nat",
            "docstring": "",
        },
        ("Mathlib/Modifiers.lean", 5): {
            "file": "Mathlib/Modifiers.lean",
            "line": 5,
            "name": "Foo.partial_def",
            "module": "Mathlib.Modifiers",
            "type": "Nat → Nat",
            "docstring": "",
        },
    }

    class FakeSearch:
        def __init__(self, root):
            self.root = Path(root)

        def all_declaration_files(self):
            return [self.root / "Mathlib/Modifiers.lean"]

        def _load_declaration(self, file, line_number, lines=None):
            return decls.get((file, line_number))

    search = FakeSearch(root)
    retriever = PremiseRetriever(mathlib_search=search, cache_dir=tmp_path / "cache")
    entries = retriever.build_index()
    names = {e.name for e in entries}
    assert names == {
        "Foo.simp_thm",
        "Foo.private_thm",
        "Foo.protected_def",
        "Foo.noncomp_thm",
        "Foo.partial_def",
    }


def test_retrieve_no_matches_and_short_tokens():
    entries = [
        PremiseEntry(name="Real.sqrt", module="Mathlib.Data.Real.Sqrt", type="Real -> Real", docstring=""),
    ]
    retriever = PremiseRetriever(entries=entries)
    assert retriever.retrieve("prime", top_k=5) == []
    assert retriever.retrieve("x y", top_k=5) == []


def test_build_index_with_entries_and_no_search_does_not_call_default_search(monkeypatch):
    """Passing entries and mathlib_search=None must not require a mathlib checkout."""

    def _fail_default_search() -> None:
        raise RuntimeError("default_search should not be called when entries are supplied")

    monkeypatch.setattr(
        "math_agent.lean.premise_retriever.default_search", _fail_default_search
    )

    entries = [
        PremiseEntry(name="Nat.Prime", module="Mathlib.Data.Nat.Prime", type="Nat -> Prop", docstring=""),
    ]
    retriever = PremiseRetriever(mathlib_search=None, entries=entries)
    assert retriever.build_index() is entries


def test_load_declaration_uses_provided_lines(tmp_path, monkeypatch):
    """_load_declaration must produce identical results when lines are passed in."""
    monkeypatch.setattr(shutil, "which", lambda _cmd: "/bin/rg")

    root = tmp_path / "mathlib"
    (root / "Mathlib").mkdir(parents=True)
    source = root / "Mathlib" / "Decl.lean"
    source.write_text(
        "/-- A docstring -/\n"
        "theorem Foo.bar (n : ℕ) : n = n := by\n"
        "  rfl\n",
        encoding="utf-8",
    )

    search = MathlibSearch(mathlib_root=root)
    lines = source.read_text(encoding="utf-8").splitlines()

    entry_with_lines = search._load_declaration("Mathlib/Decl.lean", 2, lines)
    entry_without_lines = search._load_declaration("Mathlib/Decl.lean", 2)

    assert entry_with_lines is not None
    assert entry_without_lines is not None
    assert entry_with_lines == entry_without_lines
    assert entry_with_lines["name"] == "Foo.bar"
    assert entry_with_lines["module"] == "Mathlib.Decl"
    assert entry_with_lines["type"] == "(n : ℕ) : n = n"
    assert "rfl" not in entry_with_lines["declaration"]
    assert "A docstring" in entry_with_lines["docstring"]


def test_load_declaration_stops_before_proof_and_next_declaration(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(shutil, "which", lambda _cmd: "/bin/rg")
    root = tmp_path / "mathlib"
    (root / "Mathlib").mkdir(parents=True)
    source = root / "Mathlib" / "Decl.lean"
    source.write_text(
        "theorem Foo.bar\n"
        "    (n : ℕ) :\n"
        "    n = n := by\n"
        "  rfl\n"
        "\n"
        "theorem Foo.baz : True := by\n"
        "  trivial\n",
        encoding="utf-8",
    )

    entry = MathlibSearch(mathlib_root=root)._load_declaration(
        "Mathlib/Decl.lean", 1
    )

    assert entry is not None
    assert entry["type"] == "(n : ℕ) :\nn = n"
    assert "rfl" not in entry["declaration"]
    assert "Foo.baz" not in entry["declaration"]


def test_corrupt_cache_is_rebuilt_atomically(tmp_path):
    root = tmp_path / "mathlib"
    (root / "Mathlib").mkdir(parents=True)
    source = root / "Mathlib" / "Foo.lean"
    source.write_text("theorem Foo.bar : True := by trivial\n", encoding="utf-8")

    class FakeSearch:
        def __init__(self, root):
            self.root = Path(root)

        def all_declaration_files(self):
            return [source]

        def _load_declaration(self, file, line_number, lines=None):
            return {
                "file": file,
                "line": line_number,
                "name": "Foo.bar",
                "module": "Mathlib.Foo",
                "type": ": True",
                "docstring": "",
            }

    cache_dir = tmp_path / "cache"
    first = PremiseRetriever(mathlib_search=FakeSearch(root), cache_dir=cache_dir)
    first.build_index()
    first.index_path.write_text("not-json\n", encoding="utf-8")

    second = PremiseRetriever(mathlib_search=FakeSearch(root), cache_dir=cache_dir)
    entries = second.build_index()

    assert [entry.name for entry in entries] == ["Foo.bar"]
    assert second.fingerprint_path.read_text(encoding="utf-8").startswith("v2:")
    assert not list(cache_dir.glob(".premises.jsonl.*"))


def test_tokenize_splits_underscores():
    from math_agent.lean.mathlib_search import _tokenize

    tokens = _tokenize("irrational_sqrt_two")
    assert "irrational_sqrt_two" in tokens
    assert "irrational" in tokens
    assert "sqrt" in tokens
    assert "two" in tokens


def test_retriever_recalls_by_underscore_subword():
    entries = [
        PremiseEntry(
            name="irrational_sqrt_two",
            module="Mathlib.Data.Real.Irrational",
            type="Irrational (Real.sqrt 2)",
            docstring="",
        ),
    ]
    retriever = PremiseRetriever(entries=entries)
    results = retriever.retrieve("sqrt", top_k=5)
    assert any(e.name == "irrational_sqrt_two" for e in results)
