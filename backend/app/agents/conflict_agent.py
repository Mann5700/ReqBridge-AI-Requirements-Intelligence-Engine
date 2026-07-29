"""Conflict Agent — batched LLM analysis to detect contradictions/ambiguities/overlaps."""

import json
from datetime import datetime
from backend.app.core.time import utcnow

from backend.app.agents.base_agent import AgentState, BaseAgent


class ConflictAgent(BaseAgent):
    """Detects contradictions, ambiguities, and overlaps across requirements.

    Instead of issuing one LLM call per requirement pair (O(n²) calls — which
    dominated wall-clock time and silently capped at 100 pairs), this agent sends
    the entire requirement set to the model in a single batched call and asks it
    to return every conflicting pair. This is O(1) LLM calls in the common case.

    For very large sets, requirements are bucketed by category via a hash map
    (O(n)) and analyzed one bucket per call — since conflicts overwhelmingly
    occur within the same category — keeping the call count to O(#categories)
    instead of O(n²).
    """

    agent_name = "conflict"

    # Conflict detection benefits from stronger reasoning, so prefer the
    # smart model (settings.llm_model_smart) when one is configured.
    use_smart_model = True

    # Above this many requirements, switch from a single whole-set call to
    # per-category batched calls to keep each prompt/response bounded.
    MAX_SINGLE_CALL = 80

    async def execute(self, state: AgentState) -> AgentState:
        """Analyze all requirements for conflicts using batched LLM calls."""
        started_at = utcnow()
        prompt_template = self.load_prompt("conflict_detection_batch_v1")

        requirements = state.requirements
        if len(requirements) < 2:
            state.conflicts = []
            state.progress = 0.43
            await self.log_run(
                session_id=state.session_id,
                status="completed",
                confidence=1.0,
                started_at=started_at,
            )
            return state

        # Build compact requirement records with a stable id, plus an O(1)
        # validation set so we can discard any ids the model invents.
        compact: list[dict] = []
        valid_ids: set[str] = set()
        for i, req in enumerate(requirements):
            rid = str(req.get("id") or f"REQ-{i}")
            valid_ids.add(rid)
            compact.append({
                "id": rid,
                "statement": req.get("statement", ""),
                "category": req.get("category", "unknown"),
            })

        # Choose batches: one whole-set batch when small, else bucket by category.
        if len(compact) <= self.MAX_SINGLE_CALL:
            batches = [compact]
        else:
            buckets: dict[str, list[dict]] = {}
            for rec in compact:
                buckets.setdefault(rec["category"], []).append(rec)
            batches = [b for b in buckets.values() if len(b) >= 2]

        system_prompt = (
            "You are a requirements conflict detection AI. "
            "Always respond with a single valid JSON array only."
        )

        conflicts: list[dict] = []
        seen_pairs: set[frozenset[str]] = set()  # O(1) dedup of A↔B / B↔A
        total_input_tokens = 0
        total_output_tokens = 0

        for batch in batches:
            if len(batch) < 2:
                continue

            user_message = prompt_template.format(
                requirements_json=json.dumps(batch, ensure_ascii=False)
            )

            response_text, input_tokens, output_tokens = await self.call_llm(
                system_prompt=system_prompt,
                user_message=user_message,
                max_tokens=4096,
                temperature=0.1,
            )
            total_input_tokens += input_tokens
            total_output_tokens += output_tokens

            try:
                result = self.parse_json_response(response_text)
            except (json.JSONDecodeError, ValueError, KeyError):
                state.errors.append("Failed to parse batched conflict response")
                continue

            if not isinstance(result, list):
                continue

            for item in result:
                if not isinstance(item, dict):
                    continue
                a_id = str(item.get("requirement_a_id", ""))
                b_id = str(item.get("requirement_b_id", ""))
                # Validate ids in O(1) and drop self-pairs / hallucinated ids.
                if a_id == b_id or a_id not in valid_ids or b_id not in valid_ids:
                    continue
                pair_key = frozenset((a_id, b_id))
                if pair_key in seen_pairs:
                    continue
                seen_pairs.add(pair_key)
                conflicts.append({
                    "requirement_a_id": a_id,
                    "requirement_b_id": b_id,
                    "conflict_type": item.get("conflict_type", "unknown"),
                    "severity": item.get("severity", "medium"),
                    "confidence_score": item.get("confidence", 0.5),
                    "explanation": item.get("explanation", ""),
                    "suggested_resolution": item.get("suggested_resolution", ""),
                })

        state.conflicts = conflicts
        state.progress = 0.43

        avg_confidence = (
            sum(c.get("confidence_score", 0) for c in conflicts) / len(conflicts)
            if conflicts
            else 1.0
        )

        await self.log_run(
            session_id=state.session_id,
            status="completed",
            confidence=avg_confidence,
            input_hash=self.compute_hash(requirements),
            output_hash=self.compute_hash(conflicts),
            token_input=total_input_tokens,
            token_output=total_output_tokens,
            started_at=started_at,
        )

        return state
