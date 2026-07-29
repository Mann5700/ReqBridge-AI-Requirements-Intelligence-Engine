"""Prioritization Agent — assigns MoSCoW priority and business value scores."""

import asyncio
import json
import logging
from datetime import datetime
from backend.app.core.time import utcnow

from backend.app.agents.base_agent import AgentState, BaseAgent

logger = logging.getLogger(__name__)


class PrioritizationAgent(BaseAgent):
    """Assigns MoSCoW priorities and business value scores to requirements.

    Processes requirements in batches to maintain context across related
    requirements while managing token limits effectively. Batches run
    concurrently via asyncio.gather since they're independent.
    """

    agent_name = "prioritization"
    BATCH_SIZE = 20

    async def execute(self, state: AgentState) -> AgentState:
        """Score all requirements with MoSCoW priority and business value."""
        started_at = utcnow()
        prompt_template = self.load_prompt("prioritization_v1")

        requirements = state.requirements
        if not requirements:
            state.progress = 0.57
            await self.log_run(
                session_id=state.session_id,
                status="completed",
                confidence=1.0,
                started_at=started_at,
            )
            return state

        system_prompt = (
            "You are a requirements prioritization AI. "
            "Always respond with a valid JSON array only."
        )

        # Build all batch requests, then fan them out concurrently.
        batch_starts = list(range(0, len(requirements), self.BATCH_SIZE))
        logger.info(
            "Prioritization: fanning %d batches out concurrently (batch_size=%d, %d reqs)",
            len(batch_starts), self.BATCH_SIZE, len(requirements),
        )

        async def _run_batch(batch_start: int) -> tuple[int, str | None, int, int]:
            batch = requirements[batch_start : batch_start + self.BATCH_SIZE]
            batch_json = json.dumps(batch, indent=2, default=str)
            user_message = prompt_template.format(requirements_json=batch_json)
            response_text, in_tok, out_tok = await self.call_llm(
                system_prompt=system_prompt,
                user_message=user_message,
                max_tokens=2048,
                temperature=0.2,
            )
            return batch_start, response_text, in_tok, out_tok

        results = await asyncio.gather(
            *(_run_batch(bs) for bs in batch_starts),
            return_exceptions=True,
        )

        total_input_tokens = 0
        total_output_tokens = 0
        for outcome in results:
            if isinstance(outcome, Exception):
                state.errors.append(f"Prioritization batch crashed: {outcome}")
                logger.error("Prioritization batch crashed", exc_info=outcome)
                continue
            batch_start, response_text, in_tok, out_tok = outcome
            total_input_tokens += in_tok
            total_output_tokens += out_tok
            try:
                priorities = self.parse_json_response(response_text)
                if isinstance(priorities, list):
                    # Apply priorities back to requirements
                    priority_map = {
                        p.get("requirement_id"): p for p in priorities
                    }
                    for req in state.requirements:
                        req_id = req.get("id", "")
                        if req_id in priority_map:
                            p = priority_map[req_id]
                            req["moscow_priority"] = p.get("moscow_priority")
                            req["business_value_score"] = p.get("business_value_score")
            except (json.JSONDecodeError, ValueError):
                state.errors.append(
                    f"Failed to parse prioritization response for batch starting at {batch_start}"
                )

        state.progress = 0.57

        await self.log_run(
            session_id=state.session_id,
            status="completed",
            confidence=0.8,
            input_hash=self.compute_hash(requirements),
            output_hash=self.compute_hash(state.requirements),
            token_input=total_input_tokens,
            token_output=total_output_tokens,
            started_at=started_at,
        )

        return state
