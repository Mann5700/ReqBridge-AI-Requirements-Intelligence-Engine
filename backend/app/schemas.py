"""Pydantic v2 schemas for API request/response validation and serialization."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field, model_validator


# ─── Session Schemas ──────────────────────────────────────────────────────────

class SessionCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = None


class SessionResponse(BaseModel):
    id: str
    name: str
    description: Optional[str]
    status: str
    created_at: datetime
    updated_at: datetime
    pipeline_progress: float
    current_agent: Optional[str]
    error_message: Optional[str]
    document_count: int = 0
    requirement_count: int = 0

    model_config = {"from_attributes": True}


class SessionListResponse(BaseModel):
    sessions: list[SessionResponse]
    total: int


# ─── Document Schemas ─────────────────────────────────────────────────────────

class DocumentUploadResponse(BaseModel):
    id: str
    filename: str
    file_type: str
    file_size_bytes: Optional[int]
    upload_timestamp: datetime

    model_config = {"from_attributes": True}


# ─── Requirement Schemas ──────────────────────────────────────────────────────

class RequirementResponse(BaseModel):
    id: str
    session_id: str
    source_chunk_id: Optional[str]
    statement: str
    category: Optional[str]
    moscow_priority: Optional[str]
    confidence_score: float
    status: str
    assumptions: Optional[list[str]]
    constraints: Optional[list[str]]
    business_value_score: Optional[float]
    original_text: Optional[str]
    created_at: datetime
    approved_at: Optional[datetime] = None
    approved_by: Optional[str] = None
    assigned_to: Optional[str] = None

    model_config = {"from_attributes": True}


class RequirementCorrection(BaseModel):
    corrected_text: str = Field(..., min_length=1)


class RequirementAssignee(BaseModel):
    assigned_to: Optional[str] = Field(None, max_length=255)


class RequirementApproval(BaseModel):
    requirement_ids: list[str] = Field(..., min_length=1)


# ─── Conflict Schemas ─────────────────────────────────────────────────────────

class ConflictResponse(BaseModel):
    id: str
    requirement_a_id: str
    requirement_b_id: str
    conflict_type: str
    explanation: str
    severity: str
    confidence_score: float
    resolution: Optional[str]
    resolved: bool
    created_at: datetime

    model_config = {"from_attributes": True}


# ─── Work Item Schemas ────────────────────────────────────────────────────────

class WorkItemResponse(BaseModel):
    id: str
    session_id: str
    requirement_id: Optional[str]
    parent_id: Optional[str]
    work_item_type: str
    title: str
    description: Optional[str]
    acceptance_criteria: Optional[list[str]]
    priority: int
    story_points: Optional[int]
    effort_hours: Optional[float] = None
    tshirt_size: Optional[str] = None
    tags: Optional[list[str]]
    ado_work_item_id: Optional[int]
    ado_url: Optional[str]
    pushed_to_ado: bool
    children: list[WorkItemResponse] = []

    model_config = {"from_attributes": True}

    @model_validator(mode="before")
    @classmethod
    def _coerce_acceptance_criteria(cls, values: Any) -> Any:
        """Normalize acceptance_criteria entries that the LLM emits as dicts."""
        ac = values.get("acceptance_criteria") if isinstance(values, dict) else getattr(values, "acceptance_criteria", None)
        if ac and isinstance(ac, list):
            coerced = []
            for item in ac:
                if isinstance(item, str):
                    coerced.append(item)
                elif isinstance(item, dict):
                    # Take the first string value from single-key dicts like {"criterion": "..."}
                    coerced.append(next((v for v in item.values() if isinstance(v, str)), str(item)))
                else:
                    coerced.append(str(item))
            if isinstance(values, dict):
                values["acceptance_criteria"] = coerced
            else:
                values.acceptance_criteria = coerced
        return values


# ─── Traceability Schemas ─────────────────────────────────────────────────────

class TraceabilityNode(BaseModel):
    id: str
    type: str
    label: str
    metadata: Optional[dict] = None


class TraceabilityEdge(BaseModel):
    source: str
    target: str
    link_type: str
    confidence: float = 1.0


class TraceabilityGraphResponse(BaseModel):
    nodes: list[TraceabilityNode]
    edges: list[TraceabilityEdge]


# ─── Pipeline Schemas ─────────────────────────────────────────────────────────

class PipelineStatusResponse(BaseModel):
    session_id: str
    status: str
    progress: float
    current_agent: Optional[str]
    error_message: Optional[str]
    approved_for_push: bool = False
    agent_runs: list[AgentRunResponse] = []


class AgentRunResponse(BaseModel):
    id: str
    agent_name: str
    started_at: datetime
    completed_at: Optional[datetime]
    status: str
    confidence_score: Optional[float]
    token_usage_input: Optional[int]
    token_usage_output: Optional[int]
    error_message: Optional[str]

    model_config = {"from_attributes": True}


# ─── ADO Push Schemas ─────────────────────────────────────────────────────────

class ADOPushRequest(BaseModel):
    ado_project: Optional[str] = None
    # Deprecated: use ``ado_org_url`` instead. Accepted for back-compat with
    # older frontends that still POST it; the API treats it as a fallback for
    # the project name (its original—mistaken—meaning).
    ado_org: Optional[str] = Field(default=None, deprecated=True)
    # Per-session overrides for the credentials — lets users supply their own
    # PAT from the UI without restarting the backend. Falls back to .env.
    ado_org_url: Optional[str] = None
    ado_pat: Optional[str] = None


class ADOPushLogResponse(BaseModel):
    id: str
    tool_name: str
    ado_work_item_id: Optional[int]
    success: bool
    error_message: Optional[str]
    latency_ms: Optional[int]
    timestamp: datetime

    model_config = {"from_attributes": True}

# ─── ADO Import Schemas ─────────────────────────────────────────────

class ADOImportRequest(BaseModel):
    refs: list[str] = Field(
        ..., description="Work item ids, AB#123 tokens, or ADO URLs. Mixed input accepted."
    )
    include_parents: bool = True
    include_children: bool = True
    max_depth: int = Field(default=2, ge=0, le=5)
    # Optional per-request credentials — see ADOPushRequest for rationale.
    ado_org_url: Optional[str] = None
    ado_pat: Optional[str] = None
    ado_project: Optional[str] = None


class ADOImportedItemResponse(BaseModel):
    id: str
    ado_id: int
    work_item_type: str
    title: str
    state: str
    description: Optional[str]
    acceptance_criteria: Optional[str]
    assigned_to: Optional[str]
    parent_ado_id: Optional[int]
    child_ado_ids: Optional[list[int]]
    related_ado_ids: Optional[list[int]]
    url: Optional[str]
    imported_at: datetime

    model_config = {"from_attributes": True}


class ADOImportSummary(BaseModel):
    requested: int
    fetched: int
    items: list[ADOImportedItemResponse]


class WorkItemContextImportRequest(BaseModel):
    """Pull ADO work items into a session as pipeline context (ingested like a document)."""

    refs: str = Field(
        ...,
        description="Comma/space/newline separated work item ids or ADO work-item URLs.",
    )
    # Optional per-request credentials — see ADOPushRequest for rationale.
    ado_org_url: Optional[str] = None
    ado_pat: Optional[str] = None
    ado_project: Optional[str] = None


class WorkItemContextImportResponse(BaseModel):
    document_id: str
    filename: str
    requested: int
    fetched: int
    work_item_ids: list[int]

# ─── Report Schemas ───────────────────────────────────────────────────────────

class SessionReportResponse(BaseModel):
    session_id: str
    markdown: str
    generated_at: datetime


# ─── Health ───────────────────────────────────────────────────────────────────

class HealthResponse(BaseModel):
    status: str = "ok"
    version: str = "0.1.0"
    database: str = "connected"
