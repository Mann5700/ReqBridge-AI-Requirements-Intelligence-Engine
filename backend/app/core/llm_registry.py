"""Registry of the default LLM model entry from environment configuration.

The ``get_model()`` function always returns the default model configured via
the ``LLM_*`` environment variables.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Literal

from backend.app.core.config import settings

logger = logging.getLogger(__name__)

ProviderShape = Literal["openai", "azure", "anthropic", "vscode_bridge"]


@dataclass(frozen=True)
class ModelEntry:
    """A single model + the credentials needed to call it."""

    id: str
    label: str
    provider: ProviderShape
    base_url: str
    model: str
    api_key: str = ""
    api_version: str = ""
    extra: dict = field(default_factory=dict)

    def public_dict(self) -> dict:
        """Shape returned to the frontend — secrets stripped."""
        return {
            "id": self.id,
            "label": self.label,
            "provider": self.provider,
            "model": self.model,
        }


_DEFAULT_ID = "default"
_ENTRY: ModelEntry | None = None


def _build_default_entry() -> ModelEntry:
    """Wrap the legacy LLM_* settings as the default entry."""
    provider = (settings.llm_provider or "openai").lower()
    if provider not in ("openai", "azure", "anthropic", "vscode_bridge"):
        provider = "openai"
    return ModelEntry(
        id=_DEFAULT_ID,
        label=f"{settings.llm_model} (env default)",
        provider=provider,  # type: ignore[arg-type]
        base_url=settings.llm_base_url.rstrip("/"),
        model=settings.llm_model,
        api_key=settings.llm_api_key,
        api_version=settings.llm_api_version,
    )


def reset() -> None:
    """Clear the cache. Tests call this after monkeypatching settings."""
    global _ENTRY
    _ENTRY = None


def list_models() -> list[ModelEntry]:
    """Return the default model entry."""
    global _ENTRY
    if _ENTRY is None:
        _ENTRY = _build_default_entry()
    return [_ENTRY]


def get_model(model_id: str | None = None) -> ModelEntry:
    """Always returns the default model entry."""
    global _ENTRY
    if _ENTRY is None:
        _ENTRY = _build_default_entry()
    return _ENTRY


def default_id() -> str:
    return _DEFAULT_ID
