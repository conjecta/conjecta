#!/usr/bin/env python3
"""Extract theorem statements from the arXiv undergraduate math PDF package.

Usage:
    python scripts/extract_arxiv_theorems.py \
        --package /root/arxiv_undergrad_math_package \
        --output data/arxiv_undergrad_theorems.jsonl

Each output row is a candidate proof target with the theorem statement as the
expected result.  The extraction is heuristic (regex over PDF text), so the
output should be reviewed before use.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

try:
    from pypdf import PdfReader
except ImportError as exc:  # pragma: no cover
    raise RuntimeError("pypdf is required; run `pip install -e .`") from exc


# Pattern for theorem declarations like "Theorem 1.2." or "Theorem 2.1"
# followed by the statement text until a known boundary.
THEOREM_RE = re.compile(
    r"Theorem\s+(?P<num>\d+(?:\.\d+)?)\s*[.:\s]\s*"
    r"(?P<statement>.*?)"
    r"(?=(?:\n\s*Proof|\n\s*Remark|\n\s*Lemma|\n\s*Corollary|\n\s*Definition|"
    r"\n\s*Theorem|\n\s*\d+\s+Introduction|\Z))",
    re.DOTALL | re.IGNORECASE,
)

# Boundaries that usually mean the statement has run too far.
_CUT_MARKERS = (
    "Acknowledgements",
    "References",
    "Figure ",
    "Table ",
    "\n\n",
)


def _extract_pdf_text(pdf_path: Path, max_pages: int | None = None) -> str:
    reader = PdfReader(str(pdf_path))
    pages = reader.pages
    if max_pages is not None:
        pages = pages[:max_pages]
    parts: list[str] = []
    for page in pages:
        text = page.extract_text() or ""
        text = _normalize_pdf_text(text)
        if text:
            parts.append(text)
    return "\n\n".join(parts)


def _normalize_pdf_text(text: str) -> str:
    text = text.replace("\x00", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _clean_statement(text: str) -> str:
    # Cut at common section markers.
    for marker in _CUT_MARKERS:
        if marker in text:
            text = text.split(marker)[0]
    # Collapse whitespace.
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _theorem_records_for_paper(
    paper: dict[str, Any],
    package_root: Path,
    max_pages: int | None,
) -> list[dict[str, Any]]:
    pdf_path = package_root / paper["filename"]
    if not pdf_path.exists():
        return []

    text = _extract_pdf_text(pdf_path, max_pages=max_pages)
    records: list[dict[str, Any]] = []
    for match in THEOREM_RE.finditer(text):
        number = match.group("num")
        statement = _clean_statement(match.group("statement"))
        # Skip very short or very noisy matches.
        if len(statement) < 30 or len(statement) > 3000:
            continue
        record = {
            "id": f"{paper['arxiv_id']}_thm{number}",
            "arxiv_id": paper["arxiv_id"],
            "title": paper["title"],
            "topic_group": paper.get("topic_group", ""),
            "topic_note": paper.get("topic_note", ""),
            "pages": paper.get("pages"),
            "theorem_number": number,
            "name": f"{paper['arxiv_id']} Theorem {number}",
            "statement_text": statement,
            "source_pdf": str(pdf_path),
            "difficulty": "hard",
            "target_system": "lean4",
        }
        records.append(record)
    return records


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Extract theorem statements from arXiv PDF package."
    )
    parser.add_argument(
        "--package",
        default="/root/arxiv_undergrad_math_package",
        help="Root directory of the arXiv package.",
    )
    parser.add_argument(
        "--output",
        default="data/arxiv_undergrad_theorems.jsonl",
        help="Output JSONL path.",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=None,
        help="Only read the first N pages of each PDF (useful for quick tests).",
    )
    parser.add_argument(
        "--max-theorems-per-paper",
        type=int,
        default=5,
        help="Keep at most N theorems per paper (prioritizes main results).",
    )
    args = parser.parse_args()

    package_root = Path(args.package)
    manifest_path = package_root / "manifest.json"
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    papers = json.loads(manifest_path.read_text(encoding="utf-8"))
    all_records: list[dict[str, Any]] = []
    for paper in papers:
        records = _theorem_records_for_paper(
            paper,
            package_root=package_root,
            max_pages=args.max_pages,
        )
        # Keep the first N theorems; usually main results appear early.
        if args.max_theorems_per_paper is not None:
            records = records[: args.max_theorems_per_paper]
        all_records.extend(records)

    with output_path.open("w", encoding="utf-8") as handle:
        for record in all_records:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")

    print(
        json.dumps(
            {
                "papers": len(papers),
                "theorems_extracted": len(all_records),
                "output": str(output_path),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
