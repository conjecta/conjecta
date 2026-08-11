from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable
from typing import Any


_ASCII_TOKEN_RE = re.compile(r"[a-zA-Z0-9_']+")
_CJK_RUN_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]+")
_GENERIC_TOKENS = frozenset(
    {
        "a", "an", "the", "and", "or", "for", "to", "of", "in", "on",
        "prove", "show", "theorem", "lemma", "problem", "question",
        "证明", "求证", "请", "问题", "定理", "命题", "一个", "所有", "使得",
    }
)


def multilingual_tokens(text: str) -> list[str]:
    """Tokenize English, Lean identifiers, and CJK text without a model."""
    value = (text or "").casefold()
    tokens = [
        token
        for token in _ASCII_TOKEN_RE.findall(value)
        if len(token) > 1 and token not in _GENERIC_TOKENS
    ]
    for run in _CJK_RUN_RE.findall(value):
        chars = list(run)
        tokens.extend(chars)
        tokens.extend(
            "".join(chars[index : index + 2])
            for index in range(max(0, len(chars) - 1))
        )
        tokens.extend(
            "".join(chars[index : index + 3])
            for index in range(max(0, len(chars) - 2))
        )
    return [token for token in tokens if token and token not in _GENERIC_TOKENS]


def query_terms(text: str, *, limit: int = 8) -> list[str]:
    counts = Counter(multilingual_tokens(text))
    ranked = sorted(
        counts,
        key=lambda token: (-len(token), -counts[token], token),
    )
    return ranked[: max(0, limit)]


def lexical_score(query: str, document: str) -> float:
    query_counts = Counter(multilingual_tokens(query))
    document_counts = Counter(multilingual_tokens(document))
    if not query_counts or not document_counts:
        return 0.0
    overlap = sum(
        min(count, document_counts.get(token, 0))
        for token, count in query_counts.items()
    )
    if overlap <= 0:
        return 0.0
    coverage = overlap / sum(query_counts.values())
    precision = overlap / sum(document_counts.values())
    score = 2 * coverage * precision / max(coverage + precision, 1e-12)
    normalized_query = " ".join((query or "").casefold().split())
    normalized_document = " ".join((document or "").casefold().split())
    if normalized_query and normalized_query in normalized_document:
        score += 1.0
    return score


def rank_rows(
    rows: Iterable[dict[str, Any]],
    query: str,
    columns: list[str],
    *,
    limit: int,
) -> list[dict[str, Any]]:
    scored: list[tuple[float, int, dict[str, Any]]] = []
    for index, row in enumerate(rows):
        document = "\n".join(str(row.get(column) or "") for column in columns)
        score = lexical_score(query, document)
        if score > 0:
            scored.append((score, index, dict(row)))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [row for _, _, row in scored[: max(0, limit)]]
