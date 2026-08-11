"""Versioned public schemas for the Conjecta agent API and persisted artifacts.

These dataclasses form a stable contract between the backend runner, the HTTP
service layer, and the frontend.  Existing internal models remain authoritative
for computation; these schemas are the versioned view that consumers should
rely on.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from math_agent.build_info import package_version, source_commit


@dataclass(frozen=True)
class ProofTaskV1:
    """A mathematical statement the agent should prove."""

    statement: str
    name: str = "unnamed"
    context: tuple[str, ...] = ()
    target_system: str = "lean4"


@dataclass(frozen=True)
class FormalizationCandidateV1:
    """A candidate formalization of a source statement."""

    id: str
    task_id: str
    name: str
    theorem_name: str
    lean_file: str
    informal_statement: str
    status: str | None = None
    requires_human_review: bool = True
    uses_placeholder_axiom: bool = False
    contains_sorry: bool = False
    candidate_level: str | None = None
    schema_version: str = "formalization_candidate.v1"


@dataclass(frozen=True)
class LeanCheckResultV1:
    """Outcome of the static + executable Lean verifier gate."""

    lean_file: str
    lean_available: bool
    static_ok: bool
    accepted: bool
    verification_ok: bool | None = None
    returncode: int | None = None
    stdout: str = ""
    stderr: str = ""
    blocked_tokens: list[str] = field(default_factory=list)
    diagnostics: list[dict[str, Any]] = field(default_factory=list)
    scanned_files: list[str] = field(default_factory=list)
    failure_kind: str | None = None
    schema_version: str = "lean_check_result.v1"


@dataclass(frozen=True)
class StructuralReviewV1:
    """Heuristic static review of a formalization candidate."""

    candidate_id: str
    task_id: str
    name: str
    overall_status: str
    regulator_action: str
    risk_flags: list[str]
    can_promote_proof: bool = False
    requires_human_or_formal_confirmation: bool = True
    schema_version: str = "structural_review.v1"


@dataclass(frozen=True)
class RunEventV1:
    """External progress event emitted by a proof run."""

    index: int
    state: str
    event_type: str
    message: str
    data: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ArtifactV1:
    """An artifact produced during a proof run."""

    name: str
    path: str
    artifact_type: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ReviewDecisionV1:
    """A human or automated review decision for a formalization candidate."""

    task_id: str
    candidate_id: str
    name: str
    decision: str
    review_status: str
    reviewer: str | None = None
    reviewed_at: str | None = None
    schema_version: str = "review_decision.v1"


@dataclass(frozen=True)
class ProofRunV1:
    """Public snapshot of a single proof-agent iteration."""

    run_id: str
    task_id: str
    name: str
    state: str
    accepted: bool
    paper_proof_ready: bool
    candidate_level: str | None
    current_candidate_id: str | None
    verifier_runs: list[dict[str, Any]]
    artifacts: list[dict[str, Any]]
    events: list[RunEventV1]
    next_actions: list[str]
    blocked_reasons: list[str]
    schema_version: str = "proof_run.v1"
    source_commit: str = field(default_factory=source_commit)
    package_version: str = field(default_factory=package_version)
