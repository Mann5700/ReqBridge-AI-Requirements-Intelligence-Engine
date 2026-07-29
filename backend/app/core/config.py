"""Application configuration loaded from environment variables."""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


# Resolve backend/.env regardless of the current working directory.
_BACKEND_ENV = Path(__file__).resolve().parents[2] / ".env"


class Settings(BaseSettings):
    """Central configuration for the ReqBridge application."""

    # Pydantic v2 settings config — replaces the deprecated class-based Config.
    model_config = SettingsConfigDict(
        env_file=str(_BACKEND_ENV),
        env_file_encoding="utf-8",
        # Be tolerant of extra env vars so unrelated variables in a shared
        # .env (e.g. PATH, NODE_ENV) don't blow up startup.
        extra="ignore",
    )

    # Database (SQLite — local single-user store)
    database_url: str = "sqlite+aiosqlite:///./reqbridge.db"

    # LLM (OpenAI-compatible endpoint — Ollama by default, no API key required)
    # llm_provider controls request shape:
    #   "openai"  -> standard /v1/chat/completions, Authorization: Bearer (Ollama,
    #                OpenAI, GitHub Models, LM Studio, llama.cpp, Foundry Local)
    #   "azure"   -> Azure OpenAI: <base>/chat/completions?api-version=...,
    #                api-key header. llm_base_url must already include the
    #                /openai/deployments/<deployment> path.
    llm_provider: str = "openai"
    llm_base_url: str = "http://localhost:11434/v1"
    llm_model: str = "llama3.1:8b"
    llm_api_key: str = ""
    llm_api_version: str = "2024-10-21"  # only used when llm_provider="azure"
    llm_request_timeout: float = 120.0

    # Reasoning-heavy agents (conflict detection + decomposition) use this model
    # while extraction/ingestion/traceability stay on the faster `llm_model`. Empty
    # -> use llm_model everywhere. Only the model id changes; provider/base_url/key
    # are shared.
    llm_model_smart: str = ""

    # When True, the extraction agent also uses `llm_model_smart` (if set) instead
    # of the fast `llm_model`. Extraction quality is foundational, so this lets you
    # A/B a stronger model for it — at the cost of slower (smart-model) chunk calls.
    extraction_use_smart_model: bool = False

    # Max number of extraction LLM calls issued concurrently. Extraction runs
    # one independent call per document chunk; raising this trades a heavier
    # burst on the LLM endpoint for lower wall-clock latency. 1 == sequential.
    extraction_concurrency: int = 4

    # Hard caps to prevent LLM over-generation.  If the extraction agent
    # produces more than max_requirements, the lowest-confidence extras are
    # dropped.  If the planning agent produces more than max_work_items, the
    # deepest items (Tasks/TestCases furthest from the root) are trimmed.
    max_requirements: int = 25
    max_work_items: int = 80

    # Optional JSON array of additional model entries the UI can pick from.
    # Schema (per item): {id, label, provider, base_url, model, api_key?,
    # api_version?, extra?}. provider ∈ {"openai","azure","anthropic"}.
    # See backend/app/core/llm_registry.py for parsing details.
    llm_providers_json: str = ""

    # Azure DevOps
    ado_org_url: str = ""
    ado_pat: str = ""
    ado_project: str = ""
    # Display name used as the default assignee on every pushed work item until
    # we wire per-user identity (see frontend ADO Sync page). Empty string means
    # leave work items unassigned in Azure DevOps.
    ado_default_assignee: str = ""
    # Value for the required Lean-Agile Epic field Custom.ORG_LA_WorkCategory.
    # Must be one of the project picklist values (e.g. "Functional initiatives",
    # "Maintenance", "Regulatory and compliance work", "Tech Modernization
    # Initiatives", "Wildly Important Initiatives", "Blank").
    ado_epic_work_category: str = "Functional initiatives"

    # App
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    log_level: str = "info"

    # Thresholds
    hitl_confidence_threshold: float = 0.7
    auto_approve_threshold: float = 0.9

    # Pipeline behaviour
    # When True the orchestrator actually halts at hitl_pause and waits for
    # the user to call /sessions/{id}/resume. When False (default) the pause
    # node is a no-op and the pipeline proceeds straight through — preserves
    # historical behaviour for users who don't want HITL.
    enable_hitl_pause: bool = False
    # Background watchdog: any session in PROCESSING/PUSHING longer than this
    # many minutes is considered abandoned and force-failed. Set to 0 to
    # disable the watchdog entirely.
    pipeline_stuck_minutes: int = 30

    # MCP
    reqbridge_api_url: str = "http://localhost:8000"


settings = Settings()
