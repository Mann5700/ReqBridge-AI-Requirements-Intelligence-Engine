"""LangGraph orchestrator wiring all agents into the pipeline."""

import asyncio

from langgraph.graph import END, StateGraph

from backend.app.agents.base_agent import AgentState
from backend.app.agents.conflict_agent import ConflictAgent
from backend.app.agents.extraction_agent import ExtractionAgent
from backend.app.agents.feedback_agent import FeedbackAgent
from backend.app.agents.ingestion_agent import IngestionAgent
from backend.app.agents.planning_agent import PlanningAgent
from backend.app.agents.prioritization_agent import PrioritizationAgent
from backend.app.agents.traceability_agent import TraceabilityAgent
from backend.app.core.config import settings


# ─── Conditional Edge Functions ───────────────────────────────────────────────

def should_pause_for_review(state: dict) -> str:
    """Determine if pipeline should pause for human review based on confidence.

    If any requirements have confidence below the HITL threshold,
    route to the pause node for human review before planning.
    """
    requirements = state.get("requirements", [])
    conflicts = state.get("conflicts", [])

    # Pause if there are unresolved conflicts
    if conflicts:
        return "hitl_pause"

    # Pause if low-confidence requirements exist
    low_confidence = [
        r for r in requirements
        if r.get("confidence", 0) < settings.hitl_confidence_threshold
    ]
    if low_confidence:
        return "hitl_pause"

    return "planning"


def should_continue_after_pause(state: dict) -> str:
    """Determine if pipeline should continue after HITL pause or halt.

    If human has approved (metadata flag set by /resume endpoint),
    continue to planning. Otherwise, halt (END) so the API
    can persist state and wait for user action.
    """
    metadata = state.get("metadata", {})
    if metadata.get("human_approved"):
        return "planning"
    return "end"





# ─── HITL Pause Node ──────────────────────────────────────────────────────────

async def hitl_pause_node(state: dict) -> dict:
    """Human-in-the-loop pause node.

    Marks the pipeline as awaiting review. The pipeline will resume
    when the user approves requirements via the API or MCP tool.
    This state is persisted to DB so it survives process restarts.
    """
    agent_state = AgentState(**state)
    agent_state.current_agent = "hitl_pause"
    agent_state.metadata["awaiting_human_review"] = True
    agent_state.metadata["pause_reason"] = _get_pause_reason(state)
    return agent_state.model_dump()


def _get_pause_reason(state: dict) -> str:
    """Generate human-readable reason for the pause."""
    conflicts = state.get("conflicts", [])
    requirements = state.get("requirements", [])
    reasons = []

    if conflicts:
        reasons.append(f"{len(conflicts)} conflicts detected requiring resolution")

    low_conf = [r for r in requirements if r.get("confidence", 0) < settings.hitl_confidence_threshold]
    if low_conf:
        reasons.append(f"{len(low_conf)} requirements below confidence threshold ({settings.hitl_confidence_threshold})")

    return "; ".join(reasons) if reasons else "Manual review requested"


# ─── Parallel Analysis Node ───────────────────────────────────────────────────

async def analysis_node(state: dict) -> dict:
    """Run conflict detection and prioritization concurrently.

    Both agents consume the same extracted requirements but are independent:
    conflict detection writes ``state.conflicts`` while prioritization annotates
    each requirement with ``moscow_priority``/``business_value_score``. Because
    they touch disjoint outputs, we fan them out with ``asyncio.gather`` and merge
    the results instead of running them sequentially.

    Each agent receives its own copy of the state dict (``AgentState`` is rebuilt
    from the dict inside each agent's ``__call__``), so there is no shared mutable
    state between the two concurrent tasks.
    """
    conflict = ConflictAgent()
    prioritization = PrioritizationAgent()

    conflict_result, prio_result = await asyncio.gather(
        conflict(dict(state)),
        prioritization(dict(state)),
    )

    merged = dict(state)
    # Conflicts come from the conflict agent; prioritized requirements (carrying
    # the new priority fields) come from the prioritization agent.
    merged["conflicts"] = conflict_result.get("conflicts", [])
    merged["requirements"] = prio_result.get(
        "requirements", state.get("requirements", [])
    )

    # Union of any errors raised by either branch (preserve order, dedupe).
    errors = list(state.get("errors", []))
    for err in conflict_result.get("errors", []) + prio_result.get("errors", []):
        if err not in errors:
            errors.append(err)
    merged["errors"] = errors

    # Advance progress to whichever agent reported furthest along.
    merged["progress"] = max(
        conflict_result.get("progress", 0.0),
        prio_result.get("progress", 0.0),
        state.get("progress", 0.0),
    )
    merged["current_agent"] = "analysis"
    return merged


