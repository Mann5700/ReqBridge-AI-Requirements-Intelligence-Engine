"""FastAPI routes for sessions, uploads, pipeline, requirements, work items, ADO push and WebSocket."""

import asyncio
import json
import logging
import uuid
from datetime import datetime

from backend.app.core.time import utcnow
from pathlib import Path
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.database import async_session_factory, get_db
from backend.app.graph.traceability import TraceabilityGraph
from backend.app.ingest.parser import DocumentParser
from backend.app.models import (
    ADOPushLog,
    DocumentChunk,
    FeedbackCorrection,
    MoSCoWPriority,
    Requirement,
    RequirementConflict,
    RequirementStatus,
    SessionStatus,
    SourceDocument,
    TraceabilityLink,
    UploadSession,
    WorkItem,
    WorkItemType,
)
from backend.app.schemas import (
    ADOPushLogResponse,
    ADOPushRequest,
    ConflictResponse,
    DocumentUploadResponse,
    HealthResponse,
    PipelineStatusResponse,
    RequirementApproval,
    RequirementAssignee,
    RequirementCorrection,
    RequirementResponse,
    SessionCreate,
    SessionListResponse,
    SessionReportResponse,
    SessionResponse,
    TraceabilityGraphResponse,
    WorkItemResponse,
    WorkItemContextImportRequest,
    WorkItemContextImportResponse,
)

router = APIRouter()
parser = DocumentParser()
logger = logging.getLogger(__name__)

# Hard ceiling on uploads to prevent OOM. Configurable later if needed.
MAX_UPLOAD_BYTES = 100 * 1024 * 1024  # 100 MiB

# WebSocket connection manager
active_connections: dict[str, list[WebSocket]] = {}

# Live background tasks (pipeline runs, ADO pushes). We retain references so
# they aren't garbage-collected mid-flight and so we can audit/cancel them.
_BACKGROUND_TASKS: set[asyncio.Task] = set()


def _spawn_background(coro, *, label: str) -> asyncio.Task:
    """Schedule ``coro`` and ensure any uncaught exception is logged.

    A bare ``asyncio.create_task(coro)`` swallows exceptions until garbage
    collection — which means a session can stay PROCESSING forever if the
    pipeline crashes. This wrapper logs the failure loudly and removes the
    task from the registry once it finishes either way.
    """
    task = asyncio.create_task(coro, name=label)
    _BACKGROUND_TASKS.add(task)

    def _on_done(t: asyncio.Task) -> None:
        _BACKGROUND_TASKS.discard(t)
        if t.cancelled():
            logger.warning("Background task cancelled: %s", label)
            return
        exc = t.exception()
        if exc is not None:
            logger.error("Background task crashed: %s", label, exc_info=exc)

    task.add_done_callback(_on_done)
    return task


# ─── Health ───────────────────────────────────────────────────────────────────

@router.get("/health", response_model=HealthResponse)
async def health_check():
    """Health check endpoint for monitoring and load balancers."""
    return HealthResponse(status="ok", version="0.1.0")


# ─── ADO Connection Test ──────────────────────────────────────────────────────

@router.post("/ado/test-connection")
async def test_ado_connection(payload: ADOPushRequest):
    """Validate a set of ADO credentials before opening a session.

    Posts to the ADO Projects API; a 200 means the org/project/PAT triple
    is good. Anything else is mapped to a friendly message so the UI can
    surface it without exposing raw HTTP errors.

    The request body shape reuses ``ADOPushRequest`` so the frontend can
    hand the same payload to both this endpoint and the eventual push.
    """
    org_url = (payload.ado_org_url or "").rstrip("/")
    project = payload.ado_project or payload.ado_org or ""
    pat = payload.ado_pat or ""
    if not org_url or not project or not pat:
        raise HTTPException(
            status_code=400,
            detail="ado_org_url, ado_project and ado_pat are all required.",
        )

    url = f"{org_url}/_apis/projects/{project}?api-version=7.1"
    try:
        async with httpx.AsyncClient(timeout=10.0, auth=("", pat)) as client:
            res = await client.get(url)
    except httpx.RequestError as e:
        raise HTTPException(
            status_code=502,
            detail=f"Could not reach Azure DevOps: {e.__class__.__name__}",
        )

    if res.status_code == 200:
        data = res.json()
        # Best-effort: also fetch the authenticated user's display name from
        # the ConnectionData endpoint. We don't fail the whole connection if
        # this call hiccups — the project check above already proves the PAT
        # works.
        user_name: str | None = None
        # Try ConnectionData first (works on dev.azure.com without an
        # api-version query string); fall back to the vssps Profile API.
        try:
            async with httpx.AsyncClient(timeout=5.0, auth=("", pat)) as client:
                cd = await client.get(f"{org_url}/_apis/ConnectionData")
                if cd.status_code == 200:
                    au = (cd.json() or {}).get("authenticatedUser") or {}
                    user_name = (
                        au.get("providerDisplayName")
                        or au.get("customDisplayName")
                        or au.get("displayName")
                    )
                if not user_name:
                    pf = await client.get(
                        "https://app.vssps.visualstudio.com/_apis/profile/profiles/me?api-version=7.1"
                    )
                    if pf.status_code == 200:
                        pf_data = pf.json() or {}
                        user_name = (
                            pf_data.get("displayName")
                            or pf_data.get("emailAddress")
                            or pf_data.get("publicAlias")
                        )
        except httpx.RequestError as e:
            logger.warning("User lookup failed: %s", e)
        return {
            "ok": True,
            "project": {
                "id": data.get("id"),
                "name": data.get("name"),
                "description": data.get("description"),
            },
            "user": {"name": user_name} if user_name else None,
            "org_url": org_url,
        }
    if res.status_code in (401, 403):
        raise HTTPException(
            status_code=401,
            detail="PAT was rejected by Azure DevOps. Check the token has Work Items: Read scope.",
        )
    if res.status_code == 404:
        raise HTTPException(
            status_code=404,
            detail=f"Project '{project}' not found at {org_url}. Check the org URL and project name.",
        )
    raise HTTPException(
        status_code=502,
        detail=f"Azure DevOps responded with HTTP {res.status_code}.",
    )


# \u2500\u2500\u2500 Sessions \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500

