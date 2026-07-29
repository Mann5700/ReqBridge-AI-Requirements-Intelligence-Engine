"""No-op rate-limit tracker stub.

The model-picker UI and quota badge have been removed. This module is kept
as a minimal stub so base_agent.record() calls remain harmless no-ops.
"""

from __future__ import annotations

from typing import Mapping


def record(model_id: str, headers: Mapping[str, str]) -> None:
    """No-op: previously captured rate-limit headers for the UI badge."""


def get_all() -> dict:
    """Return empty dict -- quota display has been removed."""
    return {}