# ─── Pipeline Builder ─────────────────────────────────────────────────────────

def build_pipeline() -> StateGraph:
    """Construct the full LangGraph StateGraph for the requirements pipeline.

    Graph structure:
        ingestion → extraction → analysis → planning → traceability → feedback → END

    The ``analysis`` node runs conflict detection and prioritization
    concurrently (they are independent), replacing the former sequential
    conflict → prioritization edges.

    The PlanningAgent uses ROM scoring (deterministic) + LLM to produce
    Feature Template-driven work items with proper E/C/U story points.

    The pipeline runs to completion without any HITL pause — the ROM
    estimate treeview is generated in the same run for final review.
    """
    # Instantiate agents
    ingestion = IngestionAgent()
    extraction = ExtractionAgent()
    planning = PlanningAgent()
    traceability = TraceabilityAgent()
    feedback = FeedbackAgent()

    # Build graph
    workflow = StateGraph(state_schema=dict)

    # Add nodes
    workflow.add_node("ingestion", ingestion)
    workflow.add_node("extraction", extraction)
    workflow.add_node("analysis", analysis_node)
    workflow.add_node("planning", planning)
    workflow.add_node("traceability", traceability)
    workflow.add_node("feedback", feedback)

    # Add edges
    workflow.set_entry_point("ingestion")
    workflow.add_edge("ingestion", "extraction")
    workflow.add_edge("extraction", "analysis")

    # After analysis, always proceed directly to planning (no HITL pause).
    workflow.add_edge("analysis", "planning")

    workflow.add_edge("planning", "traceability")
    workflow.add_edge("traceability", "feedback")
    workflow.add_edge("feedback", END)

    return workflow


def compile_pipeline():
    """Compile the pipeline graph for execution.

    Returns a compiled LangGraph that can be invoked with an initial state dict.
    """
    workflow = build_pipeline()
    return workflow.compile()


# ─── Pipeline Entry Point ─────────────────────────────────────────────────────

async def run_pipeline_async(
    session_id: str,
    chunks: list[dict],
    model_id: str | None = None,
    sprint_start: str | None = None,
    instructions: str | None = None,
) -> dict:
    """Execute the full pipeline for a session.

    Invoked from a FastAPI BackgroundTask. Initializes state with document
    chunks and runs the compiled LangGraph to completion (or HITL pause).

    ``model_id`` is the registry id of the LLM the user picked in the UI;
    threaded through ``state.metadata`` so every agent can pick it up via
    BaseAgent._apply_model. None ⇒ env default.

    ``sprint_start`` is an optional ISO ``YYYY-MM-DD`` date threaded through
    ``state.metadata`` so the PlanningAgent resolves the correct ADO sprint.

    ``instructions`` is optional free-text guidance threaded through
    ``state.metadata`` and appended to the extraction/planning system prompts.

    Returns the final pipeline state as a dict.
    """
    compiled = compile_pipeline()

    metadata: dict = {}
    if model_id:
        metadata["model_id"] = model_id
    if sprint_start:
        metadata["sprint_start"] = sprint_start
    if instructions:
        metadata["instructions"] = instructions

    initial_state = AgentState(
        session_id=session_id,
        chunks=chunks,
        metadata=metadata,
    ).model_dump()

    # Execute the graph
    final_state = await compiled.ainvoke(initial_state)
    return final_state


# ─── Resume-after-pause sub-pipeline ────────────────────────────────

def build_resume_pipeline() -> StateGraph:
    """Sub-pipeline that runs everything *after* the HITL pause.

    Used by ``/sessions/{id}/resume`` once the user has approved the
    requirements. Skips ingestion/extraction/conflict/prioritization (already
    done) and re-runs decomposition → (integration) → traceability → feedback.
    """
    planning = PlanningAgent()
    integration = IntegrationAgent()
    traceability = TraceabilityAgent()
    feedback = FeedbackAgent()

    wf = StateGraph(state_schema=dict)
    wf.add_node("planning", planning)
    wf.add_node("integration", integration)
    wf.add_node("traceability", traceability)
    wf.add_node("feedback", feedback)

    wf.set_entry_point("planning")
    wf.add_conditional_edges(
        "planning",
        should_push_to_ado,
        {"integration": "integration", "traceability": "traceability"},
    )
    wf.add_edge("integration", "traceability")
    wf.add_edge("traceability", "feedback")
    wf.add_edge("feedback", END)
    return wf


async def resume_pipeline_async(state: dict) -> dict:
    """Resume a paused pipeline from the decomposition step onward."""
    compiled = build_resume_pipeline().compile()
    return await compiled.ainvoke(state)