@router.post("/sessions/", response_model=SessionResponse)
async def create_session(
    payload: SessionCreate,
    db: AsyncSession = Depends(get_db),
):
    """Create a new requirements ingestion session."""
    # Mint a short id, retrying on the (vanishingly rare) collision so the
    # client never sees a 500 from a duplicate primary key.
    from backend.app.models import generate_short_id

    new_id = generate_short_id()
    for _ in range(5):
        if (await db.get(UploadSession, new_id)) is None:
            break
        new_id = generate_short_id()
    else:
        # Fall back to a full UUID if 5 attempts collided \u2014 effectively
        # impossible, but keeps the handler total.
        new_id = str(uuid.uuid4())

    session = UploadSession(
        id=new_id,
        name=payload.name,
        description=payload.description,
        status=SessionStatus.CREATED,
    )
    db.add(session)
    await db.commit()
    await db.refresh(session)
    return SessionResponse(
        id=session.id,
        name=session.name,
        description=session.description,
        status=session.status.value,
        created_at=session.created_at,
        updated_at=session.updated_at,
        pipeline_progress=session.pipeline_progress,
        current_agent=session.current_agent,
        error_message=session.error_message,
        # New sessions have no docs / requirements yet — explicit beats default.
        document_count=0,
        requirement_count=0,
    )


@router.get("/sessions/", response_model=SessionListResponse)
async def list_sessions(db: AsyncSession = Depends(get_db)):
    """List all sessions with status, metadata, and aggregate counts."""
    from sqlalchemy import func

    result = await db.execute(select(UploadSession).order_by(UploadSession.created_at.desc()))
    sessions = result.scalars().all()

    # Aggregate counts in a single round-trip per relation rather than N+1 queries.
    doc_counts_rows = (await db.execute(
        select(SourceDocument.session_id, func.count(SourceDocument.id))
        .group_by(SourceDocument.session_id)
    )).all()
    req_counts_rows = (await db.execute(
        select(Requirement.session_id, func.count(Requirement.id))
        .group_by(Requirement.session_id)
    )).all()
    doc_counts = {sid: n for sid, n in doc_counts_rows}
    req_counts = {sid: n for sid, n in req_counts_rows}

    items = [
        SessionResponse(
            id=s.id,
            name=s.name,
            description=s.description,
            status=s.status.value,
            created_at=s.created_at,
            updated_at=s.updated_at,
            pipeline_progress=s.pipeline_progress,
            current_agent=s.current_agent,
            error_message=s.error_message,
            document_count=doc_counts.get(s.id, 0),
            requirement_count=req_counts.get(s.id, 0),
        )
        for s in sessions
    ]
    return SessionListResponse(sessions=items, total=len(items))


# ─── Document Upload ──────────────────────────────────────────────────────────

