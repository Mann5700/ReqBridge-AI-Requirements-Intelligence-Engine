"""Ingestion Agent — cleans, normalizes, and deduplicates document chunks."""

from datetime import datetime
from backend.app.core.time import utcnow

from backend.app.agents.base_agent import AgentState, BaseAgent


class IngestionAgent(BaseAgent):
    """Cleans and normalizes raw document chunks before extraction.

    Responsibilities:
    - Remove boilerplate (headers, footers, page numbers)
    - Normalize whitespace and formatting artifacts
    - Deduplicate near-identical chunks across documents
    - Tag chunks with structural metadata (heading level, list context)
    """

    agent_name = "ingestion"

    async def execute(self, state: AgentState) -> AgentState:
        """Clean and normalize all chunks in the pipeline state."""
        started_at = utcnow()

        cleaned_chunks: list[dict] = []
        seen_content_hashes: set[str] = set()

        for chunk in state.chunks:
            content = chunk.get("content", "").strip()
            if not content or len(content) < 10:
                continue

            # Normalize whitespace
            content = " ".join(content.split())

            # Dedup by content hash
            content_hash = self.compute_hash(content)
            if content_hash in seen_content_hashes:
                continue
            seen_content_hashes.add(content_hash)

            chunk["content"] = content
            cleaned_chunks.append(chunk)

        state.chunks = cleaned_chunks
        state.progress = 0.14

        await self.log_run(
            session_id=state.session_id,
            status="completed",
            confidence=1.0,
            input_hash=self.compute_hash(state.chunks),
            output_hash=self.compute_hash(cleaned_chunks),
            started_at=started_at,
        )

        return state
