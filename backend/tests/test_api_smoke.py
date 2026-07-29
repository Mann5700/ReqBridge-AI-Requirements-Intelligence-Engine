"""Smoke tests for key API endpoints.

These tests run the FastAPI app entirely in-process against an in-memory
SQLite database so they're fast and have zero external dependencies.
"""

from __future__ import annotations

import importlib
import os

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient


# ─── API smoke tests ──────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def client(tmp_path, monkeypatch):
    """Build a FastAPI test client backed by a per-test SQLite file.

    We import the modules lazily and force the database URL via env so the
    real app code (config / engine) is exercised end-to-end. Each test gets
    a fresh DB so ordering doesn't matter.
    """
    db_file = tmp_path / "smoke.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{db_file}")
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path / "uploads"))

    # Force re-import so the patched env is honoured.
    import backend.app.core.config as cfg
    importlib.reload(cfg)
    import backend.app.core.database as db
    importlib.reload(db)
    import backend.app.models  # noqa: F401 — register models against the new Base
    importlib.reload(backend.app.models)
    import backend.app.api.sessions as sessions_mod
    importlib.reload(sessions_mod)
    import backend.app.main as main_mod
    importlib.reload(main_mod)

    # Trigger lifespan create_all manually for AsyncClient.
    async with main_mod.app.router.lifespan_context(main_mod.app):
        transport = ASGITransport(app=main_mod.app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            yield ac


@pytest.mark.asyncio
async def test_health(client):
    res = await client.get("/health")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "ok"


@pytest.mark.asyncio
async def test_create_session_returns_zero_counts(client):
    res = await client.post("/sessions/", json={"name": "t1", "description": None})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == "created"
    # Regression: counts must be present and zero so the UI can render the
    # session card without falling over on undefined.
    assert body.get("document_count") == 0
    assert body.get("requirement_count") == 0
    assert body["pipeline_progress"] == 0


@pytest.mark.asyncio
async def test_request_id_header_round_trip(client):
    """The middleware should echo X-Request-ID back to clients."""
    res = await client.get("/health", headers={"X-Request-ID": "deadbeef"})
    assert res.headers.get("X-Request-ID") == "deadbeef"


@pytest.mark.asyncio
async def test_root_landing(client):
    res = await client.get("/")
    assert res.status_code == 200
    body = res.json()
    assert body["name"] == "ReqBridge"
    assert body["docs"] == "/docs"


@pytest.mark.asyncio
async def test_push_without_approved_items_is_rejected(client):
    """Push must 4xx when there are no approved work items.

    Guards against silently kicking off a no-op background task that would
    leave the session stuck in PUSHING forever.
    """
    res = await client.post("/sessions/", json={"name": "p1"})
    sid = res.json()["id"]
    push_res = await client.post(f"/sessions/{sid}/push", json={})
    assert push_res.status_code in (400, 404, 409, 422)


@pytest.mark.asyncio
async def test_resume_unknown_session_is_404(client):
    res = await client.post("/sessions/does-not-exist/resume")
    assert res.status_code == 404


@pytest.mark.asyncio
async def test_resume_when_not_paused_is_409(client):
    """Resume must refuse to run when the session isn't actually paused."""
    res = await client.post("/sessions/", json={"name": "r1"})
    sid = res.json()["id"]
    resume_res = await client.post(f"/sessions/{sid}/resume")
    assert resume_res.status_code == 409
    assert "awaiting" in resume_res.json()["detail"].lower()


@pytest.mark.asyncio
async def test_models_endpoint_lists_default(client):
    """/models always exposes at least the env-default entry, no secrets leaked."""
    res = await client.get("/models")
    assert res.status_code == 200
    body = res.json()
    assert body["default_id"] == "default"
    ids = [m["id"] for m in body["models"]]
    assert "default" in ids
    # Public payload must never include credentials.
    for m in body["models"]:
        assert "api_key" not in m
        assert "base_url" not in m


@pytest.mark.asyncio
async def test_run_accepts_model_id_body(client):
    """/run must not 4xx when given a model_id; unknown ids fall back silently."""
    res = await client.post("/sessions/", json={"name": "m1"})
    sid = res.json()["id"]
    # No documents uploaded \u2014 expect 400 from the documents check, not a
    # 422 from FastAPI rejecting the body shape.
    run_res = await client.post(
        f"/sessions/{sid}/run",
        json={"model_id": "totally-made-up"},
    )
    assert run_res.status_code == 400
    assert "documents" in run_res.json()["detail"].lower()