@router.post("/sessions/{session_id}/upload", response_model=DocumentUploadResponse)
async def upload_document(
    session_id: str,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    """Upload a document to an existing session for ingestion."""
    # Verify session exists
    session = await db.get(UploadSession, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # Determine file type from extension
    ext = Path(file.filename or "").suffix.lower().lstrip(".")
    type_map = {
        "pdf": "pdf", "docx": "docx", "doc": "docx",
        "xlsx": "xlsx", "xls": "xlsx",
        "png": "image", "jpg": "image", "jpeg": "image", "tiff": "image",
        "txt": "text", "md": "text", "csv": "text",
        "eml": "email",
    }
    file_type = type_map.get(ext, "text")

    # Save file to uploads directory. Strip any path components from the
    # client-supplied filename to prevent path-traversal (e.g. "../../etc").
    upload_dir = Path("uploads") / session_id
    upload_dir.mkdir(parents=True, exist_ok=True)
    safe_name = Path(file.filename or "document").name or "document"
    file_path = upload_dir / (str(uuid.uuid4()) + "_" + safe_name)

    content = await file.read()
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File exceeds {MAX_UPLOAD_BYTES // (1024 * 1024)} MiB limit",
        )
    file_path.write_bytes(content)

    # Create DB record
    doc = SourceDocument(
        id=str(uuid.uuid4()),
        session_id=session_id,
        filename=file.filename or "document",
        file_type=file_type,
        file_path=str(file_path),
        file_size_bytes=len(content),
    )
    db.add(doc)

    session.status = SessionStatus.UPLOADING
    await db.commit()
    await db.refresh(doc)

    return DocumentUploadResponse(
        id=doc.id,
        filename=doc.filename,
        file_type=doc.file_type,
        file_size_bytes=doc.file_size_bytes,
        upload_timestamp=doc.upload_timestamp,
    )


@router.post(
    "/sessions/{session_id}/import-work-items",
    response_model=WorkItemContextImportResponse,
)
async def import_work_items(
    session_id: str,
    payload: WorkItemContextImportRequest,
    db: AsyncSession = Depends(get_db),
):
    """Pull existing ADO work items into the session as pipeline context.

    The work items are fetched read-only, rendered into a plain-text document,
    and stored as a regular ``SourceDocument`` — so the rest of the pipeline
    (parse → extract → ...) treats them exactly like an uploaded file. This is
    an alternative input to document upload: either one satisfies the
    "something to run on" requirement.
    """
    from backend.app.core.config import settings
    from backend.app.services import ado_client

    session = await db.get(UploadSession, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    org_url = (payload.ado_org_url or settings.ado_org_url or "").rstrip("/")
    project = payload.ado_project or settings.ado_project or None
    pat = payload.ado_pat or settings.ado_pat or ""
    if not org_url or not pat:
        raise HTTPException(
            status_code=400,
            detail="ado_org_url and ado_pat are required to import work items.",
        )

    ids = ado_client.parse_work_item_refs(payload.refs or "")
    if not ids:
        raise HTTPException(
            status_code=400,
            detail="No work item ids found. Provide comma-separated ids or ADO work-item URLs.",
        )

    try:
        items = await ado_client.fetch_work_items(org_url, pat, ids, project)
    except httpx.HTTPStatusError as e:
        status = e.response.status_code if e.response is not None else 502
        if status in (401, 403):
            raise HTTPException(
                status_code=401,
                detail="PAT was rejected by Azure DevOps. Check the token has Work Items: Read scope.",
            )
        raise HTTPException(
            status_code=502,
            detail=f"Azure DevOps responded with HTTP {status} while fetching work items.",
        )
    except httpx.RequestError as e:
        raise HTTPException(
            status_code=502,
            detail=f"Could not reach Azure DevOps: {e.__class__.__name__}",
        )

    if not items:
        raise HTTPException(
            status_code=404,
            detail="None of the requested work items could be retrieved. Check the ids and project.",
        )

    text = ado_client.render_work_items_as_text(items)

    upload_dir = Path("uploads") / session_id
    upload_dir.mkdir(parents=True, exist_ok=True)

    # Build a human-readable filename from the first work item's type + title.
    first = items[0]
    slug = (first.get("title") or "imported")[:60].strip()
    # Sanitize for filesystem: keep only alphanums, spaces, dashes.
    slug = "".join(c if c.isalnum() or c in " -" else " " for c in slug).strip()
    wi_type = (first.get("work_item_type") or "Work Item")
    filename = f"{wi_type} #{first['id']} {slug}"

    file_path = upload_dir / (str(uuid.uuid4()) + "_" + filename)
    content = text.encode("utf-8")
    file_path.write_bytes(content)

    doc = SourceDocument(
        id=str(uuid.uuid4()),
        session_id=session_id,
        filename=filename,
        file_type="text",
        file_path=str(file_path),
        file_size_bytes=len(content),
        metadata_={"source": "ado_import", "work_item_ids": [it["id"] for it in items]},
    )
    db.add(doc)
    session.status = SessionStatus.UPLOADING
    await db.commit()
    await db.refresh(doc)

    return WorkItemContextImportResponse(
        document_id=doc.id,
        filename=doc.filename,
        requested=len(ids),
        fetched=len(items),
        work_item_ids=[it["id"] for it in items],
    )


# ─── Pipeline ─────────────────────────────────────────────────────────────────

@router.post("/sessions/{session_id}/run")
async def run_pipeline(
    session_id: str,
    body: dict | None = None,
    db: AsyncSession = Depends(get_db),
):
    """Trigger the full agent pipeline asynchronously.

    Optional JSON body:
      - ``model_id``: ``<registry id>`` selects which LLM every agent uses.
      - ``sprint_start``: ISO ``YYYY-MM-DD`` date used to resolve the target
        ADO sprint for planning. Omitted ⇒ resolved from today's date.
      - ``instructions``: free-text guidance threaded into the extraction and
        planning system prompts for this run (e.g. "focus on security
        requirements", "treat as a brownfield change"). Optional.
    Omitted/unknown values fall back to safe defaults — the run is never
    blocked by a stale picker selection.
    """
    model_id: str | None = None
    sprint_start: str | None = None
    instructions: str | None = None
    if isinstance(body, dict):
        raw = body.get("model_id")
        if isinstance(raw, str) and raw.strip():
            model_id = raw.strip()
        raw_sprint = body.get("sprint_start")
        if isinstance(raw_sprint, str) and raw_sprint.strip():
            sprint_start = raw_sprint.strip()
        raw_instructions = body.get("instructions")
        if isinstance(raw_instructions, str) and raw_instructions.strip():
            # Cap length defensively to keep prompt size bounded.
            instructions = raw_instructions.strip()[:2000]

    session = await db.get(UploadSession, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # Parse all documents into chunks
    result = await db.execute(
        select(SourceDocument).where(SourceDocument.session_id == session_id)
    )
    documents = result.scalars().all()

    if not documents:
        raise HTTPException(status_code=400, detail="No documents uploaded")

    chunks: list[dict] = []
    # Parse documents in PARALLEL via a thread pool. parser.parse is CPU/IO
    # bound (PDF/DOCX), so awaiting them on the event loop with
    # asyncio.to_thread releases the GIL during I/O and runs the rest on
    # worker threads — much faster when the user uploads multiple docs.
    async def _parse_one(doc):
        try:
            doc_chunks = await asyncio.to_thread(
                parser.parse, doc.file_path, doc.file_type,
            )
            for c in doc_chunks:
                c["_document_id"] = doc.id
            return doc, doc_chunks, None
        except Exception as e:
            return doc, [], str(e)

    parse_results = await asyncio.gather(*(_parse_one(d) for d in documents))
    for doc, doc_chunks, err in parse_results:
        if err:
            session.error_message = f"Parse error for {doc.filename}: {err}"
        else:
            chunks.extend(doc_chunks)

    # Store chunks in DB
    for idx, chunk_data in enumerate(chunks):
        chunk = DocumentChunk(
            id=chunk_data["id"],
            document_id=chunk_data["_document_id"],
            chunk_index=idx,
            content=chunk_data["content"],
            heading_hierarchy=chunk_data.get("heading_hierarchy"),
            page_number=chunk_data.get("page_number"),
            section_title=chunk_data.get("section_title"),
            chunk_type=chunk_data.get("chunk_type", "paragraph"),
        )
        db.add(chunk)

    session.status = SessionStatus.PROCESSING
    # Persist the user's free-text instructions onto the session metadata so
    # they survive into the resume pipeline (planning runs LATER, on approve,
    # and needs to see the same guidance the user typed at run time).
    if instructions:
        session.metadata_ = session.metadata_ or {}
        session.metadata_["instructions"] = instructions
    await db.commit()

    # Launch pipeline async (in production, this would be a Celery task).
    # We retain a reference on the global registry so a stray exception in the
    # background task is logged and the session is marked FAILED — otherwise
    # the user would see PROCESSING forever.
    _spawn_background(
        _execute_pipeline(
            session_id, chunks, model_id=model_id,
            sprint_start=sprint_start, instructions=instructions,
        ),
        label=f"pipeline:{session_id}",
    )

    return {
        "session_id": session_id,
        "status": "processing",
        "chunk_count": len(chunks),
        "model_id": model_id,
        "sprint_start": sprint_start,
    }


async def _execute_pipeline(
    session_id: str,
    chunks: list[dict],
    model_id: str | None = None,
    sprint_start: str | None = None,
    instructions: str | None = None,
) -> None:
    """Background pipeline execution: run agents, broadcast progress, persist outputs."""
    from backend.app.agents.orchestrator import run_pipeline_async
    from backend.app.core.config import settings as _settings

    await _broadcast(session_id, {
        "event": "pipeline_started",
        "progress": 0.0,
        "status": "processing",
    })

    try:
        logger.info("Pipeline starting for session %s with %d chunks", session_id, len(chunks))
        final_state = await run_pipeline_async(
            session_id, chunks, model_id=model_id,
            sprint_start=sprint_start, instructions=instructions,
        )
        await _persist_pipeline_outputs(session_id, final_state)

        # If HITL pause is enabled and the run halted there, the orchestrator
        # has already set the session to AWAITING_REVIEW and broadcast the
        # pause event. Don't trample that with a COMPLETED status.
        paused = bool(
            _settings.enable_hitl_pause
            and final_state.get("metadata", {}).get("awaiting_human_review")
        )

        async with async_session_factory() as db:
            session = await db.get(UploadSession, session_id)
            if session and not paused:
                session.status = SessionStatus.COMPLETED
                session.pipeline_progress = 1.0
                session.current_agent = None
                await db.commit()

        if paused:
            logger.info("Pipeline paused for review for session %s", session_id)
        else:
            logger.info("Pipeline complete for session %s", session_id)
            await _broadcast(session_id, {
                "event": "pipeline_complete",
                "progress": 1.0,
                "status": "completed",
            })
    except Exception as e:
        logger.exception("Pipeline failed for session %s", session_id)
        async with async_session_factory() as db:
            session = await db.get(UploadSession, session_id)
            if session:
                session.status = SessionStatus.FAILED
                session.error_message = str(e)
                await db.commit()
        await _broadcast(session_id, {
            "event": "pipeline_failed",
            "progress": 0.0,
            "status": "failed",
            "error": str(e),
        })


@router.post("/sessions/{session_id}/resume")
async def resume_pipeline(
    session_id: str,
    body: dict | None = None,
    db: AsyncSession = Depends(get_db),
):
    """Resume a HITL-paused pipeline from the decomposition step.

    Reads the requirements that have been approved by the user, rebuilds an
    agent state from them, and re-runs ``decomposition → … → feedback`` in
    the background. Returns 409 if the session isn't actually awaiting
    review (so accidental clicks don't restart anything).

    Optional body ``{"model_id": ...}`` lets the user switch the LLM for
    the second half of the run; falls back to env default if omitted.
    """
    model_id: str | None = None
    if isinstance(body, dict):
        raw = body.get("model_id")
        if isinstance(raw, str) and raw.strip():
            model_id = raw.strip()

    session = await db.get(UploadSession, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.status != SessionStatus.AWAITING_REVIEW:
        raise HTTPException(
            status_code=409,
            detail=f"Session is {session.status.value}, not awaiting review",
        )

    # Pull every approved requirement back into the agent state shape.
    rows = (
        await db.execute(
            select(Requirement).where(
                Requirement.session_id == session_id,
                Requirement.status == RequirementStatus.APPROVED,
            )
        )
    ).scalars().all()
    if not rows:
        raise HTTPException(
            status_code=400,
            detail="No approved requirements to decompose. Approve at least one first.",
        )

    requirements_state = [
        {
            "id": r.id,
            "statement": r.statement,
            "category": r.category,
            "moscow_priority": r.moscow_priority.value if r.moscow_priority else None,
            "confidence": r.confidence_score,
            "status": r.status.value,
            "assumptions": r.assumptions,
            "constraints": r.constraints,
            "original_text": r.original_text,
        }
        for r in rows
    ]

    session.status = SessionStatus.PROCESSING
    session.current_agent = "decomposition"
    await db.commit()

    saved_instructions = (session.metadata_ or {}).get("instructions")
    _spawn_background(
        _execute_resume(
            session_id, requirements_state,
            model_id=model_id, instructions=saved_instructions,
        ),
        label=f"resume:{session_id}",
    )
    return {
        "session_id": session_id,
        "status": "processing",
        "approved_count": len(requirements_state),
        "model_id": model_id,
    }


async def _execute_resume(
    session_id: str,
    requirements: list[dict],
    model_id: str | None = None,
    instructions: str | None = None,
) -> None:
    """Background continuation of a paused pipeline."""
    from backend.app.agents.orchestrator import resume_pipeline_async

    await _broadcast(session_id, {
        "event": "pipeline_resumed",
        "progress": 0.5,
        "status": "processing",
    })

    try:
        state = {
            "session_id": session_id,
            "requirements": requirements,
            "conflicts": [],
            "work_items": [],
            "traceability_links": [],
            "chunks": [],
            "current_agent": "decomposition",
            "progress": 0.5,
            "metadata": {
                "resumed_from_pause": True,
                **({"model_id": model_id} if model_id else {}),
                **({"instructions": instructions} if instructions else {}),
            },
        }
        final_state = await resume_pipeline_async(state)
        await _persist_pipeline_outputs(session_id, final_state)

        async with async_session_factory() as db:
            session = await db.get(UploadSession, session_id)
            if session:
                session.status = SessionStatus.COMPLETED
                session.pipeline_progress = 1.0
                session.current_agent = None
                await db.commit()

        await _broadcast(session_id, {
            "event": "pipeline_complete",
            "progress": 1.0,
            "status": "completed",
        })
    except Exception as e:
        logger.exception("Resume failed for session %s", session_id)
        async with async_session_factory() as db:
            session = await db.get(UploadSession, session_id)
            if session:
                session.status = SessionStatus.FAILED
                session.error_message = f"Resume failed: {e}"
                await db.commit()
        await _broadcast(session_id, {
            "event": "pipeline_failed",
            "progress": 0.5,
            "status": "failed",
            "error": str(e),
        })


async def _persist_pipeline_outputs(session_id: str, final_state: dict) -> None:
    """Write requirements, conflicts, work items, and traceability links from
    the in-memory pipeline state to the database. Idempotent: skips rows that
    already exist (by primary key).
    """
    requirements = final_state.get("requirements", []) or []
    conflicts = final_state.get("conflicts", []) or []
    work_items = final_state.get("work_items", []) or []
    links = final_state.get("traceability_links", []) or []

    # Map raw priority strings ("must"/"should"/...) to enum values, ignore unknowns.
    moscow_lookup = {p.value: p for p in MoSCoWPriority}
    wi_type_lookup = {t.value: t for t in WorkItemType}
    req_status_lookup = {s.value: s for s in RequirementStatus}

    async with async_session_factory() as db:
        # Requirements
        for req in requirements:
            req_id = req.get("id")
            if not req_id:
                continue
            if await db.get(Requirement, req_id):
                continue
            db.add(Requirement(
                id=req_id,
                session_id=session_id,
                source_chunk_id=req.get("source_chunk_id"),
                statement=req.get("statement", ""),
                category=req.get("category"),
                moscow_priority=moscow_lookup.get(str(req.get("moscow_priority") or "").lower()),
                confidence_score=float(req.get("confidence", 0.0) or 0.0),
                status=req_status_lookup.get(str(req.get("status") or "").lower(), RequirementStatus.EXTRACTED),
                assumptions=req.get("assumptions"),
                constraints=req.get("constraints"),
                business_value_score=req.get("business_value_score"),
                original_text=req.get("original_text") or req.get("statement"),
            ))
        # Flush so the requirement rows are queryable below when we validate
        # the requirement→work-item link.
        await db.flush()

        # Real requirement IDs for this session. The planning LLM is asked to
        # echo the exact requirement `id` in source_requirement_ids, but it can
        # hallucinate values like "REQ-001". We only keep a link when it points
        # at a genuine requirement so the Requirements page and ADO push can
        # trace work items back to their source.
        valid_req_ids: set[str] = {r.get("id") for r in requirements if r.get("id")}
        existing_req_ids = (
            await db.execute(
                select(Requirement.id).where(Requirement.session_id == session_id)
            )
        ).scalars().all()
        valid_req_ids.update(existing_req_ids)

        # Conflicts
        for c in conflicts:
            db.add(RequirementConflict(
                session_id=session_id,
                requirement_a_id=c.get("requirement_a_id", ""),
                requirement_b_id=c.get("requirement_b_id", ""),
                conflict_type=c.get("conflict_type", "unknown"),
                explanation=c.get("explanation", ""),
                severity=c.get("severity", "medium"),
                confidence_score=float(c.get("confidence_score", 0.0) or 0.0),
                resolution=c.get("suggested_resolution"),
                resolved=False,
            ))

        # Work items — build local-id → DB-id map for parent linkage and traceability.
        # IMPORTANT: _local_id values like "WI-0001" are sequential PER-SESSION
        # and collide across sessions. Always mint a fresh UUID for the DB PK;
        # use _local_id only to resolve parent references within the batch.
        local_to_db: dict[str, str] = {}
        for wi in work_items:
            local_id = wi.get("_local_id") or wi.get("id") or str(uuid.uuid4())
            db_id = str(uuid.uuid4())
            local_to_db[local_id] = db_id
            wi_type = wi_type_lookup.get(str(wi.get("work_item_type") or ""), WorkItemType.TASK)
            src_ids = wi.get("source_requirement_ids") or []
            linked_req_id = next((rid for rid in src_ids if rid in valid_req_ids), None)
            db.add(WorkItem(
                id=db_id,
                session_id=session_id,
                requirement_id=linked_req_id,
                parent_id=None,  # resolved in second pass
                work_item_type=wi_type,
                title=wi.get("title", ""),
                description=wi.get("description"),
                acceptance_criteria=wi.get("acceptance_criteria"),
                priority=int(wi.get("priority", 2) or 2),
                story_points=wi.get("story_points"),
                tags=wi.get("tags"),
                metadata_={
                    k: v for k, v in {
                        "effort_hours": wi.get("effort_hours"),
                        "tshirt_size": wi.get("tshirt_size"),
                    }.items() if v is not None
                } or None,
            ))
        await db.flush()

        # Resolve parent links now that all rows exist. Wrapped in a try/except
        # because a malformed parent_ref shouldn't lose the work-item rows we
        # just created — better to commit what we have and log the issue.
        try:
            for wi in work_items:
                wi_id = local_to_db.get(wi.get("_local_id") or wi.get("id") or "")
                parent_local = wi.get("parent_id") or wi.get("parent_ref")
                if wi_id and parent_local and parent_local in local_to_db:
                    row = await db.get(WorkItem, wi_id)
                    if row is not None:
                        row.parent_id = local_to_db[parent_local]
        except Exception:
            logger.exception(
                "Parent-id resolution failed for session %s; work items already saved without hierarchy",
                session_id,
            )

        # Traceability links
        for link in links:
            db.add(TraceabilityLink(
                session_id=session_id,
                requirement_id=link.get("source_node_id") if link.get("source_node_type") == "requirement" else None,
                source_node_type=link.get("source_node_type", ""),
                source_node_id=link.get("source_node_id", ""),
                target_node_type=link.get("target_node_type", ""),
                target_node_id=link.get("target_node_id", ""),
                link_type=link.get("link_type", "derived_from"),
                confidence=float(link.get("confidence", 1.0) or 1.0),
            ))

        await db.commit()


@router.get("/sessions/{session_id}/status", response_model=PipelineStatusResponse)
async def get_pipeline_status(
    session_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Get current pipeline status and agent progress."""
    session = await db.get(UploadSession, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    meta = session.metadata_ or {}
    return PipelineStatusResponse(
        session_id=session.id,
        status=session.status.value,
        progress=session.pipeline_progress,
        current_agent=session.current_agent,
        error_message=session.error_message,
        approved_for_push=bool(meta.get("approved_for_push")),
    )


# ─── Requirements ─────────────────────────────────────────────────────────────

@router.get("/sessions/{session_id}/requirements", response_model=list[RequirementResponse])
async def get_requirements(
    session_id: str,
    filter_confidence_below: Optional[float] = None,
    db: AsyncSession = Depends(get_db),
):
    """Get all extracted requirements for a session, optionally filtered by confidence."""
    query = select(Requirement).where(Requirement.session_id == session_id)
    if filter_confidence_below is not None:
        query = query.where(Requirement.confidence_score >= filter_confidence_below)

    result = await db.execute(query)
    requirements = result.scalars().all()
    return [RequirementResponse.model_validate(r) for r in requirements]


@router.put("/sessions/{session_id}/requirements/{requirement_id}")
async def correct_requirement(
    session_id: str,
    requirement_id: str,
    payload: RequirementCorrection,
    db: AsyncSession = Depends(get_db),
):
    """Submit a human correction to a requirement (triggers feedback agent)."""
    req = await db.get(Requirement, requirement_id)
    if not req or req.session_id != session_id:
        raise HTTPException(status_code=404, detail="Requirement not found")

    from backend.app.models import RequirementStatus as _ReqStatus

    original_text = req.statement
    req.statement = payload.corrected_text
    req.status = _ReqStatus.APPROVED
    await db.commit()

    return {"id": requirement_id, "corrected": True, "original": original_text}


@router.put("/sessions/{session_id}/requirements/{requirement_id}/assignee")
async def set_requirement_assignee(
    session_id: str,
    requirement_id: str,
    payload: RequirementAssignee,
    db: AsyncSession = Depends(get_db),
):
    """Set (or clear) the person a requirement is assigned to.

    The value is persisted on the requirement and flows through to the
    System.AssignedTo field of every work item generated from it when the
    session is pushed to Azure DevOps. Pass an empty/blank value to clear.
    """
    req = await db.get(Requirement, requirement_id)
    if not req or req.session_id != session_id:
        raise HTTPException(status_code=404, detail="Requirement not found")

    assignee = (payload.assigned_to or "").strip() or None
    req.assigned_to = assignee
    await db.commit()

    return {"id": requirement_id, "assigned_to": assignee}


@router.post("/sessions/{session_id}/requirements/approve")
async def approve_requirements(
    session_id: str,
    payload: RequirementApproval,
    db: AsyncSession = Depends(get_db),
):
    """Approve a batch of requirements for work item generation."""
    approved_count = 0
    for req_id in payload.requirement_ids:
        req = await db.get(Requirement, req_id)
        if req and req.session_id == session_id:
            req.status = RequirementStatus.APPROVED
            req.approved_at = utcnow()
            approved_count += 1

    await db.commit()
    return {"approved_count": approved_count}


@router.post("/sessions/{session_id}/requirements/unapprove")
async def unapprove_requirements(
    session_id: str,
    payload: RequirementApproval,
    db: AsyncSession = Depends(get_db),
):
    """Drop a batch of requirements from the push (mark them rejected).

    Used by the interactive report's "drop" control: unticking a requirement
    excludes it from the ADO push. The rejected status persists, so reopening
    the report keeps it unticked instead of silently re-including it. The
    requirement stays editable in Requirements Review.
    """
    unapproved_count = 0
    for req_id in payload.requirement_ids:
        req = await db.get(Requirement, req_id)
        if req and req.session_id == session_id:
            req.status = RequirementStatus.REJECTED
            req.approved_at = None
            unapproved_count += 1

    await db.commit()
    return {"unapproved_count": unapproved_count}


# ─── Conflicts ────────────────────────────────────────────────────────────────

@router.get("/sessions/{session_id}/conflicts", response_model=list[ConflictResponse])
async def get_conflicts(
    session_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Get all detected requirement conflicts for a session."""
    result = await db.execute(
        select(RequirementConflict).where(RequirementConflict.session_id == session_id)
    )
    conflicts = result.scalars().all()
    return [ConflictResponse.model_validate(c) for c in conflicts]


# ─── Work Items ───────────────────────────────────────────────────────────────

@router.get("/sessions/{session_id}/workitems", response_model=list[WorkItemResponse])
async def get_work_items(
    session_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Get generated work items in hierarchical structure."""
    # Load all work items flat, then assemble the parent→child tree in Python.
    # The ORM `children` relationship is a lazy backref, so touching it during
    # serialization would raise MissingGreenlet outside the async session.
    result = await db.execute(
        select(WorkItem).where(WorkItem.session_id == session_id)
    )
    rows = result.scalars().all()

    children_by_parent: dict[str | None, list[WorkItem]] = {}
    for wi in rows:
        children_by_parent.setdefault(wi.parent_id, []).append(wi)

    def build(wi: WorkItem) -> WorkItemResponse:
        meta = wi.metadata_ or {}
        return WorkItemResponse(
            id=wi.id,
            session_id=wi.session_id,
            requirement_id=wi.requirement_id,
            parent_id=wi.parent_id,
            work_item_type=str(getattr(wi.work_item_type, "value", wi.work_item_type)),
            title=wi.title,
            description=wi.description,
            acceptance_criteria=wi.acceptance_criteria,
            priority=wi.priority,
            story_points=wi.story_points,
            effort_hours=meta.get("effort_hours"),
            tshirt_size=meta.get("tshirt_size"),
            tags=wi.tags,
            ado_work_item_id=wi.ado_work_item_id,
            ado_url=wi.ado_url,
            pushed_to_ado=wi.pushed_to_ado,
            children=[build(c) for c in children_by_parent.get(wi.id, [])],
        )

    # Return only roots at the top level; descendants are nested under `children`.
    roots = children_by_parent.get(None, [])
    return [build(r) for r in roots]


# ─── ADO Push ─────────────────────────────────────────────────────────────────

@router.post("/sessions/{session_id}/push")
async def push_to_ado(
    session_id: str,
    payload: ADOPushRequest,
    db: AsyncSession = Depends(get_db),
):
    """Push approved work items to Azure DevOps via the IntegrationAgent."""
    session = await db.get(UploadSession, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # Human approval gate: push is blocked until explicitly approved.
    session_meta = session.metadata_ or {}
    if not session_meta.get("approved_for_push"):
        raise HTTPException(
            status_code=403,
            detail="Push not authorized. Call /approve-for-push after reviewing the report.",
        )

    # Load approved work items so the agent has something to push.
    wi_result = await db.execute(
        select(WorkItem).where(WorkItem.session_id == session_id)
    )
    wi_rows = wi_result.scalars().all()

    # Map requirement → assignee so each work item inherits the person its
    # source requirement was assigned to (System.AssignedTo in Azure DevOps).
    assignee_rows = (
        await db.execute(
            select(Requirement.id, Requirement.assigned_to).where(
                Requirement.session_id == session_id,
                Requirement.assigned_to.is_not(None),
            )
        )
    ).all()
    assignee_by_req = {rid: name for rid, name in assignee_rows if name}

    work_items = [
        {
            "_local_id": wi.id,
            "work_item_type": wi.work_item_type.value if hasattr(wi.work_item_type, "value") else wi.work_item_type,
            "title": wi.title,
            "description": wi.description,
            "parent_local_id": wi.parent_id,
            "assigned_to": assignee_by_req.get(wi.requirement_id),
        }
        for wi in wi_rows
    ]
    if not work_items:
        raise HTTPException(status_code=400, detail="No work items to push")

    session.status = SessionStatus.PUSHING
    await db.commit()

    _spawn_background(
        _execute_ado_push(
            session_id,
            work_items,
            ado_org_url=payload.ado_org_url or None,
            ado_pat=payload.ado_pat or None,
            ado_project=payload.ado_project or payload.ado_org or None,
        ),
        label=f"push:{session_id}",
    )
    return {
        "session_id": session_id,
        "status": "pushing",
        "work_item_count": len(work_items),
    }


async def _execute_ado_push(
    session_id: str,
    work_items: list[dict],
    *,
    ado_org_url: Optional[str] = None,
    ado_pat: Optional[str] = None,
    ado_project: Optional[str] = None,
) -> None:
    """Background task: invoke IntegrationAgent and persist final session status."""
    from backend.app.agents.base_agent import AgentState
    from backend.app.agents.integration_agent import IntegrationAgent

    agent = IntegrationAgent(
        ado_org_url=ado_org_url,
        ado_pat=ado_pat,
        ado_project=ado_project,
    )
    state = AgentState(session_id=session_id, work_items=work_items)
    try:
        await agent.execute(state)
        from backend.app.core.config import settings

        org = (ado_org_url or settings.ado_org_url or "").rstrip("/")
        proj = ado_project or settings.ado_project or ""
        async with async_session_factory() as db:
            # Persist the ADO ids the agent assigned back onto the WorkItem rows
            # so the read-back endpoint, traceability, and idempotent re-push all
            # reflect the push (the agent only writes the ADOPushLog audit trail
            # and the in-memory state, not the WorkItem table).
            for item in state.work_items:
                ado_id = item.get("ado_work_item_id")
                local_id = item.get("_local_id")
                if not (ado_id and local_id):
                    continue
                wi = await db.get(WorkItem, local_id)
                if wi is None:
                    continue
                wi.ado_work_item_id = ado_id
                wi.pushed_to_ado = True
                wi.pushed_at = utcnow()
                if org and proj:
                    wi.ado_url = f"{org}/{proj}/_workitems/edit/{ado_id}"
            session = await db.get(UploadSession, session_id)
            if session:
                session.status = SessionStatus.COMPLETED
            await db.commit()
    except Exception as e:
        async with async_session_factory() as db:
            session = await db.get(UploadSession, session_id)
            if session:
                session.status = SessionStatus.FAILED
                session.error_message = f"ADO push failed: {e}"
                await db.commit()


@router.get("/sessions/{session_id}/push-log", response_model=list[ADOPushLogResponse])
async def get_push_log(
    session_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Return audit log of all ADO push attempts for a session."""
    result = await db.execute(
        select(ADOPushLog).where(ADOPushLog.session_id == session_id).order_by(ADOPushLog.timestamp.desc())
    )
    return [ADOPushLogResponse.model_validate(r) for r in result.scalars().all()]


# ─── Traceability Graph ───────────────────────────────────────────────────────

@router.get("/sessions/{session_id}/graph", response_model=TraceabilityGraphResponse)
async def get_traceability_graph(
    session_id: str,
    requirement_id: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    """Get the traceability graph as JSON nodes and edges for D3.js visualization."""
    result = await db.execute(
        select(TraceabilityLink).where(TraceabilityLink.session_id == session_id)
    )
    links = result.scalars().all()

    graph = TraceabilityGraph()
    link_dicts = [
        {
            "source_node_type": link.source_node_type,
            "source_node_id": link.source_node_id,
            "target_node_type": link.target_node_type,
            "target_node_id": link.target_node_id,
            "link_type": link.link_type,
            "confidence": link.confidence,
        }
        for link in links
    ]
    graph.build_from_links(link_dicts)

    if requirement_id:
        graph = graph.get_subgraph_for_requirement(requirement_id)

    graph_json = graph.to_json()
    return TraceabilityGraphResponse(**graph_json)


# ─── Report ───────────────────────────────────────────────────────────────────

@router.get("/sessions/{session_id}/report", response_model=SessionReportResponse)
async def get_session_report(
    session_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Generate a markdown summary report of the entire session."""
    session = await db.get(UploadSession, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # Build markdown report
    reqs_result = await db.execute(
        select(Requirement).where(Requirement.session_id == session_id)
    )
    requirements = reqs_result.scalars().all()

    wi_result = await db.execute(
        select(WorkItem).where(WorkItem.session_id == session_id)
    )
    work_items = wi_result.scalars().all()

    markdown = f"""# ReqBridge Session Report
## Session: {session.name}
**Status:** {session.status.value}
**Created:** {session.created_at}
**Progress:** {session.pipeline_progress * 100:.0f}%

## Requirements Extracted: {len(requirements)}
| ID | Statement | Category | Confidence |
|----|-----------|----------|------------|
"""
    for req in requirements:
        markdown += f"| {req.id[:8]} | {req.statement[:60]} | {req.category} | {req.confidence_score:.2f} |\n"

    markdown += f"\n## Work Items Generated: {len(work_items)}\n"
    for wi in work_items:
        markdown += f"- [{wi.work_item_type}] {wi.title}\n"

    return SessionReportResponse(
        session_id=session_id,
        markdown=markdown,
        generated_at=utcnow(),
    )


# ─── ROM Scores ───────────────────────────────────────────────────────────────

@router.get("/sprints")
async def list_sprints():
    """Return the configured sprint timeline so the UI can offer a picker.

    Sourced from rom_config.yaml (``sprint_timeline``). Marks the sprint that
    contains today's date as ``is_current`` so the UI can preselect it.
    """
    from datetime import date, datetime

    from backend.app.scoring.rom_engine import load_config

    config = load_config()
    timeline = config.get("sprint_timeline", [])
    today = date.today()

    sprints = []
    for entry in timeline:
        start_raw = entry.get("start")
        end_raw = entry.get("end")
        try:
            start = (
                datetime.strptime(start_raw, "%Y-%m-%d").date()
                if isinstance(start_raw, str) else start_raw
            )
            end = (
                datetime.strptime(end_raw, "%Y-%m-%d").date()
                if isinstance(end_raw, str) else end_raw
            )
            is_current = bool(start and end and start <= today <= end)
        except (ValueError, TypeError):
            is_current = False

        sprints.append({
            "sprint": entry.get("sprint"),
            "start": str(start_raw),
            "end": str(end_raw),
            "iteration_path": entry.get("iteration_path"),
            "note": entry.get("note"),
            "is_current": is_current,
        })

    return {"sprints": sprints}


@router.get("/sessions/{session_id}/rom-scores")
async def get_rom_scores(
    session_id: str,
    sprint_start: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    """Return deterministic ROM scoring for a session's requirements.

    Runs the same ROM engine the PlanningAgent uses, but standalone — useful
    for previewing the ROM band/score and target sprint before (or
    independently of) running the full pipeline.

    Optional ``sprint_start`` query param (ISO ``YYYY-MM-DD``) overrides the
    date used to resolve the target ADO sprint.
    """
    session = await db.get(UploadSession, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    reqs_result = await db.execute(
        select(Requirement).where(Requirement.session_id == session_id)
    )
    requirements = reqs_result.scalars().all()

    req_dicts = [
        {
            "statement": r.statement,
            "category": r.category,
            "confidence": r.confidence_score,
        }
        for r in requirements
    ]

    from backend.app.agents.planning_agent import PlanningAgent

    agent = PlanningAgent()
    rom_score, rom_band, slices, sprint = agent._score_requirements(
        req_dicts, sprint_start=sprint_start
    )

    return {
        "session_id": session_id,
        "requirement_count": len(req_dicts),
        "rom_score": rom_score,
        "rom_band": rom_band,
        "slices": slices,
        "sprint": sprint,
        "generated_at": utcnow(),
    }


# ─── HTML Report ──────────────────────────────────────────────────────────────

@router.get("/sessions/{session_id}/report/html")
async def get_session_report_html(
    session_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Generate a rich HTML review report suitable for human approval.

    Returns HTML directly (Content-Type: text/html) for browser rendering.
    This is the approval checkpoint document — stakeholders review this
    before authorizing an ADO push.
    """
    from fastapi.responses import HTMLResponse
    from backend.app.scoring.session_report import generate_session_report

    session = await db.get(UploadSession, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    reqs_result = await db.execute(
        select(Requirement).where(Requirement.session_id == session_id)
    )
    requirements = [
        {
            "id": r.id,
            "statement": r.statement,
            "category": r.category,
            "moscow_priority": r.moscow_priority.value if r.moscow_priority else None,
            "confidence_score": r.confidence_score,
            "status": r.status.value if r.status else "draft",
            "assigned_to": r.assigned_to,
            "assumptions": r.assumptions,
            "constraints": r.constraints,
            "original_text": r.original_text,
        }
        for r in reqs_result.scalars().all()
    ]

    wi_result = await db.execute(
        select(WorkItem).where(WorkItem.session_id == session_id)
    )
    work_items = [
        {
            "id": wi.id,
            "requirement_id": wi.requirement_id,
            "parent_id": wi.parent_id,
            "work_item_type": wi.work_item_type.value if wi.work_item_type else "Unknown",
            "title": wi.title,
            "description": wi.description,
            "story_points": wi.story_points,
        }
        for wi in wi_result.scalars().all()
    ]

    conflicts_result = await db.execute(
        select(RequirementConflict).where(RequirementConflict.session_id == session_id)
    )
    conflicts = [
        {
            "requirement_a_id": c.requirement_a_id,
            "requirement_b_id": c.requirement_b_id,
            "description": c.explanation,
            "severity": c.severity,
        }
        for c in conflicts_result.scalars().all()
    ]

    # Gather uploaded document names for context + their metadata so the
    # report can distinguish uploads from ADO imports.
    docs_result = await db.execute(
        select(SourceDocument.filename, SourceDocument.metadata_).where(
            SourceDocument.session_id == session_id
        )
    )
    docs_rows = list(docs_result.all())
    doc_names = [r[0] for r in docs_rows if r[0]]
    doc_metadatas = [r[1] for r in docs_rows if r[1]]

    # Carry the session metadata too (rom_band, rom_score, rom_slices, sprint)
    # so the report can populate the Impact / ROM Estimate / Sprint
    # Alignment sections without recomputing anything.
    session_meta = dict(session.metadata_ or {})

    html = generate_session_report(
        session_id=session_id,
        requirements=requirements,
        work_items=work_items,
        conflicts=conflicts,
        metadata={
            "uploaded_documents": doc_names,
            "document_metadatas": doc_metadatas,
            "session_metadata": session_meta,
        },
    )

    return HTMLResponse(content=html)


# ─── Human Approval Gate ──────────────────────────────────────────────────────

@router.post("/sessions/{session_id}/approve-for-push")
async def approve_session_for_push(
    session_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Explicit human approval gate before ADO push.

    This endpoint must be called AFTER reviewing the HTML report.
    It marks the session as approved-for-push, which is a prerequisite
    for the /push endpoint to execute. Without approval, push is blocked.

    Returns 400 if no approved requirements exist (nothing to push).
    """
    session = await db.get(UploadSession, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # Verify at least some requirements are approved
    reqs_result = await db.execute(
        select(Requirement).where(
            Requirement.session_id == session_id,
            Requirement.status == RequirementStatus.APPROVED,
        )
    )
    approved_reqs = reqs_result.scalars().all()
    if not approved_reqs:
        raise HTTPException(
            status_code=400,
            detail="No approved requirements. Approve requirements before authorizing push.",
        )

    # Mark session as approved for push
    session.metadata_ = session.metadata_ or {}
    session.metadata_["approved_for_push"] = True
    session.metadata_["approved_at"] = utcnow().isoformat()
    await db.commit()

    # Generate the ADO work item hierarchy now (if it hasn't been produced
    # yet). The pipeline halts at the HITL gate before planning, so the work
    # items only exist once a human has approved. Kicking planning off here —
    # right when the reviewer authorizes the push — means the "work items by
    # type" counts populate on confirmation. Runs in the background; the
    # frontend polls /workitems until they appear.
    existing_wi = (
        await db.execute(
            select(func.count())
            .select_from(WorkItem)
            .where(WorkItem.session_id == session_id)
        )
    ).scalar() or 0

    generating = False
    if existing_wi == 0:
        requirements_state = [
            {
                "id": r.id,
                "statement": r.statement,
                "category": r.category,
                "moscow_priority": r.moscow_priority.value if r.moscow_priority else None,
                "confidence": r.confidence_score,
                "status": r.status.value,
                "assumptions": r.assumptions,
                "constraints": r.constraints,
                "original_text": r.original_text,
            }
            for r in approved_reqs
        ]
        _spawn_background(
            _execute_resume(
                session_id, requirements_state,
                instructions=(session.metadata_ or {}).get("instructions"),
            ),
            label=f"plan-on-approve:{session_id}",
        )
        generating = True

    return {
        "session_id": session_id,
        "approved": True,
        "approved_requirements": len(approved_reqs),
        "generating_work_items": generating,
        "message": "Session approved for ADO push. You may now trigger the push.",
    }


@router.post("/sessions/{session_id}/revoke-push-approval")
async def revoke_push_approval(
    session_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Revoke the push approval for a session.

    Clears the approved_for_push flag so the user must re-approve
    before any push can proceed.
    """
    session = await db.get(UploadSession, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    session.metadata_ = session.metadata_ or {}
    session.metadata_["approved_for_push"] = False
    session.metadata_.pop("approved_at", None)
    await db.commit()

    return {"session_id": session_id, "approved": False, "message": "Push approval revoked."}


# ─── WebSocket ────────────────────────────────────────────────────────────────

@router.websocket("/ws/{session_id}")
async def websocket_endpoint(websocket: WebSocket, session_id: str):
    """Real-time agent progress streaming via WebSocket.

    Tolerates clients that connect before a session is created — the
    backend just registers the socket and waits for events. Cleans up
    aggressively on disconnect to avoid leaking the (session_id → list)
    map for sessions that come and go.
    """
    await websocket.accept()

    active_connections.setdefault(session_id, []).append(websocket)

    try:
        while True:
            data = await websocket.receive_text()
            # Client can send control messages (e.g., resume after HITL).
            try:
                msg = json.loads(data)
            except json.JSONDecodeError:
                continue
            if msg.get("action") == "resume":
                await _broadcast(session_id, {"event": "pipeline_resumed"})
    except WebSocketDisconnect:
        pass
    except Exception:
        # Any other exception (closed transport, frame error, …) — log and
        # drop. We never want the WS handler to crash the worker.
        logger.exception("WebSocket handler error for session %s", session_id)
    finally:
        conns = active_connections.get(session_id)
        if conns is not None:
            try:
                conns.remove(websocket)
            except ValueError:
                pass
            if not conns:
                # Drop the empty bucket so active_connections doesn't grow
                # unbounded for short-lived sessions.
                active_connections.pop(session_id, None)


async def _broadcast(session_id: str, message: dict) -> None:
    """Broadcast a message to all WebSocket clients for a session.

    Iterates a *copy* so a disconnect-driven mutation of the list during
    send doesn't skip subscribers or raise. Sockets that fail to receive
    are evicted so we stop trying to write to them.
    """
    connections = list(active_connections.get(session_id, []))
    if not connections:
        return
    dead: list[WebSocket] = []
    for ws in connections:
        try:
            await ws.send_json(message)
        except Exception:
            dead.append(ws)
    if dead:
        live = active_connections.get(session_id)
        if live is not None:
            for ws in dead:
                try:
                    live.remove(ws)
                except ValueError:
                    pass
            if not live:
                active_connections.pop(session_id, None)
