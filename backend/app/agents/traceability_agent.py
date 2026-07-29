"""Traceability Agent — builds bidirectional links between chunks, requirements, work items, ADO IDs."""

from datetime import datetime
from backend.app.core.time import utcnow

from backend.app.agents.base_agent import AgentState, BaseAgent


class TraceabilityAgent(BaseAgent):
    """Builds the NetworkX traceability graph from pipeline artifacts.

    Maintains bidirectional links between:
    source_document ↔ chunk ↔ requirement ↔ work_item ↔ ado_work_item_id

    Enables impact analysis: "if requirement X changes, which work items
    and ADO items are affected?"
    """

    agent_name = "traceability"

    async def execute(self, state: AgentState) -> AgentState:
        """Build traceability links from current pipeline state."""
        started_at = utcnow()
        links: list[dict] = []

        # Link chunks → requirements
        for req in state.requirements:
            chunk_id = req.get("source_chunk_id")
            req_id = req.get("id", "")
            if chunk_id:
                links.append({
                    "source_node_type": "chunk",
                    "source_node_id": chunk_id,
                    "target_node_type": "requirement",
                    "target_node_id": req_id,
                    "link_type": "derived_from",
                    "confidence": req.get("confidence", 1.0),
                })

        # Link requirements → work items
        for wi in state.work_items:
            req_ids = wi.get("source_requirement_ids", [])
            wi_id = wi.get("_local_id", "")
            for req_id in req_ids:
                links.append({
                    "source_node_type": "requirement",
                    "source_node_id": req_id,
                    "target_node_type": "work_item",
                    "target_node_id": wi_id,
                    "link_type": "decomposes_to",
                    "confidence": 1.0,
                })

            # Link work items → ADO IDs
            ado_id = wi.get("ado_work_item_id")
            if ado_id:
                links.append({
                    "source_node_type": "work_item",
                    "source_node_id": wi_id,
                    "target_node_type": "ado_work_item",
                    "target_node_id": str(ado_id),
                    "link_type": "pushed_as",
                    "confidence": 1.0,
                })

        # Link work item hierarchy (parent → child)
        for wi in state.work_items:
            parent_id = wi.get("parent_id")
            if parent_id:
                links.append({
                    "source_node_type": "work_item",
                    "source_node_id": parent_id,
                    "target_node_type": "work_item",
                    "target_node_id": wi.get("_local_id", ""),
                    "link_type": "parent_of",
                    "confidence": 1.0,
                })

        state.traceability_links = links
        state.progress = 0.86

        await self.log_run(
            session_id=state.session_id,
            status="completed",
            confidence=1.0,
            input_hash=self.compute_hash({
                "requirements": state.requirements,
                "work_items": state.work_items,
            }),
            output_hash=self.compute_hash(links),
            started_at=started_at,
        )

        return state
