"""Supabase-backed project metadata owned by a phone tenant."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from math_agent.knowledge.supabase_client import (
    create_supabase_client,
    run_supabase,
    service_role_configured,
)
from math_agent.web.project_store import validate_project_id

PROJECTS_TABLE = "conjecta_projects"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ProjectRepository:
    """CRUD for Conjecta-owned projects filtered by owner_user_id."""

    def __init__(self, client: Any | None = None) -> None:
        self._client_override = client

    @property
    def client(self) -> Any:
        if self._client_override is not None:
            return self._client_override
        return create_supabase_client(prefer_service_role=True)

    @client.setter
    def client(self, value: Any) -> None:
        self._client_override = value

    def list_projects(self, owner_user_id: str) -> list[dict[str, Any]]:
        resp = run_supabase(
            lambda: (
                self.client.table(PROJECTS_TABLE)
                .select("id, name, starred, updated_at, payload")
                .eq("owner_user_id", owner_user_id)
                .order("updated_at", desc=True)
                .execute()
            )
        )
        rows = resp.data or []
        out: list[dict[str, Any]] = []
        for row in rows:
            out.append(
                {
                    "id": row.get("id"),
                    "name": row.get("name") or row.get("id"),
                    "updatedAt": row.get("updated_at") or "",
                    "starred": bool(row.get("starred", False)),
                }
            )
        return sorted(out, key=lambda item: str(item.get("id") or ""))

    def get_project(self, owner_user_id: str, project_id: str) -> dict[str, Any] | None:
        project_id = validate_project_id(project_id)
        resp = run_supabase(
            lambda: (
                self.client.table(PROJECTS_TABLE)
                .select("*")
                .eq("owner_user_id", owner_user_id)
                .eq("id", project_id)
                .limit(1)
                .execute()
            )
        )
        rows = resp.data or []
        if not rows:
            return None
        return self._to_response(rows[0])

    def get_projects_by_ids(
        self, owner_user_id: str, project_ids: list[str]
    ) -> dict[str, dict[str, Any]]:
        """Batch-fetch projects of one owner; returns {project_id: response}."""
        ids = [str(pid or "").strip() for pid in project_ids]
        ids = [pid for pid in dict.fromkeys(ids) if pid]
        if not ids:
            return {}
        resp = run_supabase(
            lambda: (
                self.client.table(PROJECTS_TABLE)
                .select("*")
                .eq("owner_user_id", owner_user_id)
                .in_("id", ids)
                .execute()
            )
        )
        out: dict[str, dict[str, Any]] = {}
        for row in resp.data or []:
            pid = str(row.get("id") or "")
            if pid:
                out[pid] = self._to_response(row)
        return out

    def upsert_project(
        self,
        owner_user_id: str,
        project_id: str,
        project: dict[str, Any],
        *,
        starred: bool | None = None,
    ) -> dict[str, Any]:
        project_id = validate_project_id(project_id)
        project = dict(project or {})
        project["id"] = project_id
        name = str(project.get("name") or project_id)
        now = _now()
        project.setdefault("updatedAt", now)
        star = bool(project.get("starred", False) if starred is None else starred)
        row = {
            "owner_user_id": owner_user_id,
            "id": project_id,
            "name": name,
            "starred": star,
            "payload": project,
            "updated_at": now,
        }
        existing = self.get_project(owner_user_id, project_id)
        if existing is None:
            row["created_at"] = now
            resp = run_supabase(
                lambda: self.client.table(PROJECTS_TABLE).insert(row).execute()
            )
        else:
            resp = run_supabase(
                lambda: (
                    self.client.table(PROJECTS_TABLE)
                    .update(
                        {
                            "name": name,
                            "starred": star,
                            "payload": project,
                            "updated_at": now,
                        }
                    )
                    .eq("owner_user_id", owner_user_id)
                    .eq("id", project_id)
                    .execute()
                )
            )
        data = (resp.data or [None])[0]
        return self._to_response(data) if data else self.get_project(owner_user_id, project_id) or {}

    def set_starred(self, owner_user_id: str, project_id: str, starred: bool) -> dict[str, Any]:
        project_id = validate_project_id(project_id)
        current = self.get_project(owner_user_id, project_id)
        if current is None:
            from fastapi import HTTPException

            raise HTTPException(status_code=404, detail="Project not found.")
        payload = dict(current.get("project") or {})
        payload["starred"] = bool(starred)
        return self.upsert_project(owner_user_id, project_id, payload, starred=bool(starred))

    def owns_project(self, owner_user_id: str, project_id: str) -> bool:
        return self.get_project(owner_user_id, project_id) is not None

    @staticmethod
    def _to_response(row: dict[str, Any]) -> dict[str, Any]:
        payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
        project = dict(payload)
        project["id"] = row.get("id")
        project.setdefault("name", row.get("name") or row.get("id"))
        project["starred"] = bool(row.get("starred", False))
        project.setdefault("updatedAt", row.get("updated_at") or "")
        return {
            "project": project,
            "starred": bool(row.get("starred", False)),
            "updatedAt": row.get("updated_at") or project.get("updatedAt") or "",
        }


def project_repository_or_none() -> ProjectRepository | None:
    if not service_role_configured():
        return None
    try:
        return ProjectRepository()
    except Exception:
        return None
