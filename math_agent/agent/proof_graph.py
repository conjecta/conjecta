from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any


_GOAL_STATUSES = frozenset({"pending", "in_progress", "proved", "failed"})


def goal_id_for(statement: str) -> str:
    normalized = " ".join((statement or "").strip().casefold().split())
    return f"goal-{hashlib.sha256(normalized.encode('utf-8')).hexdigest()[:12]}"


@dataclass
class ProofGoal:
    id: str
    statement: str
    depends_on: list[str] = field(default_factory=list)
    status: str = "pending"
    attempts: int = 0
    evidence_id: str = ""
    issues: list[str] = field(default_factory=list)
    priority: int = 0
    verification_policy: str = "review"
    attempts_log: list[dict[str, Any]] = field(default_factory=list)
    counterexample_results: list[dict[str, Any]] = field(default_factory=list)
    accepted_artifact_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "statement": self.statement,
            "depends_on": list(self.depends_on),
            "status": self.status,
            "attempts": self.attempts,
            "evidence_id": self.evidence_id,
            "issues": list(self.issues),
            "priority": self.priority,
            "verification_policy": self.verification_policy,
            "attempts_log": [dict(item) for item in self.attempts_log],
            "counterexample_results": [
                dict(item) for item in self.counterexample_results
            ],
            "accepted_artifact_id": self.accepted_artifact_id,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "ProofGoal":
        statement = str(raw.get("statement") or "").strip()
        goal_id = str(raw.get("id") or goal_id_for(statement)).strip()
        status = str(raw.get("status") or "pending")
        if status not in _GOAL_STATUSES:
            status = "pending"
        dependencies = raw.get("depends_on") or []
        issues = raw.get("issues") or []
        return cls(
            id=goal_id,
            statement=statement,
            depends_on=(
                [str(item) for item in dependencies]
                if isinstance(dependencies, list)
                else []
            ),
            status=status,
            attempts=max(0, _coerce_int(raw.get("attempts"))),
            evidence_id=str(raw.get("evidence_id") or ""),
            issues=[str(item) for item in issues] if isinstance(issues, list) else [],
            priority=_coerce_int(raw.get("priority")),
            verification_policy=str(raw.get("verification_policy") or "review"),
            attempts_log=[
                dict(item)
                for item in (raw.get("attempts_log") or [])
                if isinstance(item, dict)
            ],
            counterexample_results=[
                dict(item)
                for item in (raw.get("counterexample_results") or [])
                if isinstance(item, dict)
            ],
            accepted_artifact_id=str(raw.get("accepted_artifact_id") or ""),
        )


