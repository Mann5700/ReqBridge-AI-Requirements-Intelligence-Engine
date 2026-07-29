"""Base agent: shared LLM, prompt loading, retry, JSON parsing, and run logging."""

import hashlib
import json
import ssl
from abc import ABC, abstractmethod
from dataclasses import replace as dataclass_replace
from datetime import datetime
from backend.app.core.time import utcnow
from pathlib import Path
from typing import Any, TypeVar

import httpx
import truststore
from pydantic import BaseModel
from tenacity import retry, stop_after_attempt, wait_exponential

from backend.app.core.config import settings
from backend.app.core.database import async_session_factory
from backend.app.core.llm_registry import ModelEntry, get_model
from backend.app.core import quota as quota_tracker
from backend.app.models import AgentRun

T = TypeVar("T", bound=BaseModel)


# Use the OS trust store (Windows cert store) so corporate TLS-inspection
# proxies (e.g. Zscaler/Netskope) work without disabling verification.
_SSL_CTX = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)


class AgentState(BaseModel):
    """Shared state passed between agents in the LangGraph pipeline."""
    session_id: str
    chunks: list[dict] = []
    requirements: list[dict] = []
    conflicts: list[dict] = []
    work_items: list[dict] = []
    traceability_links: list[dict] = []
    current_agent: str = ""
    progress: float = 0.0
    errors: list[str] = []
    metadata: dict = {}


