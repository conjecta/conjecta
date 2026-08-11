from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import math_agent.web.app as web_app
import math_agent.web.solve_routes as solve_routes

_PNG_BYTES = b"\x89PNG\r\n\x1a\nfake-png-payload"


@pytest.fixture
def client(tmp_path, monkeypatch):
    figure_dir = tmp_path / "sess-1" / "figures"
    figure_dir.mkdir(parents=True)
    (figure_dir / "fig-1.png").write_bytes(_PNG_BYTES)
    other_dir = tmp_path / "other-session" / "figures"
    other_dir.mkdir(parents=True)
    (other_dir / "fig-1.png").write_bytes(_PNG_BYTES)
    config = SimpleNamespace(agent=SimpleNamespace(artifact_root=str(tmp_path)))
    monkeypatch.setattr(solve_routes, "load_config", lambda: config)
    monkeypatch.delenv("CONJECTA_JWT_SECRET", raising=False)
    monkeypatch.setenv("CONJECTA_ALLOW_UNAUTHENTICATED", "1")

    store = SimpleNamespace(
        get_checkpoint=lambda session_id: (
            {"session_id": session_id} if session_id == "sess-1" else None
        )
    )
    monkeypatch.setattr(
        solve_routes, "_project_store", lambda user_id=None: store
    )
    return TestClient(web_app.app)


def test_serves_existing_figure(client):
    resp = client.get("/api/solve/figures/sess-1/fig-1.png")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/png"
    assert resp.content == _PNG_BYTES


def test_missing_figure_is_404(client):
    assert client.get("/api/solve/figures/sess-1/missing.png").status_code == 404
    # File exists on disk but session is not owned by this user → 404 (IDOR).
    assert client.get("/api/solve/figures/other-session/fig-1.png").status_code == 404


def test_rejects_unsafe_path_segments(client):
    # ".." sanitizes to "artifact" and no longer matches the raw segment.
    assert client.get("/api/solve/figures/%2E%2E/fig-1.png").status_code == 404
    # Characters outside the safe set are rejected, non-png names too.
    assert client.get("/api/solve/figures/sess-1/fig;1.png").status_code == 404
    assert client.get("/api/solve/figures/sess-1/fig-1.txt").status_code == 404


def test_rejects_unowned_session_even_when_file_exists(tmp_path, monkeypatch):
    figure_dir = tmp_path / "foreign" / "figures"
    figure_dir.mkdir(parents=True)
    (figure_dir / "fig-1.png").write_bytes(_PNG_BYTES)
    config = SimpleNamespace(agent=SimpleNamespace(artifact_root=str(tmp_path)))
    monkeypatch.setattr(solve_routes, "load_config", lambda: config)
    monkeypatch.delenv("CONJECTA_JWT_SECRET", raising=False)
    monkeypatch.setenv("CONJECTA_ALLOW_UNAUTHENTICATED", "1")
    monkeypatch.setattr(
        solve_routes,
        "_project_store",
        lambda user_id=None: SimpleNamespace(get_checkpoint=lambda _sid: None),
    )
    monkeypatch.setattr(
        solve_routes.active_solve_tasks,
        "contains",
        lambda session_id, *, user_id=None: False,
    )
    client = TestClient(web_app.app)
    assert client.get("/api/solve/figures/foreign/fig-1.png").status_code == 404
