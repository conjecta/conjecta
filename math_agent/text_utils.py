"""Shared text/markdown extraction helpers."""
from __future__ import annotations

import json
import re
from typing import Any


def extract_fenced_code(text: str, language: str | None = None) -> str | None:
    """Extract the first fenced code block, optionally filtering by language."""
    if language:
        pattern = rf"```{re.escape(language)}\b\s*\n(.*?)```"
        match = re.search(pattern, text, re.DOTALL)
        if match:
            return match.group(1).strip()
    match = re.search(r"```\s*\n(.*?)```", text, re.DOTALL)
    if match:
        return match.group(1).strip()
    return None


def parse_json_blob(text: str) -> dict[str, Any] | None:
    """Parse JSON from raw text, tolerating markdown fences and trailing junk."""
    raw = text.strip()
    fenced = extract_fenced_code(raw, language="json")
    if fenced:
        raw = fenced
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    start = raw.find("{")
    end = raw.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(raw[start : end + 1])
        except json.JSONDecodeError:
            pass
    return None


def extract_lean_code(response: str) -> str:
    """Extract Lean 4 code from markdown fences or raw response."""
    for lang in (r"lean4?", r"lean"):
        match = re.search(rf"```{lang}\b\s*\n(.*?)```", response, re.DOTALL)
        if match:
            return match.group(1).strip()
    match = re.search(r"```\s*\n(.*?)```", response, re.DOTALL)
    if match:
        return match.group(1).strip()
    return response.strip()