class BaseAgent(ABC):
    """Abstract base class for all ReqBridge pipeline agents.

    Each agent is a node in the LangGraph StateGraph. This base class provides
    common infrastructure: LLM calls, prompt loading, run logging, and confidence
    scoring — enabling reproducible research metrics across agent invocations.
    """

    agent_name: str = "base"
    prompt_dir: Path = Path(__file__).parent.parent / "prompts"

    # When True, this agent prefers the reasoning-heavy model configured via
    # `settings.llm_model_smart` (falling back to the default model when that
    # is empty). Fast/structured agents leave this False to use the default.
    use_smart_model: bool = False

    def __init__(self) -> None:
        # Active model is resolved per-run from state.metadata["model_id"]
        # in __call__ below. Defaults to whatever the registry returns when
        # no id is supplied (== the env LLM_* config).
        self._model: ModelEntry = get_model(None)
        self._http = httpx.AsyncClient(
            timeout=settings.llm_request_timeout,
            verify=_SSL_CTX,
        )

    def _apply_model(self, model_id: str | None) -> None:
        """Switch the active model for the current run.

        Called from __call__ before execute() so each pipeline invocation can
        target a different provider/model than the env default.
        """
        self._model = get_model(model_id)
        # Reasoning-heavy agents may override just the model id (sharing the
        # same provider/base_url/credentials) when llm_model_smart is set.
        if self.use_smart_model and settings.llm_model_smart:
            self._model = dataclass_replace(
                self._model, model=settings.llm_model_smart
            )

    @property
    def provider(self) -> str:
        return self._model.provider

    @property
    def base_url(self) -> str:
        return self._model.base_url

    @property
    def model(self) -> str:
        return self._model.model

    @property
    def api_key(self) -> str:
        return self._model.api_key

    @property
    def api_version(self) -> str:
        return self._model.api_version

    def load_prompt(self, prompt_name: str) -> str:
        """Load a versioned prompt template from the prompts directory.

        Supports the prompt versioning system by loading the latest version
        of a named prompt template.
        """
        prompt_path = self.prompt_dir / f"{prompt_name}.txt"
        if not prompt_path.exists():
            raise FileNotFoundError(f"Prompt template not found: {prompt_path}")
        return prompt_path.read_text(encoding="utf-8")

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=30),
    )
    async def call_llm(
        self,
        system_prompt: str,
        user_message: str,
        max_tokens: int = 4096,
        temperature: float = 0.1,
    ) -> tuple[str, int, int]:
        """Call the configured LLM. Returns (text, input_tokens, output_tokens).

        Supports four request shapes via the active ModelEntry's provider:
          - "openai" (default): Ollama, OpenAI, GitHub Models, LM Studio,
             llama.cpp, Microsoft Foundry Local, OpenRouter — uses
             /chat/completions with Authorization: Bearer.
          - "azure": Azure OpenAI Service — api-key header + ?api-version=.
          - "anthropic": Anthropic Messages API — x-api-key header,
             /v1/messages, system prompt as a top-level field.
          - "vscode_bridge": local VS Code LLM Bridge extension — POST /chat
             with {system_prompt, user_prompt, model_id}; routes to the
             model selected in VS Code's model picker (e.g. Claude Opus).
        """
        if self.provider == "vscode_bridge":
            # The bridge runs inside VS Code and exposes the editor's model
            # picker over localhost. It returns plain text and no token usage.
            url = f"{self.base_url}/chat"
            headers = {"Content-Type": "application/json"}
            if self.api_key:
                headers["X-Bridge-Key"] = self.api_key
            payload = {
                "system_prompt": system_prompt,
                "user_prompt": user_message,
                "model_id": self.model,
            }
            response = await self._http.post(url, json=payload, headers=headers)
            response.raise_for_status()
            data = response.json()
            content = data.get("content", "")
            if not isinstance(content, str) or not content.strip():
                raise ValueError("VS Code bridge returned empty content")
            # The bridge does not report token counts.
            return (content, 0, 0)

        if self.provider == "azure":
            url = f"{self.base_url}/chat/completions?api-version={self.api_version or settings.llm_api_version}"
            headers = {"Content-Type": "application/json", "api-key": self.api_key}
            payload = {
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message},
                ],
                "temperature": temperature,
                "max_tokens": max_tokens,
            }
            response = await self._http.post(url, json=payload, headers=headers)
            quota_tracker.record(self._model.id, response.headers)
            response.raise_for_status()
            data = response.json()
            text = data["choices"][0]["message"]["content"]
            usage = data.get("usage", {}) or {}
            return (
                text,
                int(usage.get("prompt_tokens", 0)),
                int(usage.get("completion_tokens", 0)),
            )

        if self.provider == "anthropic":
            # Native Anthropic Messages API. base_url should be the API root
            # (e.g. https://api.anthropic.com) — we append /v1/messages.
            url = f"{self.base_url}/v1/messages"
            headers = {
                "Content-Type": "application/json",
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
            }
            payload = {
                "model": self.model,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "system": system_prompt,
                "messages": [{"role": "user", "content": user_message}],
            }
            response = await self._http.post(url, json=payload, headers=headers)
            quota_tracker.record(self._model.id, response.headers)
            response.raise_for_status()
            data = response.json()
            # content is a list of blocks; concatenate any text blocks.
            blocks = data.get("content") or []
            text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
            usage = data.get("usage", {}) or {}
            return (
                text,
                int(usage.get("input_tokens", 0)),
                int(usage.get("output_tokens", 0)),
            )

        # Default: OpenAI-compatible /chat/completions
        url = f"{self.base_url}/chat/completions"
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        response = await self._http.post(url, json=payload, headers=headers)
        quota_tracker.record(self._model.id, response.headers)
        response.raise_for_status()
        data = response.json()
        text = data["choices"][0]["message"]["content"]
        usage = data.get("usage", {}) or {}
        return (
            text,
            int(usage.get("prompt_tokens", 0)),
            int(usage.get("completion_tokens", 0)),
        )

    def parse_json_response(self, response_text: str) -> Any:
        """Extract and parse JSON from LLM response, handling markdown fences.

        Robust parser that handles common LLM output patterns:
        - Raw JSON
        - JSON wrapped in ```json ... ``` fences
        - JSON with trailing text after the closing bracket
        - Truncated JSON (attempts partial salvage)
        """
        text = response_text.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            lines = lines[1:]  # Remove opening fence
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines)

        # Find the JSON array or object boundaries
        start_idx = -1
        for i, ch in enumerate(text):
            if ch in ("[", "{"):
                start_idx = i
                break
        if start_idx == -1:
            return []

        bracket = text[start_idx]
        close_bracket = "]" if bracket == "[" else "}"
        depth = 0
        end_idx = -1
        for i in range(start_idx, len(text)):
            if text[i] == bracket:
                depth += 1
            elif text[i] == close_bracket:
                depth -= 1
                if depth == 0:
                    end_idx = i
                    break

        if end_idx != -1:
            return json.loads(text[start_idx : end_idx + 1])

        # Truncated JSON — attempt to salvage complete items from an array.
        # Find the last complete object (ending with '}') before truncation.
        if bracket == "[":
            truncated = text[start_idx:]
            last_obj_end = truncated.rfind("}")
            if last_obj_end > 0:
                candidate = truncated[: last_obj_end + 1].rstrip().rstrip(",") + "]"
                try:
                    result = json.loads(candidate)
                    if isinstance(result, list) and result:
                        return result
                except json.JSONDecodeError:
                    pass

        raise json.JSONDecodeError("Unterminated JSON (likely output truncated)", text, len(text))

    @staticmethod
    def compute_hash(data: Any) -> str:
        """Compute SHA-256 hash of input/output for dedup and change detection."""
        serialized = json.dumps(data, sort_keys=True, default=str)
        return hashlib.sha256(serialized.encode()).hexdigest()

    async def log_run(
        self,
        session_id: str,
        status: str,
        confidence: float | None = None,
        input_hash: str | None = None,
        output_hash: str | None = None,
        token_input: int | None = None,
        token_output: int | None = None,
        error: str | None = None,
        started_at: datetime | None = None,
    ) -> str:
        """Persist agent run metadata (timing, tokens, hashes, confidence) to the database."""
        run = AgentRun(
            session_id=session_id,
            agent_name=self.agent_name,
            started_at=started_at or utcnow(),
            completed_at=utcnow(),
            status=status,
            input_hash=input_hash,
            output_hash=output_hash,
            confidence_score=confidence,
            token_usage_input=token_input,
            token_usage_output=token_output,
            error_message=error,
        )
        async with async_session_factory() as session:
            session.add(run)
            await session.commit()
            return run.id

    @abstractmethod
    async def execute(self, state: AgentState) -> AgentState:
        """Execute the agent's core logic, transforming the pipeline state.

        Each agent implementation must:
        1. Read relevant data from state
        2. Perform its specialized processing (LLM calls, analysis, etc.)
        3. Update state with results
        4. Log the run to database
        5. Return the updated state
        """
        ...

    async def __call__(self, state: dict) -> dict:
        """LangGraph node interface — wraps execute() with logging, broadcasting, error handling."""
        agent_state = AgentState(**state)
        agent_state.current_agent = self.agent_name
        started_at = utcnow()

        # Honour any per-run model override stashed in metadata by the API.
        # Falls back to the env default when missing/unknown.
        self._apply_model(agent_state.metadata.get("model_id"))

        # Best-effort progress notification to any connected WebSocket clients.
        # Imported lazily to avoid a circular import (api ↔ agents).
        try:
            from backend.app.api.sessions import _broadcast  # type: ignore
            await _broadcast(agent_state.session_id, {
                "event": "agent_started",
                "current_agent": self.agent_name,
                "progress": agent_state.progress,
                "status": "processing",
            })
        except Exception:
            pass

        try:
            result = await self.execute(agent_state)
            try:
                from backend.app.api.sessions import _broadcast  # type: ignore
                await _broadcast(result.session_id, {
                    "event": "agent_completed",
                    "current_agent": self.agent_name,
                    "progress": result.progress,
                    "status": "processing",
                })
            except Exception:
                pass
            return result.model_dump()
        except Exception as e:
            agent_state.errors.append(f"{self.agent_name}: {str(e)}")
            await self.log_run(
                session_id=agent_state.session_id,
                status="failed",
                error=str(e),
                started_at=started_at,
            )
            return agent_state.model_dump()
