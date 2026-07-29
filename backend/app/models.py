"""SQLAlchemy ORM models for ReqBridge.

Defines the full data model supporting multi-modal document ingestion,
AI-extracted requirements with confidence scoring, hierarchical work items,
traceability links, agent execution logging, and Azure DevOps push auditing.

Uses SQLAlchemy 2.0 ``Mapped`` / ``mapped_column`` typing so static type
checkers (Pyright/Pylance) infer the actual Python attribute types instead of
``Column[T]`` descriptors.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from backend.app.core.time import utcnow as _utcnow
from typing import Any, Optional

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.core.database import Base


def generate_uuid() -> str:
    return str(uuid.uuid4())


def generate_short_id() -> str:
    """8-char hex id for user-facing entities like UploadSession.

    secrets.token_hex(4) gives 4 random bytes → 8 hex chars (~4.3B values).
    Plenty for a single-user local app; the API handler retries on the
    astronomically-rare collision so a duplicate PK never reaches the user.
    """
    import secrets
    return secrets.token_hex(4)


# ─── Enums ────────────────────────────────────────────────────────────────────

class SessionStatus(str, enum.Enum):
    CREATED = "created"
    UPLOADING = "uploading"
    PROCESSING = "processing"
    AWAITING_REVIEW = "awaiting_review"
    APPROVED = "approved"
    PUSHING = "pushing"
    COMPLETED = "completed"
    FAILED = "failed"


class FileType(str, enum.Enum):
    PDF = "pdf"
    DOCX = "docx"
    XLSX = "xlsx"
    IMAGE = "image"
    TEXT = "text"
    EMAIL = "email"


class MoSCoWPriority(str, enum.Enum):
    MUST = "must"
    SHOULD = "should"
    COULD = "could"
    WONT = "wont"


class WorkItemType(str, enum.Enum):
    EPIC = "Epic"
    FEATURE = "Feature"
    USER_STORY = "User Story"
    TASK = "Task"
    TEST_CASE = "Test Case"


class RequirementStatus(str, enum.Enum):
    EXTRACTED = "extracted"
    CONFLICT_FLAGGED = "conflict_flagged"
    APPROVED = "approved"
    REJECTED = "rejected"
    DECOMPOSED = "decomposed"


# ─── Models ───────────────────────────────────────────────────────────────────

class UploadSession(Base):
    """A requirements ingestion session grouping documents and pipeline state."""
    __tablename__ = "upload_sessions"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=generate_short_id)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[SessionStatus] = mapped_column(Enum(SessionStatus), default=SessionStatus.CREATED)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)
    pipeline_progress: Mapped[float] = mapped_column(Float, default=0.0)
    current_agent: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    metadata_: Mapped[Optional[dict[str, Any]]] = mapped_column("metadata", JSON, nullable=True)

    documents: Mapped[list["SourceDocument"]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )
    requirements: Mapped[list["Requirement"]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )
    work_items: Mapped[list["WorkItem"]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )
    agent_runs: Mapped[list["AgentRun"]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )


class SourceDocument(Base):
    """A single uploaded document within a session — raw file metadata and parse status."""
    __tablename__ = "source_documents"
    __table_args__ = (Index("ix_source_document_session", "session_id"),)

    id: Mapped[str] = mapped_column(String, primary_key=True, default=generate_uuid)
    session_id: Mapped[str] = mapped_column(String, ForeignKey("upload_sessions.id"), nullable=False)
    filename: Mapped[str] = mapped_column(String(500), nullable=False)
    file_type: Mapped[FileType] = mapped_column(Enum(FileType), nullable=False)
    file_path: Mapped[str] = mapped_column(String(1000), nullable=False)
    file_size_bytes: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    upload_timestamp: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    parse_status: Mapped[str] = mapped_column(String(50), default="pending")
    page_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    metadata_: Mapped[Optional[dict[str, Any]]] = mapped_column("metadata", JSON, nullable=True)

    session: Mapped["UploadSession"] = relationship(back_populates="documents")
    chunks: Mapped[list["DocumentChunk"]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )


class DocumentChunk(Base):
    """Semantic chunk from a parsed document — the atomic unit for requirement extraction."""
    __tablename__ = "document_chunks"
    __table_args__ = (Index("ix_document_chunk_document", "document_id"),)

    id: Mapped[str] = mapped_column(String, primary_key=True, default=generate_uuid)
    document_id: Mapped[str] = mapped_column(String, ForeignKey("source_documents.id"), nullable=False)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    heading_hierarchy: Mapped[Optional[list[str]]] = mapped_column(JSON, nullable=True)
    page_number: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    section_title: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    chunk_type: Mapped[str] = mapped_column(String(50), default="paragraph")
    embedding_vector: Mapped[Optional[list[float]]] = mapped_column(JSON, nullable=True)
    token_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    metadata_: Mapped[Optional[dict[str, Any]]] = mapped_column("metadata", JSON, nullable=True)

    document: Mapped["SourceDocument"] = relationship(back_populates="chunks")


class Requirement(Base):
    """An AI-extracted requirement statement with confidence scoring and traceability."""
    __tablename__ = "requirements"
    __table_args__ = (
        Index("ix_requirement_session", "session_id"),
        Index("ix_requirement_status", "status"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=generate_uuid)
    session_id: Mapped[str] = mapped_column(String, ForeignKey("upload_sessions.id"), nullable=False)
    source_chunk_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("document_chunks.id"), nullable=True)
    statement: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    moscow_priority: Mapped[Optional[MoSCoWPriority]] = mapped_column(Enum(MoSCoWPriority), nullable=True)
    confidence_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    status: Mapped[RequirementStatus] = mapped_column(Enum(RequirementStatus), default=RequirementStatus.EXTRACTED)
    assumptions: Mapped[Optional[list[str]]] = mapped_column(JSON, nullable=True)
    constraints: Mapped[Optional[list[str]]] = mapped_column(JSON, nullable=True)
    business_value_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    original_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    approved_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    approved_by: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    assigned_to: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    metadata_: Mapped[Optional[dict[str, Any]]] = mapped_column("metadata", JSON, nullable=True)

    session: Mapped["UploadSession"] = relationship(back_populates="requirements")
    conflicts: Mapped[list["RequirementConflict"]] = relationship(
        foreign_keys="RequirementConflict.requirement_a_id",
        cascade="all, delete-orphan",
    )
    work_items: Mapped[list["WorkItem"]] = relationship(back_populates="requirement")
    traceability_links: Mapped[list["TraceabilityLink"]] = relationship(
        back_populates="requirement", cascade="all, delete-orphan"
    )


class RequirementConflict(Base):
    """Detected contradiction between two requirements with AI-generated explanation."""
    __tablename__ = "requirement_conflicts"
    __table_args__ = (Index("ix_conflict_session", "session_id"),)

    id: Mapped[str] = mapped_column(String, primary_key=True, default=generate_uuid)
    session_id: Mapped[str] = mapped_column(String, ForeignKey("upload_sessions.id"), nullable=False)
    requirement_a_id: Mapped[str] = mapped_column(
        String, ForeignKey("requirements.id", ondelete="CASCADE"), nullable=False
    )
    requirement_b_id: Mapped[str] = mapped_column(
        String, ForeignKey("requirements.id", ondelete="CASCADE"), nullable=False
    )
    conflict_type: Mapped[str] = mapped_column(String(100), nullable=False)
    explanation: Mapped[str] = mapped_column(Text, nullable=False)
    severity: Mapped[str] = mapped_column(String(50), default="medium")
    confidence_score: Mapped[float] = mapped_column(Float, nullable=False)
    resolution: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    resolved: Mapped[bool] = mapped_column(Boolean, default=False)
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    requirement_a: Mapped["Requirement"] = relationship(
        foreign_keys=[requirement_a_id],
        # Both this side and `Requirement.conflicts` map to the same FK
        # column. Declare the overlap explicitly so SQLAlchemy stops
        # warning and so we acknowledge that updating one side will
        # naturally affect the other.
        overlaps="conflicts",
    )
    requirement_b: Mapped["Requirement"] = relationship(foreign_keys=[requirement_b_id])


class WorkItem(Base):
    """Generated Agile work item in the Epic→Feature→Story→Task→TestCase hierarchy."""
    __tablename__ = "work_items"
    __table_args__ = (Index("ix_workitem_session", "session_id"),)

    id: Mapped[str] = mapped_column(String, primary_key=True, default=generate_uuid)
    session_id: Mapped[str] = mapped_column(String, ForeignKey("upload_sessions.id"), nullable=False)
    requirement_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("requirements.id"), nullable=True)
    parent_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("work_items.id"), nullable=True)
    work_item_type: Mapped[WorkItemType] = mapped_column(Enum(WorkItemType), nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    acceptance_criteria: Mapped[Optional[list[str]]] = mapped_column(JSON, nullable=True)
    priority: Mapped[int] = mapped_column(Integer, default=2)
    story_points: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    tags: Mapped[Optional[list[str]]] = mapped_column(JSON, nullable=True)
    ado_work_item_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    ado_url: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True)
    pushed_to_ado: Mapped[bool] = mapped_column(Boolean, default=False)
    pushed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    metadata_: Mapped[Optional[dict[str, Any]]] = mapped_column("metadata", JSON, nullable=True)

    session: Mapped["UploadSession"] = relationship(back_populates="work_items")
    requirement: Mapped[Optional["Requirement"]] = relationship(back_populates="work_items")
    parent: Mapped[Optional["WorkItem"]] = relationship(remote_side=[id], backref="children")


class TraceabilityLink(Base):
    """Bidirectional link in the traceability graph: source→requirement→workitem→ADO."""
    __tablename__ = "traceability_links"
    __table_args__ = (Index("ix_trace_session", "session_id"),)

    id: Mapped[str] = mapped_column(String, primary_key=True, default=generate_uuid)
    session_id: Mapped[str] = mapped_column(String, ForeignKey("upload_sessions.id"), nullable=False)
    requirement_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("requirements.id"), nullable=True)
    source_node_type: Mapped[str] = mapped_column(String(50), nullable=False)
    source_node_id: Mapped[str] = mapped_column(String, nullable=False)
    target_node_type: Mapped[str] = mapped_column(String(50), nullable=False)
    target_node_id: Mapped[str] = mapped_column(String, nullable=False)
    link_type: Mapped[str] = mapped_column(String(100), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    requirement: Mapped[Optional["Requirement"]] = relationship(back_populates="traceability_links")


class AgentRun(Base):
    """Execution log for each agent invocation — enables accuracy tracking over time."""
    __tablename__ = "agent_runs"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=generate_uuid)
    session_id: Mapped[str] = mapped_column(String, ForeignKey("upload_sessions.id"), nullable=False)
    agent_name: Mapped[str] = mapped_column(String(100), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(50), default="running")
    input_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    output_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    confidence_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    token_usage_input: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    token_usage_output: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    metadata_: Mapped[Optional[dict[str, Any]]] = mapped_column("metadata", JSON, nullable=True)

    session: Mapped["UploadSession"] = relationship(back_populates="agent_runs")


class FeedbackCorrection(Base):
    """Human correction to an AI-generated output — drives active learning and prompt refinement."""
    __tablename__ = "feedback_corrections"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=generate_uuid)
    session_id: Mapped[str] = mapped_column(String, ForeignKey("upload_sessions.id"), nullable=False)
    requirement_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("requirements.id"), nullable=True)
    agent_name: Mapped[str] = mapped_column(String(100), nullable=False)
    original_output: Mapped[str] = mapped_column(Text, nullable=False)
    corrected_output: Mapped[str] = mapped_column(Text, nullable=False)
    correction_type: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    applied_to_prompt: Mapped[bool] = mapped_column(Boolean, default=False)


class PromptVersion(Base):
    """Versioned prompt template."""
    __tablename__ = "prompt_versions"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=generate_uuid)
    agent_name: Mapped[str] = mapped_column(String(100), nullable=False)
    prompt_name: Mapped[str] = mapped_column(String(200), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    performance_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    metadata_: Mapped[Optional[dict[str, Any]]] = mapped_column("metadata", JSON, nullable=True)


class ADOPushLog(Base):
    """Audit log for every Azure DevOps MCP server call — enables push reliability analysis."""
    __tablename__ = "ado_push_log"
    __table_args__ = (Index("ix_pushlog_session", "session_id"),)

    id: Mapped[str] = mapped_column(String, primary_key=True, default=generate_uuid)
    session_id: Mapped[str] = mapped_column(String, ForeignKey("upload_sessions.id"), nullable=False)
    work_item_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("work_items.id"), nullable=True)
    tool_name: Mapped[str] = mapped_column(String(200), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    response: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)
    ado_work_item_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    success: Mapped[bool] = mapped_column(Boolean, nullable=False)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    latency_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class ADOImportedItem(Base):
    """Snapshot of an Azure DevOps work item pulled in for *context* (not push).

    Stored per session so the DecompositionAgent can reuse the parent /
    sibling tree as in-context examples when generating new children.
    """
    __tablename__ = "ado_imported_items"
    __table_args__ = (
        Index("ix_ado_import_session", "session_id"),
        Index("ix_ado_import_session_ado", "session_id", "ado_id", unique=True),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=generate_uuid)
    session_id: Mapped[str] = mapped_column(
        String, ForeignKey("upload_sessions.id", ondelete="CASCADE"), nullable=False
    )
    ado_id: Mapped[int] = mapped_column(Integer, nullable=False)
    work_item_type: Mapped[str] = mapped_column(String(100), nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    state: Mapped[str] = mapped_column(String(100), default="")
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    acceptance_criteria: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    assigned_to: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    parent_ado_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    child_ado_ids: Mapped[Optional[list[int]]] = mapped_column(JSON, nullable=True)
    related_ado_ids: Mapped[Optional[list[int]]] = mapped_column(JSON, nullable=True)
    url: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True)
    raw: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)
    imported_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
