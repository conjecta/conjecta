"""HTTP routes for static pages, health/version probes, and math news."""
from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from math_agent.web import agent_factory

STATIC_DIR = Path(os.getenv("CONJECTA_STATIC_DIR") or (Path(__file__).parent / "static")).resolve()
WEB_DIR = Path(__file__).resolve().parents[2] / "web"
PROJECT_PAGES = frozenset({"terms"})
DEPLOYMENT_VERSION = os.getenv("CONJECTA_DEPLOYMENT_VERSION") or datetime.now(timezone.utc).isoformat()

router = APIRouter(tags=["pages"])


@router.get("/")
async def project_home():
    return FileResponse(str(WEB_DIR / "index.html"))


@router.get("/healthz", include_in_schema=False)
async def healthz():
    return {"ok": True, "status": "healthy"}


@router.get("/api/version", include_in_schema=False)
async def version():
    return {"version": DEPLOYMENT_VERSION}


@router.get("/api/math-news")
async def get_math_news():
    store = agent_factory.math_news_store
    if store is None:
        return {"items": [], "updated_at": None}
    items = store.load()[:5]
    updated_at = store.updated_at()
    return {
        "items": [item.to_dict() for item in items],
        "updated_at": updated_at.isoformat() if updated_at else None,
    }


@router.get("/app")
@router.get("/app/")
async def app_index():
    return FileResponse(str(STATIC_DIR / "index.html"))


@router.get("/admin")
@router.get("/admin/")
async def admin_index():
    return FileResponse(str(STATIC_DIR / "index.html"))


@router.get("/app/friends")
@router.get("/app/friends/")
@router.get("/app/knowledge")
@router.get("/app/knowledge/")
@router.get("/app/knowledge/gallery")
@router.get("/app/knowledge/gallery/")
@router.get("/app/knowledge/friends")
@router.get("/app/knowledge/friends/")
@router.get("/app/knowledge/card/{card_id}")
@router.get("/app/knowledge/card/{card_id}/")
async def knowledge_workbench_index(card_id: str | None = None):
    return FileResponse(str(STATIC_DIR / "index.html"))


@router.get("/share/knowledge/{card_id}")
async def shared_knowledge_index(card_id: str):
    return FileResponse(str(STATIC_DIR / "index.html"))


@router.get("/{page}.html")
async def project_page(page: str):
    if page not in PROJECT_PAGES:
        raise HTTPException(status_code=404, detail="Not found")
    return FileResponse(str(WEB_DIR / f"{page}.html"))


@router.get("/styles.css")
async def project_styles():
    return FileResponse(str(WEB_DIR / "styles.css"))


@router.get("/home.css")
async def project_home_styles():
    return FileResponse(str(WEB_DIR / "home.css"))


@router.get("/i18n.js")
async def project_i18n():
    return FileResponse(str(WEB_DIR / "i18n.js"))


@router.get("/banner.js")
async def project_banner():
    return FileResponse(str(WEB_DIR / "banner.js"))


@router.get("/contact.js")
async def project_contact():
    return FileResponse(str(WEB_DIR / "contact.js"))
