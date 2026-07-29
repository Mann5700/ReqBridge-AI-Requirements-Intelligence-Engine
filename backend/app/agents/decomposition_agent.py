"""Decomposition Agent — produces Epic→Feature→Story→Task→TestCase hierarchy."""

import json
from datetime import datetime
from backend.app.core.time import utcnow

from backend.app.agents.base_agent import AgentState, BaseAgent


class DecompositionAgent(BaseAgent):
    """Decomposes approved requirements into Epic→Feature→Story→Task→TestCase hierarchy.

    Generates a complete Azure DevOps work item tree with:
    - Epics grouping related functional areas
    - Features as deliverable increments
    - User Stories in standard format with acceptance criteria
    - Tasks as implementable work units
    - Test Cases covering positive, negative, and edge scenarios
    """

    agent_name = "decomposition"

    # Decomposition into a well-structured work-item tree benefits from
    # stronger reasoning, so prefer the smart model when one is configured.
    use_smart_model = True

    async def execute(self, state: AgentState) -> AgentState:
        """Generate full work item hierarchy from approved requirements."""
        started_at = utcnow()
        prompt_template = self.load_prompt("decomposition_v1")
        total_input_tokens = 0
        total_output_tokens = 0

        # Only decompose approved or high-confidence requirements
        approved_reqs = [
            r for r in state.requirements
            if r.get("status") == "approved"
            or r.get("confidence", 0) >= 0.7
        ]

        if not approved_reqs:
            state.progress = 0.7
            await self.log_run(
                session_id=state.session_id,
                status="completed",
                confidence=1.0,
                started_at=started_at,
            )
            return state

        system_prompt = (
            "You are an Agile work item decomposition AI. "
            "Always respond with a valid JSON array of work items. "
            "Each item must have: work_item_type, title, description, "
            "acceptance_criteria (array), parent_ref, priority, story_points, "
            "tags (array), source_requirement_ids (array)."
        )

        # Process in batches of 10 requirements
        all_work_items: list[dict] = []
        batch_size = 10

        for batch_start in range(0, len(approved_reqs), batch_size):
            batch = approved_reqs[batch_start : batch_start + batch_size]
            requirements_json = json.dumps(batch, indent=2, default=str)

            user_message = prompt_template.format(
                requirements_json=requirements_json
            )

            response_text, input_tokens, output_tokens = await self.call_llm(
                system_prompt=system_prompt,
                user_message=user_message,
                max_tokens=8192,
                temperature=0.2,
            )
            total_input_tokens += input_tokens
            total_output_tokens += output_tokens

            try:
                work_items = self.parse_json_response(response_text)
                if isinstance(work_items, list):
                    all_work_items.extend(work_items)
            except (json.JSONDecodeError, ValueError):
                state.errors.append(
                    f"Failed to parse decomposition response for batch at {batch_start}"
                )

        # Resolve parent references to create proper hierarchy
        state.work_items = self._resolve_hierarchy(all_work_items)
        state.progress = 0.7

        await self.log_run(
            session_id=state.session_id,
            status="completed",
            confidence=0.85,
            input_hash=self.compute_hash(approved_reqs),
            output_hash=self.compute_hash(state.work_items),
            token_input=total_input_tokens,
            token_output=total_output_tokens,
            started_at=started_at,
        )

        return state

    def _resolve_hierarchy(self, work_items: list[dict]) -> list[dict]:
        """Resolve parent_ref strings into proper parent_id relationships.

        The LLM outputs parent_ref as a title reference; this method resolves
        those references into stable IDs for database persistence.
        """
        # Index items by their title for parent lookup
        title_to_idx: dict[str, int] = {}
        for idx, item in enumerate(work_items):
            title_to_idx[item.get("title", "")] = idx
            item["_local_id"] = f"WI-{idx:04d}"
            item["parent_id"] = None

        # Resolve parent references
        for item in work_items:
            parent_ref = item.get("parent_ref", "")
            if parent_ref and parent_ref in title_to_idx:
                parent_idx = title_to_idx[parent_ref]
                item["parent_id"] = work_items[parent_idx]["_local_id"]

        return work_items
