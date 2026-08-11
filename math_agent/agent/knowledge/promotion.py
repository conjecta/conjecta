from __future__ import annotations

from typing import Any

from math_agent.agent.formal_evidence import extract_formal_declarations


def _declaration_signature(lean_code: str, name: str) -> str | None:
    """Return the cleaned signature line for ``name`` from ``lean_code``.

    Comments and docstrings are stripped before parsing so the returned
    statement is the actual code signature, not arbitrary prose.
    """
    declarations = extract_formal_declarations(lean_code)
    for declaration in declarations:
        if declaration["name"] == name:
            signature = declaration["signature"]
            return f"{name} {signature}".strip()
    return None


def promote_verified_lean(
    knowledge_store: Any,
    project_id: str,
    lean_code: str,
    *,
    evidence_id: str = "",
    accepted_evidence_id: str = "",
    status: str = "candidate",
) -> str:
    """Promote declarations from verified Lean code to the knowledge base.

    Only declarations from the artifact identified by ``accepted_evidence_id``
    are promoted.  The statement is extracted from the cleaned Lean signature,
    never from docstring prose.  The default status is ``candidate``; callers
    may pass ``approved`` only when the evidence has passed full verification.
    """
    if knowledge_store is None:
        return "No knowledge store configured; skipping promotion."

    if not accepted_evidence_id:
        return "No accepted evidence ID; skipping promotion."

    if evidence_id != accepted_evidence_id:
        return (
            "Evidence ID does not match the accepted artifact; skipping promotion."
        )

    declarations = extract_formal_declarations(lean_code)
    if not declarations:
        return "No theorem/lemma/def/instance found in verified code; nothing to promote."

    promoted: list[str] = []
    for declaration in declarations:
        decl_type = declaration["kind"]
        name = declaration["name"]
        statement = _declaration_signature(lean_code, name)
        if statement is None:
            statement = name
        try:
            if decl_type in {"theorem", "lemma"}:
                result = knowledge_store.add_fact(
                    project_id,
                    statement=statement,
                    why=f"Verified by lean_check in Lean 4 ({decl_type} {name}).",
                    status=status,
                )
            else:
                result = knowledge_store.add_trick(
                    project_id,
                    title=f"{decl_type} {name}",
                    body=statement,
                    category="verified_code",
                    status=status,
                )
            promoted.append(result.get("id", f"{decl_type}:{name}"))
        except Exception as exc:
            promoted.append(f"{name}(failed:{exc})")

    return f"Promoted {len(promoted)} verified declaration(s): {', '.join(promoted)}"
