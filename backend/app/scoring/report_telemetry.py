"""Shared report footer for agent processing and token telemetry.

This module centralizes the footer HTML block used by report generators.
Values can come from a report data payload and/or environment variables.
"""

from __future__ import annotations

import html
import os
from typing import Any


DEFAULT_PROCESSING_PROFILES: dict[str, tuple[float, float]] = {
    "reqbridge_pipeline": (80.0, 20.0),
    "rom_report": (80.0, 20.0),
}


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _to_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return None


def _pick_float(data: dict[str, Any], keys: list[str], env_keys: list[str]) -> float | None:
    for key in keys:
        if key in data:
            n = _to_float(data.get(key))
            if n is not None:
                return n
    for env_key in env_keys:
        n = _to_float(os.getenv(env_key))
        if n is not None:
            return n
    return None


def _pick_int(data: dict[str, Any], keys: list[str], env_keys: list[str]) -> int | None:
    for key in keys:
        if key in data:
            n = _to_int(data.get(key))
            if n is not None:
                return n
    for env_key in env_keys:
        n = _to_int(os.getenv(env_key))
        if n is not None:
            return n
    return None


def _resolve_processing_mix(agent_name: str, data: dict[str, Any]) -> tuple[float, float, str]:
    agent_pct = _pick_float(
        data,
        ["agent_processing_pct", "agent_pct", "agent_only_processing_pct"],
        ["AGENT_PROCESSING_PCT", "REPORT_AGENT_PROCESSING_PCT"],
    )
    code_pct = _pick_float(
        data,
        ["code_processing_pct", "python_processing_pct", "non_agent_processing_pct"],
        ["CODE_PROCESSING_PCT", "REPORT_CODE_PROCESSING_PCT"],
    )

    if agent_pct is None and code_pct is None:
        defaults = DEFAULT_PROCESSING_PROFILES.get(agent_name, (0.0, 100.0))
        return (defaults[0], defaults[1], "default estimate")

    if agent_pct is None:
        agent_pct = max(0.0, 100.0 - code_pct)
    if code_pct is None:
        code_pct = max(0.0, 100.0 - agent_pct)

    total = agent_pct + code_pct
    if total <= 0:
        return (0.0, 100.0, "default estimate")

    # Normalize to ensure percentages always add up to 100.
    agent_norm = round((agent_pct / total) * 100.0, 1)
    code_norm = round(100.0 - agent_norm, 1)
    return (agent_norm, code_norm, "runtime telemetry")


def _resolve_token_usage(data: dict[str, Any]) -> tuple[int | None, int | None, int | None]:
    usage = data.get("token_usage") or data.get("usage") or data.get("llm_usage") or {}
    usage = usage if isinstance(usage, dict) else {}

    prompt = _pick_int(
        {**usage, **data},
        ["prompt_tokens", "input_tokens"],
        ["AGENT_PROMPT_TOKENS", "REPORT_PROMPT_TOKENS"],
    )
    completion = _pick_int(
        {**usage, **data},
        ["completion_tokens", "output_tokens"],
        ["AGENT_COMPLETION_TOKENS", "REPORT_COMPLETION_TOKENS"],
    )
    total = _pick_int(
        {**usage, **data},
        ["total_tokens"],
        ["AGENT_TOTAL_TOKENS", "REPORT_TOTAL_TOKENS"],
    )

    if total is None and (prompt is not None or completion is not None):
        total = (prompt or 0) + (completion or 0)

    return (prompt, completion, total)


def render_agent_footer(agent_name: str, data: dict[str, Any] | None = None) -> str:
    """Render a standard footer block for processing mix and token usage."""
    payload = data or {}
    agent_pct, code_pct, processing_source = _resolve_processing_mix(agent_name, payload)
    prompt, completion, total = _resolve_token_usage(payload)

    prompt_txt = f"{prompt:,}" if prompt is not None else "n/a"
    completion_txt = f"{completion:,}" if completion is not None else "n/a"
    total_txt = f"{total:,}" if total is not None else "n/a"

    name = html.escape(agent_name)
    return f"""
<section class="agent-telemetry" aria-label="Agent telemetry footer">
  <style>
    .agent-telemetry {{
      margin-top: 2rem;
      border-top: 1px solid var(--border, #d1d5db);
      padding-top: 0.9rem;
      font-size: 0.8rem;
      color: var(--muted, #6b7280);
    }}
    .agent-telemetry .telemetry-title {{
      font-weight: 600;
      color: var(--text, #374151);
      margin-bottom: 0.35rem;
    }}
    .agent-telemetry .telemetry-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
      gap: 0.5rem 1rem;
    }}
    .agent-telemetry .k {{ color: var(--muted, #4b5563); }}
    .agent-telemetry .v {{ font-weight: 600; color: var(--text, #111827); }}
  </style>
  <div class="telemetry-title">Agent Processing Metrics: {name}</div>
  <div class="telemetry-grid">
    <div><span class="k">Agent-only processing:</span> <span class="v">{agent_pct:.1f}%</span></div>
    <div><span class="k">Python/other code processing:</span> <span class="v">{code_pct:.1f}%</span></div>
        <div><span class="k">Processing source:</span> <span class="v">{html.escape(processing_source)}</span></div>
    <div><span class="k">Prompt tokens:</span> <span class="v">{prompt_txt}</span></div>
    <div><span class="k">Completion tokens:</span> <span class="v">{completion_txt}</span></div>
    <div><span class="k">Total tokens:</span> <span class="v">{total_txt}</span></div>
  </div>
</section>
"""