@dataclass
class ProofGraph:
    goals: dict[str, ProofGoal] = field(default_factory=dict)
    root_id: str = ""
    active_goal_id: str = ""

    def ensure_root(self, statement: str) -> ProofGoal:
        if self.root_id and self.root_id in self.goals:
            return self.goals[self.root_id]
        root = self.upsert_goal(statement)
        self.root_id = root.id
        if not self.active_goal_id:
            self.active_goal_id = root.id
            root.status = "in_progress"
        return root

    def upsert_goal(
        self,
        statement: str,
        *,
        goal_id: str = "",
        depends_on: list[str] | None = None,
        activate: bool = False,
        priority: int | None = None,
        verification_policy: str | None = None,
    ) -> ProofGoal:
        statement = (statement or "").strip()
        if not statement:
            raise ValueError("Proof goal statement cannot be empty.")
        resolved_id = (goal_id or goal_id_for(statement)).strip()
        dependencies = list(dict.fromkeys(depends_on or []))
        if resolved_id in dependencies:
            raise ValueError("A proof goal cannot depend on itself.")
        goal = self.goals.get(resolved_id)
        previous = goal.to_dict() if goal is not None else None
        if goal is None:
            goal = ProofGoal(
                id=resolved_id,
                statement=statement,
                depends_on=dependencies,
            )
            self.goals[resolved_id] = goal
        else:
            goal.statement = statement
            goal.depends_on = dependencies
        if priority is not None:
            goal.priority = int(priority)
        if verification_policy:
            goal.verification_policy = str(verification_policy)
        if self._has_cycle():
            if previous is None:
                self.goals.pop(resolved_id, None)
            else:
                self.goals[resolved_id] = ProofGoal.from_dict(previous)
            raise ValueError("Proof goal dependencies must be acyclic.")
        if activate:
            self.activate(resolved_id)
        return goal

    def activate(self, goal_id: str) -> ProofGoal:
        if goal_id not in self.goals:
            raise KeyError(f"Unknown proof goal: {goal_id}")
        goal = self.goals[goal_id]
        self.active_goal_id = goal_id
        if goal.status != "proved":
            goal.status = "in_progress"
        return goal

    def active_goal(self) -> ProofGoal | None:
        return self.goals.get(self.active_goal_id)

    def record_formal_attempt(
        self,
        *,
        success: bool,
        evidence_id: str,
        issue: str = "",
    ) -> None:
        goal = self.active_goal()
        if goal is None:
            return
        goal.attempts += 1
        if success:
            goal.status = "proved"
            goal.evidence_id = evidence_id
            goal.issues = []
        else:
            goal.status = "in_progress"
            if issue and issue not in goal.issues:
                goal.issues.append(issue)

    def mark_proved(self, goal_id: str, *, evidence_id: str = "") -> None:
        goal = self.goals.get(goal_id)
        if goal is None:
            return
        goal.status = "proved"
        if evidence_id:
            goal.evidence_id = evidence_id
            goal.accepted_artifact_id = evidence_id
        goal.issues = []

    def mark_failed(self, goal_id: str, *, issue: str = "") -> None:
        goal = self.goals.get(goal_id)
        if goal is None:
            return
        goal.status = "failed"
        if issue and issue not in goal.issues:
            goal.issues.append(issue)

    def reset_goal(self, goal_id: str, *, cascade: bool = True) -> list[str]:
        """Reset one goal to pending, optionally cascading to its dependents.

        Clears ``evidence_id``/``accepted_artifact_id`` so the goal is attacked
        again, but keeps ``attempts``/``attempts_log`` as an audit trail. With
        ``cascade`` every goal that depends on the target (directly or
        transitively — including the root when affected) is reset as well,
        since their proofs relied on the invalidated evidence. Returns the ids
        of the reset goals, target first, in breadth-first dependency order.
        """
        if goal_id not in self.goals:
            raise KeyError(f"Unknown proof goal: {goal_id}")
        reset_ids = [goal_id]
        if cascade:
            dependents: dict[str, list[str]] = {}
            for goal in self.goals.values():
                for dependency in goal.depends_on:
                    dependents.setdefault(dependency, []).append(goal.id)
            seen = {goal_id}
            queue = [goal_id]
            while queue:
                current = queue.pop(0)
                for dependent in dependents.get(current, []):
                    # The seen-set keeps the walk finite even if a hand-built
                    # graph somehow contains a dependency cycle.
                    if dependent in seen:
                        continue
                    seen.add(dependent)
                    reset_ids.append(dependent)
                    queue.append(dependent)
        for reset_id in reset_ids:
            goal = self.goals[reset_id]
            goal.status = "pending"
            goal.evidence_id = ""
            goal.accepted_artifact_id = ""
        return reset_ids

    def edit_goal_statement(self, goal_id: str, new_statement: str) -> list[str]:
        """Rewrite a goal's statement in place, then reset it and its dependents.

        The goal keeps its existing id: ``goal_id_for`` hashing only dedups
        goals at creation time, so a post-edit id/statement mismatch is
        acceptable. Returns the ids from :meth:`reset_goal`.
        """
        if goal_id not in self.goals:
            raise KeyError(f"Unknown proof goal: {goal_id}")
        statement = (new_statement or "").strip()
        if not statement:
            raise ValueError("Proof goal statement cannot be empty.")
        self.goals[goal_id].statement = statement
        return self.reset_goal(goal_id, cascade=True)

    def record_attempt(self, goal_id: str, attempt: dict[str, Any]) -> None:
        goal = self.goals.get(goal_id)
        if goal is None:
            return
        goal.attempts += 1
        goal.attempts_log.append(dict(attempt))

    def record_counterexample(
        self, goal_id: str, result: dict[str, Any]
    ) -> None:
        goal = self.goals.get(goal_id)
        if goal is None:
            return
        goal.counterexample_results.append(dict(result))

    def ready_goals(self) -> list[ProofGoal]:
        ready = [
            goal
            for goal in self.goals.values()
            if goal.status == "pending"
            and all(
                dependency in self.goals
                and self.goals[dependency].status == "proved"
                for dependency in goal.depends_on
            )
        ]
        return sorted(ready, key=lambda goal: (-goal.priority, goal.id))

    def context_block(self, *, max_goals: int = 12) -> str:
        if not self.goals:
            return ""
        lines = ["Proof goal graph:"]
        for goal in list(self.goals.values())[:max_goals]:
            active = " active" if goal.id == self.active_goal_id else ""
            dependencies = (
                f" depends_on={','.join(goal.depends_on)}"
                if goal.depends_on
                else ""
            )
            lines.append(
                f"- [{goal.status}{active}] {goal.id}: {goal.statement}"
                f"{dependencies} attempts={goal.attempts}"
            )
            if goal.accepted_artifact_id:
                lines[-1] += f" artifact={goal.accepted_artifact_id}"
            if goal.issues:
                rendered_issues = "; ".join(
                    str(issue)[:200] for issue in goal.issues[-3:]
                )
                lines[-1] += f" issues={rendered_issues}"
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "root_id": self.root_id,
            "active_goal_id": self.active_goal_id,
            "goals": [goal.to_dict() for goal in self.goals.values()],
        }

    @classmethod
    def from_dict(cls, raw: Any) -> "ProofGraph":
        if not isinstance(raw, dict):
            return cls()
        graph = cls(
            root_id=str(raw.get("root_id") or ""),
            active_goal_id=str(raw.get("active_goal_id") or ""),
        )
        raw_goals = raw.get("goals") or []
        if isinstance(raw_goals, list):
            for raw_goal in raw_goals:
                if isinstance(raw_goal, dict):
                    goal = ProofGoal.from_dict(raw_goal)
                    if goal.id and goal.statement:
                        graph.goals[goal.id] = goal
        if graph.root_id not in graph.goals:
            graph.root_id = ""
        if graph.active_goal_id not in graph.goals:
            graph.active_goal_id = graph.root_id
        return graph

    def _has_cycle(self) -> bool:
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(goal_id: str) -> bool:
            if goal_id in visiting:
                return True
            if goal_id in visited or goal_id not in self.goals:
                return False
            visiting.add(goal_id)
            for dependency in self.goals[goal_id].depends_on:
                if visit(dependency):
                    return True
            visiting.remove(goal_id)
            visited.add(goal_id)
            return False

        return any(visit(goal_id) for goal_id in self.goals)


def _coerce_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0
