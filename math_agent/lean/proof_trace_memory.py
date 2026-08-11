"""Verified-proof trace memory: the inference-time data flywheel.

Every tactic search that closes a goal records the verified proof here.
Later searches retrieve similar solved statements as few-shot exemplars for
the tactic generator — the retrieval-side half of expert iteration, with no
training loop required.

Storage is an append-only JSONL file, atomic-appended and bounded by a simple
rotation cap so the file stays small enough to scan synchronously.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

log = logging.getLogger("math_agent.lean.proof_trace_memory")

_DEFAULT_PATH = Path(".lean_workspace") / ".conjecta_cache" / "proof_traces.jsonl"
_MAX_RECORDS = 4096


@dataclass(frozen=True)
class ProofTrace:
    statement: str
    proof: str
    attempts: int
    source: str = "tactic_search"  # "tactic_search" | "lemma_hook" | ...
    ts: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ProofTraceMemory:
    """Append-only store of verified proofs with similarity lookup."""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path is not None else _DEFAULT_PATH
        self._records: list[ProofTrace] | None = None

    def _load(self) -> list[ProofTrace]:
        if self._records is not None:
            return self._records
        records: list[ProofTrace] = []
        if self.path.exists():
            try:
                for line in self.path.read_text(encoding="utf-8").splitlines():
                    if not line.strip():
                        continue
                    data = json.loads(line)
                    records.append(
                        ProofTrace(
                            statement=str(data.get("statement", "")),
                            proof=str(data.get("proof", "")),
                            attempts=int(data.get("attempts", 0)),
                            source=str(data.get("source", "tactic_search")),
                            ts=float(data.get("ts", 0.0)),
                        )
                    )
            except (OSError, json.JSONDecodeError, TypeError, ValueError):
                log.warning("Ignoring unreadable proof trace store %s", self.path)
                records = []
        self._records = records
        return records

    def record(
        self, statement: str, proof: str, *, attempts: int = 0, source: str = "tactic_search"
    ) -> None:
        statement = statement.strip()
        proof = proof.strip()
        if not statement or not proof:
            return
        records = self._load()
        if any(r.statement == statement and r.proof == proof for r in records):
            return
        trace = ProofTrace(
            statement=statement, proof=proof, attempts=attempts, source=source, ts=time.time()
        )
        records.append(trace)
        if len(records) > _MAX_RECORDS:
            # Over the cap: compact by rewriting only the most recent records.
            self._records = records[-_MAX_RECORDS:]
            self._flush()
        else:
            self._records = records
            self._append(trace)

    def _append(self, record: ProofTrace) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record.to_dict()) + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    def _flush(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(
            prefix=f".{self.path.name}.", dir=self.path.parent, text=True
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                for record in self._records or []:
                    handle.write(json.dumps(record.to_dict()) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, self.path)
        finally:
            Path(tmp).unlink(missing_ok=True)

    def similar(self, statement: str, *, top_k: int = 2) -> list[ProofTrace]:
        """Token-overlap similarity over recorded statements (BM25-light)."""
        records = self._load()
        if not records:
            return []
        query_tokens = _significant_tokens(statement)
        if not query_tokens:
            return []
        scored = []
        for record in records:
            overlap = len(query_tokens & _significant_tokens(record.statement))
            if overlap:
                scored.append((overlap, record))
        scored.sort(key=lambda item: (-item[0], -item[1].ts))
        return [record for _, record in scored[:top_k]]


def _significant_tokens(text: str) -> frozenset[str]:
    from math_agent.lean.mathlib_search import _tokenize

    return frozenset(
        token.lower() for token in _tokenize(text) if len(token) >= 3
    )
