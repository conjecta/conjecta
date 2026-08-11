"""Project membership and access resolution for collaborative projects."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal

from fastapi import HTTPException

from math_agent.knowledge.supabase_client import (
    create_supabase_client,
    is_transient_supabase_error,
    run_supabase,
    service_role_configured,
)
from math_agent.web.jwt_auth import normalize_phone, user_id_for_phone
from math_agent.web.project_repository import ProjectRepository
from math_agent.web.project_store import validate_project_id
from math_agent.web.user_store import UserStore

MEMBERS_TABLE = "project_members"
Role = Literal["lead", "collaborator"]
_CLOUD_REQUIRED = "CLOUD_STORAGE_REQUIRED"

# Bounded reads: membership lists are never expected to exceed these; a hard
# limit keeps list endpoints from degenerating into unbounded scans.
_MEMBERSHIPS_LIMIT = 500
_PROJECT_MEMBERS_LIMIT = 200


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _reraise_supabase_transport(exc: BaseException) -> None:
    """Turn exhausted Supabase transport failures into a stable 503."""
    if isinstance(exc, HTTPException):
        raise exc
    if is_transient_supabase_error(exc):
        raise HTTPException(
            status_code=503,
            detail="服务暂时遇到问题，请稍后重试。",
        ) from exc
    raise exc


def require_collab_cloud() -> Any:
    if not service_role_configured():
        raise HTTPException(status_code=503, detail=_CLOUD_REQUIRED)
    try:
        return create_supabase_client(prefer_service_role=True, require_service_role=True)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=_CLOUD_REQUIRED) from exc


@dataclass(frozen=True)
class ProjectAccess:
    actor_user_id: str
    owner_user_id: str
    project_id: str
    role: Role

    @property
    def can_manage_members(self) -> bool:
        return self.role == "lead"

    @property
    def can_edit_knowledge(self) -> bool:
        return self.role in ("lead", "collaborator")

    @property
    def knowledge_tenant_id(self) -> str:
        """User id used for knowledge rows and agent ProjectContext."""
        return self.owner_user_id


class ProjectAccessService:
    """Resolve lead/collaborator access and manage membership."""

    def __init__(self, client: Any | None = None) -> None:
        # Keep injected fakes for tests; otherwise resolve via create_client so
        # run_supabase retries can drop a dead HTTP/2 session.
        self._client_override = client
        self.repo = ProjectRepository(client=client)
        self.users = UserStore(client=client)

    @property
    def client(self) -> Any:
        if self._client_override is not None:
            return self._client_override
        return require_collab_cloud()

    @client.setter
    def client(self, value: Any) -> None:
        self._client_override = value
        self.repo.client = value
        self.users.client = value

    def _personal_lead_access(
        self, actor_user_id: str, project_id: str, *, create: bool = False
    ) -> ProjectAccess:
        """Return lead access for the actor's own project namespace.

        Solo projects are valid without ``project_members`` rows, and a
        personal project implicitly exists for its owner, so resolving access
        never 404s here.  The cloud ``conjecta_projects`` row is materialized
        only on write paths (``create=True``) — read-only resolves must not
        create rows for mistyped or never-saved project ids.
        """
        if create and not self.repo.owns_project(actor_user_id, project_id):
            self.repo.upsert_project(
                actor_user_id,
                project_id,
                {"id": project_id, "name": project_id},
            )
        return ProjectAccess(
            actor_user_id=actor_user_id,
            owner_user_id=actor_user_id,
            project_id=project_id,
            role="lead",
        )

    def resolve(
        self,
        actor_user_id: str,
        project_id: str,
        *,
        owner_user_id: str | None = None,
        create_if_missing: bool = False,
    ) -> ProjectAccess:
        project_id = validate_project_id(project_id)
        owner = (owner_user_id or "").strip() or None

        if owner is None or owner == actor_user_id:
            if self.repo.owns_project(actor_user_id, project_id):
                return ProjectAccess(
                    actor_user_id=actor_user_id,
                    owner_user_id=actor_user_id,
                    project_id=project_id,
                    role="lead",
                )
            if owner == actor_user_id:
                # Explicit self-owner hint for a not-yet-registered personal
                # project (e.g. first PUT/create).
                return self._personal_lead_access(
                    actor_user_id, project_id, create=create_if_missing
                )
            # No owner hint: collaborator membership first, else personal project.
            membership = self._membership_for_actor(actor_user_id, project_id)
            if membership is not None:
                return ProjectAccess(
                    actor_user_id=actor_user_id,
                    owner_user_id=str(membership["owner_user_id"]),
                    project_id=project_id,
                    role=str(membership["role"]),  # type: ignore[arg-type]
                )
            return self._personal_lead_access(
                actor_user_id, project_id, create=create_if_missing
            )

        # Explicit owner different from actor
        if self.repo.owns_project(owner, project_id):
            membership = self._get_member(owner, project_id, actor_user_id)
            if membership is None:
                raise HTTPException(status_code=404, detail="Project not found.")
            role = str(membership.get("role") or "collaborator")
            if role not in ("lead", "collaborator"):
                role = "collaborator"
            return ProjectAccess(
                actor_user_id=actor_user_id,
                owner_user_id=owner,
                project_id=project_id,
                role=role,  # type: ignore[arg-type]
            )
        raise HTTPException(status_code=404, detail="Project not found.")

    def list_accessible_projects(self, actor_user_id: str) -> list[dict[str, Any]]:
        owned = self.repo.list_projects(actor_user_id)
        for item in owned:
            item["owner_user_id"] = actor_user_id
            item["role"] = "lead"
        member_rows = self._memberships_for_user(actor_user_id)
        seen = {(actor_user_id, str(p["id"])) for p in owned}
        by_owner: dict[str, list[str]] = {}
        roles: dict[tuple[str, str], str] = {}
        for row in member_rows:
            owner = str(row.get("owner_user_id") or "")
            pid = str(row.get("project_id") or "")
            if not owner or not pid or (owner, pid) in seen:
                continue
            if str(row.get("role")) == "lead" and owner == actor_user_id:
                continue
            by_owner.setdefault(owner, []).append(pid)
            roles[(owner, pid)] = str(row.get("role") or "collaborator")
            seen.add((owner, pid))
        # One query per distinct owner instead of one per membership row.
        for owner, pids in by_owner.items():
            try:
                projects = self.repo.get_projects_by_ids(owner, pids)
            except Exception:
                # Fall back to per-project lookups (older fakes / partial clients).
                projects = {}
                for pid in pids:
                    data = self.repo.get_project(owner, pid)
                    if data is not None:
                        projects[pid] = data
            for pid, cloud in projects.items():
                project = cloud.get("project") or {}
                owned.append(
                    {
                        "id": pid,
                        "name": project.get("name") or pid,
                        "updatedAt": cloud.get("updatedAt") or "",
                        "starred": bool(cloud.get("starred", False)),
                        "owner_user_id": owner,
                        "role": roles[(owner, pid)],
                    }
                )
        return sorted(owned, key=lambda item: (str(item.get("owner_user_id") or ""), str(item.get("id") or "")))

    def list_members(self, access: ProjectAccess) -> list[dict[str, Any]]:
        rows = self._members_for_project(access.owner_user_id, access.project_id)
        # Ensure lead is always represented even before first collaborator invite.
        has_lead = any(str(r.get("role")) == "lead" for r in rows)
        if not has_lead:
            rows = [
                {
                    "owner_user_id": access.owner_user_id,
                    "project_id": access.project_id,
                    "user_id": access.owner_user_id,
                    "role": "lead",
                    "added_by": access.owner_user_id,
                    "created_at": "",
                },
                *rows,
            ]
        profiles = self.users.get_profiles(
            [str(row.get("user_id") or "") for row in rows]
        )
        out: list[dict[str, Any]] = []
        for row in rows:
            uid = str(row.get("user_id") or "")
            profile = profiles.get(uid) or {}
            display = str(profile.get("display_name") or "").strip()
            phone_masked = str(profile.get("phone_masked") or "")
            out.append(
                {
                    "user_id": uid,
                    "role": str(row.get("role") or "collaborator"),
                    "display_name": display,
                    "phone_masked": phone_masked,
                    "label": display or phone_masked or uid,
                    "added_by": str(row.get("added_by") or ""),
                    "created_at": str(row.get("created_at") or ""),
                }
            )
        return out

    def add_member(
        self,
        access: ProjectAccess,
        member_user_id: str = "",
        *,
        phone: str | None = None,
    ) -> dict[str, Any]:
        if not access.can_manage_members:
            raise HTTPException(status_code=404, detail="Project not found.")
        member_user_id = str(member_user_id or "").strip()
        if not member_user_id and phone and str(phone).strip():
            member_user_id = self._resolve_user_id_by_phone(str(phone).strip())
        if not member_user_id:
            raise HTTPException(status_code=400, detail="user_id or phone is required.")
        if member_user_id == access.owner_user_id:
            raise HTTPException(status_code=400, detail="Lead is already a member.")
        try:
            profile = self.users.get_profile(member_user_id)
        except Exception as exc:
            _reraise_supabase_transport(exc)
            raise  # pragma: no cover — _reraise always raises
        if profile is None:
            raise HTTPException(status_code=404, detail="User not found.")
        self._ensure_lead_row(access)
        existing = self._get_member(access.owner_user_id, access.project_id, member_user_id)
        if existing is not None:
            return {"member": existing, "created": False}
        row = {
            "owner_user_id": access.owner_user_id,
            "project_id": access.project_id,
            "user_id": member_user_id,
            "role": "collaborator",
            "added_by": access.actor_user_id,
            "created_at": _now(),
        }
        # upsert (PK: owner/project/user) keeps concurrent adds idempotent
        # instead of surfacing a 500 on a duplicate-key race.
        try:
            resp = run_supabase(
                lambda: self.client.table(MEMBERS_TABLE).upsert(row).execute()
            )
        except Exception as exc:
            _reraise_supabase_transport(exc)
        member = (resp.data or [row])[0]
        return {"member": member, "created": True}

    def _resolve_user_id_by_phone(self, phone: str) -> str:
        try:
            normalized = normalize_phone(phone)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        target = user_id_for_phone(normalized)
        profile = self.users.get_profile(target)
        if profile is not None:
            return str(profile.get("id") or target)
        by_phone = self.users.find_by_phone(normalized)
        if by_phone is None:
            raise HTTPException(status_code=404, detail="User not found.")
        return str(by_phone["id"])

    def remove_member(self, access: ProjectAccess, member_user_id: str) -> dict[str, Any]:
        if not access.can_manage_members:
            raise HTTPException(status_code=404, detail="Project not found.")
        member_user_id = str(member_user_id or "").strip()
        if member_user_id == access.owner_user_id:
            raise HTTPException(status_code=400, detail="Cannot remove the project lead.")
        existing = self._get_member(access.owner_user_id, access.project_id, member_user_id)
        if existing is None:
            raise HTTPException(status_code=404, detail="Member not found.")
        try:
            run_supabase(
                lambda: (
                    self.client.table(MEMBERS_TABLE)
                    .delete()
                    .eq("owner_user_id", access.owner_user_id)
                    .eq("project_id", access.project_id)
                    .eq("user_id", member_user_id)
                    .execute()
                )
            )
        except Exception as exc:
            _reraise_supabase_transport(exc)
        return {"deleted": True, "user_id": member_user_id}

    def _ensure_lead_row(self, access: ProjectAccess) -> None:
        existing = self._get_member(access.owner_user_id, access.project_id, access.owner_user_id)
        if existing is not None:
            return
        row = {
            "owner_user_id": access.owner_user_id,
            "project_id": access.project_id,
            "user_id": access.owner_user_id,
            "role": "lead",
            "added_by": access.actor_user_id,
            "created_at": _now(),
        }
        try:
            run_supabase(lambda: self.client.table(MEMBERS_TABLE).upsert(row).execute())
        except Exception as exc:
            _reraise_supabase_transport(exc)

    def _get_member(
        self, owner_user_id: str, project_id: str, user_id: str
    ) -> dict[str, Any] | None:
        try:
            resp = run_supabase(
                lambda: (
                    self.client.table(MEMBERS_TABLE)
                    .select("*")
                    .eq("owner_user_id", owner_user_id)
                    .eq("project_id", project_id)
                    .eq("user_id", user_id)
                    .limit(1)
                    .execute()
                )
            )
        except Exception as exc:
            _reraise_supabase_transport(exc)
        rows = resp.data or []
        return dict(rows[0]) if rows else None

    def _members_for_project(self, owner_user_id: str, project_id: str) -> list[dict[str, Any]]:
        try:
            resp = run_supabase(
                lambda: (
                    self.client.table(MEMBERS_TABLE)
                    .select("*")
                    .eq("owner_user_id", owner_user_id)
                    .eq("project_id", project_id)
                    .limit(_PROJECT_MEMBERS_LIMIT)
                    .execute()
                )
            )
        except Exception as exc:
            _reraise_supabase_transport(exc)
        return [dict(r) for r in (resp.data or [])]

    def _memberships_for_user(self, user_id: str) -> list[dict[str, Any]]:
        try:
            resp = run_supabase(
                lambda: (
                    self.client.table(MEMBERS_TABLE)
                    .select("*")
                    .eq("user_id", user_id)
                    .limit(_MEMBERSHIPS_LIMIT)
                    .execute()
                )
            )
        except Exception as exc:
            _reraise_supabase_transport(exc)
        return [dict(r) for r in (resp.data or [])]

    def _membership_for_actor(self, actor_user_id: str, project_id: str) -> dict[str, Any] | None:
        try:
            resp = run_supabase(
                lambda: (
                    self.client.table(MEMBERS_TABLE)
                    .select("*")
                    .eq("user_id", actor_user_id)
                    .eq("project_id", project_id)
                    .execute()
                )
            )
        except Exception as exc:
            _reraise_supabase_transport(exc)
        rows = resp.data or []
        if not rows:
            return None
        if len(rows) == 1:
            return dict(rows[0])
        # Ambiguous project_id across owners — caller should pass owner_user_id.
        raise HTTPException(
            status_code=400,
            detail="owner_user_id is required when the project id is shared across owners.",
        )


def project_access_service_or_none() -> ProjectAccessService | None:
    if not service_role_configured():
        return None
    try:
        return ProjectAccessService()
    except Exception:
        return None


def resolve_project_access(
    actor_user_id: str,
    project_id: str,
    *,
    owner_user_id: str | None = None,
    client: Any | None = None,
    create_if_missing: bool = False,
) -> ProjectAccess:
    """Resolve access; local personal fallback only when cloud is not configured.

    When Supabase IS configured, a cloud failure is fail-closed (503): silently
    falling back to a personal lead namespace would redirect collaborators into
    the wrong tenant and misplace their writes.
    """
    if service_role_configured() or client is not None:
        try:
            svc = ProjectAccessService(client=client) if client is not None else ProjectAccessService()
            return svc.resolve(
                actor_user_id,
                project_id,
                owner_user_id=owner_user_id,
                create_if_missing=create_if_missing,
            )
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=503, detail=_CLOUD_REQUIRED) from exc
    # Local / no-cloud: only personal projects.
    owner = (owner_user_id or actor_user_id).strip()
    if owner != actor_user_id:
        raise HTTPException(status_code=404, detail="Project not found.")
    project_id = validate_project_id(project_id)
    return ProjectAccess(
        actor_user_id=actor_user_id,
        owner_user_id=actor_user_id,
        project_id=project_id,
        role="lead",
    )
