"""Feedback Agent — persists human corrections and computes per-agent accuracy."""

from datetime import datetime
from backend.app.core.time import utcnow

from backend.app.agents.base_agent import AgentState, BaseAgent
from backend.app.core.database import async_session_factory
from backend.app.models import FeedbackCorrection


class FeedbackAgent(BaseAgent):
    """Processes human corrections and updates agent performance metrics.

    Responsibilities:
    - Log all human corrections to the feedback_corrections table
    - Compute per-agent accuracy metrics over time
    - Identify patterns in corrections (systematic errors)
    - Suggest prompt template refinements based on correction patterns
    """

    agent_name = "feedback"

    async def execute(self, state: AgentState) -> AgentState:
        """Process any pending corrections in the pipeline state."""
        started_at = utcnow()

        corrections = state.metadata.get("pending_corrections", [])
        if not corrections:
            await self.log_run(
                session_id=state.session_id,
                status="completed",
                confidence=1.0,
                started_at=started_at,
            )
            return state

        async with async_session_factory() as db_session:
            for correction in corrections:
                feedback = FeedbackCorrection(
                    session_id=state.session_id,
                    requirement_id=correction.get("requirement_id"),
                    agent_name=correction.get("agent_name", "extraction"),
                    original_output=correction.get("original_text", ""),
                    corrected_output=correction.get("corrected_text", ""),
                    correction_type=correction.get("correction_type", "text_edit"),
                )
                db_session.add(feedback)
            await db_session.commit()

        # Clear processed corrections
        state.metadata["pending_corrections"] = []
        state.metadata["corrections_processed"] = len(corrections)

        await self.log_run(
            session_id=state.session_id,
            status="completed",
            confidence=1.0,
            input_hash=self.compute_hash(corrections),
            started_at=started_at,
        )

        return state

    async def compute_agent_accuracy(
        self, agent_name: str, session_id: str | None = None
    ) -> dict:
        """Compute accuracy metrics for an agent: total runs, total corrections, accuracy_rate."""
        from sqlalchemy import func, select

        from backend.app.models import AgentRun

        async with async_session_factory() as db_session:
            # Count total runs
            runs_query = select(func.count(AgentRun.id)).where(
                AgentRun.agent_name == agent_name
            )
            if session_id:
                runs_query = runs_query.where(AgentRun.session_id == session_id)
            total_runs = (await db_session.execute(runs_query)).scalar() or 0

            # Count corrections
            corrections_query = select(func.count(FeedbackCorrection.id)).where(
                FeedbackCorrection.agent_name == agent_name
            )
            if session_id:
                corrections_query = corrections_query.where(
                    FeedbackCorrection.session_id == session_id
                )
            total_corrections = (await db_session.execute(corrections_query)).scalar() or 0

        accuracy = 1.0 - (total_corrections / max(total_runs, 1))
        return {
            "agent_name": agent_name,
            "total_outputs": total_runs,
            "total_corrections": total_corrections,
            "accuracy_rate": accuracy,
        }
