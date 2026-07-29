"""Extraction Agent — extracts requirement statements from document chunks."""

import asyncio
import json
import uuid
from datetime import datetime
from backend.app.core.time import utcnow

from backend.app.agents.base_agent import AgentState, BaseAgent
from backend.app.core.config import settings


class ExtractionAgent(BaseAgent):
    """Extracts discrete requirement statements from semantic document chunks.

    Uses Claude with a carefully engineered system prompt and structured JSON
    output to identify functional/non-functional requirements, constraints,
    and assumptions from raw document content. Each extracted requirement
    carries an explainable confidence score.
    """

    agent_name = "extraction"

    @property
    def use_smart_model(self) -> bool:  # type: ignore[override]
        """Extraction can opt into the smart model via EXTRACTION_USE_SMART_MODEL.

        Off by default so extraction stays on the fast model and its per-chunk
        calls remain cheap enough to parallelize aggressively.
        """
        return settings.extraction_use_smart_model

    async def execute(self, state: AgentState) -> AgentState:
        """Process all document chunks to extract requirements.

        Iterates over semantic chunks, calling Claude for each to extract
        requirement statements. Aggregates results with deduplication
        based on semantic similarity of extracted statements.
        """
        started_at = utcnow()
        prompt_template = self.load_prompt("extraction_v1")
        all_requirements: list[dict] = []
        total_input_tokens = 0
        total_output_tokens = 0

        # Load the domain knowledge so extraction is area-aware (EDC,
        # IDC, ODC, BRE_SEC, MSG, REG, SCAN_STS, RPT, INFRA, PERF, MOD)
        # and knows the Feature Template fields it should look for.
        try:
            domain_knowledge = self.load_prompt("domain_knowledge_v1")
        except FileNotFoundError:
            domain_knowledge = ""

        system_prompt = (
            "You are a requirements extraction AI for an enterprise software testing domain. "
            "Always respond with valid JSON only. No explanatory text outside "
            "the JSON structure.\n\n"
        )
        if domain_knowledge:
            system_prompt += (
                "# Permanent Domain Knowledge (always applies)\n\n"
                + domain_knowledge
                + "\n\nUse the area definitions above to set the `category` "
                "field of each requirement when the source clearly maps to one "
                "(e.g. 'functional' covers EDC/IDC/MSG behaviors; 'non_functional' "
                "covers PERF/INFRA quality attributes).\n\n"
            )

        # Optional per-run user guidance threaded from the Pipeline Monitor.
        user_instructions = (state.metadata or {}).get("instructions")
        if user_instructions:
            system_prompt += (
                "\n\nAdditional reviewer guidance for this run (follow it where it "
                f"does not conflict with valid JSON output):\n{user_instructions}"
            )

        # Each chunk is independent, so fan the LLM calls out concurrently
        # (bounded by a semaphore) instead of awaiting them one-by-one. This
        # turns extraction wall-clock from O(chunks) latency into roughly the
        # latency of the slowest chunk per concurrency window.
        concurrency = max(1, settings.extraction_concurrency)
        semaphore = asyncio.Semaphore(concurrency)

        async def _extract_chunk(chunk: dict) -> tuple[dict, str | None, int, int]:
            user_message = prompt_template.format(
                chunk_content=chunk.get("content", ""),
                source_filename=chunk.get("source_filename", "unknown"),
                section_info=chunk.get("section_info", ""),
            )
            async with semaphore:
                response_text, input_tokens, output_tokens = await self.call_llm(
                    system_prompt=system_prompt,
                    user_message=user_message,
                    max_tokens=4096,
                    temperature=0.1,
                )
            return chunk, response_text, input_tokens, output_tokens

        results = await asyncio.gather(
            *(_extract_chunk(chunk) for chunk in state.chunks),
            return_exceptions=True,
        )

        # Preserve original chunk order so requirement ordering is stable.
        for outcome in results:
            if isinstance(outcome, Exception):
                state.errors.append(f"Extraction call failed: {outcome}")
                continue
            chunk, response_text, input_tokens, output_tokens = outcome
            total_input_tokens += input_tokens
            total_output_tokens += output_tokens

            try:
                extracted = self.parse_json_response(response_text)
                if isinstance(extracted, list):
                    for req in extracted:
                        # Always assign a globally-unique id. The model numbers
                        # requirements independently per chunk (REQ-1, REQ-2, …),
                        # so honoring its ids causes collisions across chunks that
                        # silently collapse rows on persist (PK clash). A fresh
                        # UUID per item keeps every requirement distinct.
                        req["id"] = str(uuid.uuid4())
                        req["source_chunk_id"] = chunk.get("id")
                        all_requirements.append(req)
            except (json.JSONDecodeError, ValueError):
                state.errors.append(
                    f"Failed to parse extraction response for chunk {chunk.get('id')}"
                )

        # Deduplicate by statement similarity (exact match for now)
        seen_statements: set[str] = set()
        unique_requirements: list[dict] = []
        for req in all_requirements:
            normalized = req.get("statement", "").strip().lower()
            if normalized not in seen_statements:
                seen_statements.add(normalized)
                unique_requirements.append(req)

        state.requirements = unique_requirements

        # Hard cap — drop lowest-confidence extras to prevent downstream
        # work-item explosion when the LLM over-splits.
        max_reqs = settings.max_requirements
        if len(state.requirements) > max_reqs:
            state.requirements.sort(
                key=lambda r: r.get("confidence", 0.0), reverse=True
            )
            dropped = len(state.requirements) - max_reqs
            state.requirements = state.requirements[:max_reqs]
            state.errors.append(
                f"Extraction guardrail: trimmed {dropped} lowest-confidence "
                f"requirements to stay within the {max_reqs} cap."
            )

        state.progress = 0.29

        # Compute aggregate confidence
        confidences = [r.get("confidence", 0.0) for r in unique_requirements]
        avg_confidence = sum(confidences) / len(confidences) if confidences else 0.0

        await self.log_run(
            session_id=state.session_id,
            status="completed",
            confidence=avg_confidence,
            input_hash=self.compute_hash(state.chunks),
            output_hash=self.compute_hash(unique_requirements),
            token_input=total_input_tokens,
            token_output=total_output_tokens,
            started_at=started_at,
        )

        return state
