"""FastAPI application entry point for ReqBridge."""

import logging
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware

from backend.app.api.sessions import router as sessions_router
from backend.app.core.config import settings

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("reqbridge")


class _SecretsRedactionFilter(logging.Filter):
    """Best-effort redaction of bearer tokens / PATs in log records.

    Catches accidental leaks if any third-party logger (e.g. httpx) ever
    prints request headers. Not a substitute for never logging secrets in
    the first place — but a defence-in-depth net.
    """

    _PATTERNS = (
        "authorization: bearer ",
        "api-key: ",
        "ado_pat=",
    )

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:
            return True
        lower = msg.lower()
        for pat in self._PATTERNS:
            idx = lower.find(pat)
            if idx == -1:
                continue
            head = msg[: idx + len(pat)]
            record.msg = head + "***REDACTED***"
            record.args = ()
            return True
        return True


# Attach the filter to the root logger so every handler benefits.
logging.getLogger().addFilter(_SecretsRedactionFilter())


def _apply_lightweight_migrations(conn) -> None:
    """Additive, idempotent schema patches for the local SQLite database.

    SQLAlchemy's ``create_all`` only creates missing tables; it never alters
    existing ones. For a single-file demo DB we apply a few ``ALTER TABLE ADD
    COLUMN`` statements guarded by a column-existence check so they're safe to
    re-run on every startup.
    """
    from sqlalchemy import inspect, text

    inspector = inspect(conn)
    try:
        existing_tables = set(inspector.get_table_names())
    except Exception:
        return

    # (table, column, column definition) tuples to ensure exist.
    wanted = [
        ("requirements", "assigned_to", "VARCHAR(255)"),
    ]
    for table, column, ddl in wanted:
        if table not in existing_tables:
            continue
        cols = {c["name"] for c in inspector.get_columns(table)}
        if column in cols:
            continue
        conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialise database tables on startup; release resources on shutdown.

    Also boots a background watchdog that periodically marks abandoned
    pipelines as FAILED so the UI doesn't show forever-spinning sessions
    when the worker was killed mid-run.
    """
    import asyncio

    from backend.app.api.sessions import active_connections
    from backend.app.core.database import Base, engine

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # Lightweight, additive migrations for SQLite (create_all won't add
        # columns to tables that already exist). Safe to run on every boot.
        await conn.run_sync(_apply_lightweight_migrations)

    watchdog_task: asyncio.Task | None = None
    if settings.pipeline_stuck_minutes > 0:
        watchdog_task = asyncio.create_task(
            _stuck_pipeline_watchdog(),
            name="stuck-pipeline-watchdog",
        )

    logger.info("ReqBridge startup complete")
    try:
        yield
    finally:
        if watchdog_task is not None:
            watchdog_task.cancel()
            try:
                await watchdog_task
            except (asyncio.CancelledError, Exception):
                pass
        # Cleanly close any remaining WebSocket clients so browsers don't
        # see a dangling "connecting…" state across server restarts.
        for conns in list(active_connections.values()):
            for ws in list(conns):
                try:
                    await ws.close(code=1012, reason="Server shutdown")
                except Exception:
                    pass
        active_connections.clear()
        await engine.dispose()
        logger.info("ReqBridge shutdown complete")


async def _stuck_pipeline_watchdog() -> None:
    """Force-fail any session that's been PROCESSING/PUSHING for too long.

    Runs forever in the background. Sleeps a minute between sweeps so it
    barely costs anything; cancellable via the lifespan shutdown path.
    """
    import asyncio
    from datetime import timedelta

    from sqlalchemy import select

    from backend.app.core.database import async_session_factory
    from backend.app.core.time import utcnow
    from backend.app.models import SessionStatus, UploadSession

    cutoff_minutes = settings.pipeline_stuck_minutes
    interval_seconds = 60

    while True:
        try:
            await asyncio.sleep(interval_seconds)
            cutoff = utcnow() - timedelta(minutes=cutoff_minutes)
            async with async_session_factory() as db:
                rows = (
                    await db.execute(
                        select(UploadSession).where(
                            UploadSession.status.in_(
                                [SessionStatus.PROCESSING, SessionStatus.PUSHING]
                            ),
                            UploadSession.updated_at < cutoff,
                        )
                    )
                ).scalars().all()
                for s in rows:
                    s.status = SessionStatus.FAILED
                    s.error_message = (
                        s.error_message
                        or f"Marked FAILED by watchdog after >{cutoff_minutes} minutes idle"
                    )
                    logger.warning(
                        "Watchdog: session %s stuck since %s, marking FAILED",
                        s.id, s.updated_at,
                    )
                if rows:
                    await db.commit()
        except asyncio.CancelledError:
            raise
        except Exception:
            # Never let the watchdog die. Log and try again next tick.
            logger.exception("Stuck-pipeline watchdog iteration failed")


app = FastAPI(
    title="ReqBridge",
    description="AI-Powered Requirements Intelligence & Agile Work Item Engine",
    version="0.1.0",
    lifespan=lifespan,
)


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Stamp every request with an X-Request-ID for log correlation."""

    async def dispatch(self, request: Request, call_next):
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex
        request.state.request_id = rid
        try:
            response = await call_next(request)
        except Exception:
            logger.exception("Unhandled error [request_id=%s]", rid)
            raise
        response.headers["X-Request-ID"] = rid
        return response


app.add_middleware(RequestIdMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://localhost:5174",
        "http://localhost:3000",
    ],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "X-Request-ID"],
    expose_headers=["X-Request-ID"],
)

app.include_router(sessions_router)


@app.get("/")
async def root() -> dict:
    """Friendly landing payload so curling :8000 doesn't 404."""
    return {
        "name": "ReqBridge",
        "version": "0.1.0",
        "docs": "/docs",
        "health": "/health",
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=settings.app_host, port=settings.app_port)
