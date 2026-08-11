from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from math_agent.agent.react_state import Action, ToolObservation


# Canonical set of tools whose observations carry formal proof evidence.
# conclude_gate, react_agent (knowledge promotion + solution.lean_proofs),
# and the proof-graph bookkeeping all derive from this single definition.
FORMAL_ACTIONS = frozenset(
    {"formalize", "lean_check", "tactic_search", "prove_by_lemmas"}
)
_NAMED_DECLARATION_RE = re.compile(
    r"(?ms)^\s*(theorem|lemma)\s+([A-Za-z_][A-Za-z0-9_'.]*)\s*(.+?)\s*:=\s*(?:by\b)?"
)
_EXAMPLE_DECLARATION_RE = re.compile(
    r"(?ms)^\s*(example)\s*(.+?)\s*:=\s*(?:by\b)?"
)


def formal_evidence_id(
    *,
    action_name: str,
    target_claim: str,
    artifact: str,
) -> str:
    """Return a stable identifier binding a formal artifact to its target claim."""
    payload = json.dumps(
        {
            "action": action_name,
            "target_claim": _normalize_text(target_claim),
            "artifact": artifact.strip(),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:20]
    return f"formal-{digest}"


def attach_formal_evidence(
    action: Action,
    observation: ToolObservation,
    *,
    target_claim: str,
) -> ToolObservation:
    """Attach claim/artifact provenance to every formal tool observation."""
    if action.name not in FORMAL_ACTIONS:
        return observation

    artifact = _formal_artifact(action, observation)
    evidence_id = formal_evidence_id(
        action_name=action.name,
        target_claim=target_claim,
        artifact=artifact,
    )
    code = str(observation.lean_code or "")
    declarations = extract_formal_declarations(code)
    requested_declaration = str(action.args.get("declaration") or "").strip()
    primary = next(
        (
            item
            for item in declarations
            if requested_declaration and item["name"] == requested_declaration
        ),
        declarations[-1] if declarations and not requested_declaration else None,
    )
    requested_claim = str(
        action.args.get("claim")
        or action.args.get("statement")
        or target_claim
    ).strip()
    declared_claim = str(
        (primary or {}).get("signature") or requested_claim
    ).strip()
    metadata = dict(observation.metadata)
    metadata["formal_evidence"] = {
        "id": evidence_id,
        "action": action.name,
        "target_claim": target_claim.strip(),
        "declared_claim": declared_claim,
        "requested_claim": requested_claim,
        "requested_declaration": requested_declaration,
        "primary_declaration": dict(primary or {}),
        "declarations": declarations,
        "statement_bound": bool(primary),
        "axioms": _axioms_from_output(observation.output),
        "artifact_sha256": hashlib.sha256(artifact.encode("utf-8")).hexdigest(),
        "lean_code_sha256": (
            hashlib.sha256(code.encode("utf-8")).hexdigest() if code else ""
        ),
        "passed": bool(observation.success),
    }
    observation.metadata = metadata
    marker = f"Formal evidence ID: {evidence_id}"
    if marker not in observation.output:
        observation.output = f"{observation.output.rstrip()}\n\n{marker}".strip()
    return observation


def evidence_id_from_observation(observation: ToolObservation) -> str:
    evidence = observation.metadata.get("formal_evidence")
    if not isinstance(evidence, dict):
        return ""
    return str(evidence.get("id") or "").strip()


def formal_evidence_metadata(observation: ToolObservation) -> dict[str, Any]:
    evidence = observation.metadata.get("formal_evidence")
    return dict(evidence) if isinstance(evidence, dict) else {}


def extract_formal_declarations(code: str) -> list[dict[str, str]]:
    """Extract theorem-level signatures for evidence and fidelity review.

    Lean remains the authority for type correctness; this conservative parser
    gives the trust layer a concrete declaration to bind instead of copying the
    natural-language target into metadata.
    """
    declarations: list[tuple[int, dict[str, str]]] = []
    source = _strip_non_code(code or "")
    for match in _NAMED_DECLARATION_RE.finditer(source):
        signature = _normalize_signature(match.group(3))
        declarations.append(
            (
                match.start(),
                {
                    "kind": match.group(1),
                    "name": match.group(2),
                    "signature": signature,
                },
            )
        )
    for index, match in enumerate(_EXAMPLE_DECLARATION_RE.finditer(source), start=1):
        declarations.append(
            (
                match.start(),
                {
                    "kind": "example",
                    "name": f"<example:{index}>",
                    "signature": _normalize_signature(match.group(2)),
                },
            )
        )
    declarations.sort(key=lambda item: item[0])
    return [item for _, item in declarations]


def _axioms_from_output(output: str) -> list[str] | None:
    """Extract the `Axioms: ...` summary line appended after a strict pass.

    Returns the axiom list ([] for "none"), or None when the observation
    carries no axiom report (e.g. failed or draft checks).
    """
    match = re.search(r"(?m)^Axioms: (.+)$", output or "")
    if not match:
        return None
    raw = match.group(1).strip()
    if raw == "none":
        return []
    return [item.strip() for item in raw.split(",") if item.strip()]


def _formal_artifact(action: Action, observation: ToolObservation) -> str:
    if observation.lean_code:
        return str(observation.lean_code)
    if action.name == "lean_check":
        return str(action.args.get("code") or "")
    return str(action.args.get("statement") or action.args.get("claim") or "")


def _normalize_text(value: str) -> str:
    return " ".join((value or "").strip().split()).casefold()


def claims_match(left: str, right: str) -> bool:
    """Return True when two natural-language claims are semantically equal.

    This is intentionally strict: whitespace and casing are normalized, but
    the wording must match.  It is used to decide whether a piece of formal
    evidence that was bound to one claim may be used to conclude another.
    """
    return _normalize_text(left) == _normalize_text(right)


def _normalize_signature(value: str) -> str:
    return " ".join((value or "").strip().split())


def _strip_non_code(text: str) -> str:
    output: list[str] = []
    index = 0
    block_depth = 0
    in_string = False
    escaped = False
    while index < len(text):
        pair = text[index : index + 2]
        char = text[index]
        if block_depth:
            if pair == "/-":
                block_depth += 1
                output.extend("  ")
                index += 2
            elif pair == "-/":
                block_depth -= 1
                output.extend("  ")
                index += 2
            else:
                output.append("\n" if char == "\n" else " ")
                index += 1
            continue
        if in_string:
            output.append("\n" if char == "\n" else " ")
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            index += 1
            continue
        if pair == "/-":
            block_depth = 1
            output.extend("  ")
            index += 2
        elif pair == "--":
            newline = text.find("\n", index)
            if newline == -1:
                output.extend(" " * (len(text) - index))
                break
            output.extend(" " * (newline - index))
            index = newline
        elif char == '"':
            in_string = True
            output.append(" ")
            index += 1
        else:
            output.append(char)
            index += 1
    return "".join(output)
