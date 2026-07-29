"""Planning Agent — uses the ROM model and Feature Template to produce structured work items.

This agent replaces the generic decomposition prompt with domain-specific
planning logic. It:

1. Classifies requirements against the platform's application areas
2. Runs deterministic ROM scoring (no LLM needed for scoring)
3. Uses the ROM band to calibrate story counts and points
4. Generates Feature Template-driven work items via LLM
5. Applies E/C/U sizing guidance and naming conventions

The result is work items that match the team's established ADO patterns.
"""

import asyncio
import json
import logging
import os
from datetime import date
from pathlib import Path

from backend.app.agents.base_agent import AgentState, BaseAgent
from backend.app.core.time import utcnow

logger = logging.getLogger(__name__)

# Bundled ROM config. Overridable at runtime via the ROM_CONFIG_PATH env var.
_DEFAULT_SCORING_CONFIG_PATH = Path(__file__).parent.parent / "scoring" / "config" / "rom_config.yaml"


def _scoring_config_path() -> str:
    """Resolve the ROM config path, honoring the ROM_CONFIG_PATH env var."""
    return os.environ.get("ROM_CONFIG_PATH") or str(_DEFAULT_SCORING_CONFIG_PATH)


class PlanningAgent(BaseAgent):
    """Produces structured ADO work items using ROM scoring and Feature Template guidance.

    Pipeline position: runs AFTER prioritization (or HITL pause), BEFORE integration.
    Replaces the generic DecompositionAgent with domain-aware planning.
    """

    agent_name = "planning"

    # Hybrid model strategy: Phase 1 (Feature generation) uses the smart
    # model (Sonnet) because Feature Template mapping needs reasoning. Phase 2
    # (Story+Task decomposition) uses the FAST model (Haiku) because the
    # structure is now fixed and we want parallel speed. The bridge serializes
    # same-model calls, so using TWO different models lets Phase 1 run while
    # Phase 2's first batches queue up.
    #
    # We do NOT set use_smart_model here — the base agent will pick the fast
    # default model. We swap to the smart model for Phase 1 only, via
    # _with_smart_model() context manager below.
    use_smart_model = False

    _SYSTEM_PROMPT = """\
You are the ADO Product Planner agent. Your job is to decompose approved \
requirements into a complete Azure DevOps work item hierarchy following the \
Feature Template model.

## CRITICAL — Work Item Structure
All requirements you receive belong to the SAME feature or initiative.
You MUST produce exactly ONE Feature work item and nest everything under it:

  Feature (1 only)
    └── User Story  (several)
          └── Task        (under each story)
          └── Test Case   (under each story)

Do NOT create a separate Feature per requirement. Requirements are different \
aspects of ONE feature — group them into coherent User Stories under the single \
Feature.

## Hard Limits
- Maximum 1 Feature per response.
- Maximum {max_stories} User Stories (use ROM band guidance below).
- Maximum 5 Tasks per User Story.
- Maximum 2 Test Cases per User Story.
- Total work items in this response MUST NOT exceed {max_items}.
If you are tempted to exceed these limits, consolidate smaller items into \
broader stories or tasks instead.

## Feature Template Fields (populate for the Feature)
- Initiative Summary: short overview of business purpose and value
- Project Objective & Problem Statement: current problem → desired outcomes
- In Scope / Out of Scope
- Business Requirements (High-Level)
- Deliverables with type and expected benefit
- Risk Level (Low / Medium / High)
- Production Impact Type (Breaking / Non-Breaking / Config Change)

## Work Item Hierarchy
Feature → User Story → Task → Test Case

## User Story Format
- Title: action-oriented, linked to Feature
- As a [role], I want [goal], so that [benefit]
- Acceptance Criteria in Given / When / Then format
- Testable keys: data (tables/messages), integration points (APIs/MQ), expected outcomes

## Story Pointing (Effort + Complexity + Uncertainty)
Use modified Fibonacci: 1, 2, 3, 5, 8, 13
- Low E/C/U → 1–2
- Moderate E or C → 2–3
- Standard feature work → 3–5
- High E + C → 5–8
- Very High / Cross-Team → 8–13
Planning constant: 1 story point = 6.5 hours.

## Task Format
- Action-oriented title linked to a User Story
- Clear description of what is being done
- Explicit completion criteria
- Estimated effort (hours)

## Test Case Format
- Cover positive, negative, and edge cases
- Given / When / Then
- Include data conditions and expected outcomes

## Naming Convention
- Feature: "QA Testing: <Feature Name>"
- User Story: "<Action> for Feature <Feature ID or Name>"
- Task: "<Verb> <what>" (e.g., "Build test data matrix")

## Safety Guardrails
- Create-only: never update/delete existing items
- Leave User Stories and Tasks unassigned unless explicitly requested
- Flag any story ≥ 8 points for splitting review

## ROM Band Context
The requirements have been scored with ROM band: {rom_band} (score: {rom_score}).
Calibrate the TOTAL number of stories accordingly:
- Small (< 6): 1–3 stories total
- Medium (6–12): 3–6 stories total
- Large (12–20): 6–10 stories total
- Extra Large (20+): 10–15 stories total
These are TOTALS across ALL requirements, not per-requirement.

## Suggested Testing Story & Task Counts
Use these tables to calibrate QA / testing story and task volumes:

Story count by test scope:
- Functional only: 1 story
- Functional + targeted regression: 2 stories
- Functional + full regression: 2–3 stories
- Functional + performance: 3–5 stories
- Functional + performance + UAT: 3–5 stories

Typical task count per testing story type:
- Prepare Test Plan / Scripts: 1–3 tasks
- Setup Environment: 1 task
- Execute System Test: 1–2 tasks
- Execute Cross-System Test: 1 task
- Execute Performance Test: 2–3 tasks
- Signoff: 1 task

Always respond with a JSON array of work items. Each item must have:
work_item_type, title, description, acceptance_criteria (array), parent_ref, \
priority (1-4), story_points, effort_hours, tags (array), source_requirement_ids (array).

Field guidance:
- story_points: modified-Fibonacci E/C/U estimate for User Stories ONLY (1, 2, 3, 5, 8, 13).
  Features must have story_points: null (they use tshirt_size instead).
  Tasks must have story_points: null (they use effort_hours instead).
- effort_hours: estimated hours of work for Tasks ONLY. This is the concrete
  effort estimate for each Task. User Stories should NOT have effort_hours
  (their effort is implied by story_points × 6.5).
- tshirt_size: ONLY for Features (S/M/L/XL based on ROM band).
"""

    # ROM-band → max story count (used in prompt + hard cap).
    _BAND_MAX_STORIES = {"small": 3, "medium": 6, "large": 10, "extra_large": 15}

    async def execute(self, state: AgentState) -> AgentState:
        """Generate work items using ROM-calibrated planning."""
        from backend.app.core.config import settings as _settings

        started_at = utcnow()
        total_input_tokens = 0
        total_output_tokens = 0

        approved_reqs = [
            r for r in state.requirements
            if r.get("status") == "approved"
            or r.get("confidence", 0) >= 0.7
        ]

        if not approved_reqs:
            state.progress = 0.71
            await self.log_run(
                session_id=state.session_id,
                status="completed",
                confidence=1.0,
                started_at=started_at,
            )
            return state

        # Step 1: ROM scoring (deterministic — no LLM)
        sprint_start = state.metadata.get("sprint_start")
        rom_score, rom_band, slices, sprint = self._score_requirements(
            approved_reqs, sprint_start=sprint_start
        )
        state.metadata["rom_score"] = rom_score
        state.metadata["rom_band"] = rom_band
        state.metadata["rom_slices"] = slices
        if sprint:
            state.metadata["sprint"] = sprint

        # Derive hard limits from ROM band + config.
        max_stories = self._BAND_MAX_STORIES.get(rom_band, 6)
        max_items = min(
            _settings.max_work_items,
            # 1 Feature + stories + up to 5 tasks + 2 tests per story
            1 + max_stories * (1 + 5 + 2),
        )

        # ── Hybrid model planning: Sonnet for the Feature, Haiku for the
        # Story+Task decomposition. Because the VS Code bridge serializes
        # same-model calls, splitting work across TWO models lets Sonnet (1
        # call) run in parallel with Haiku (N calls).
        domain_knowledge = self.load_prompt("domain_knowledge_v1")
        user_instructions = (state.metadata or {}).get("instructions")

        all_work_items: list[dict] = []
        total_input_tokens = 0
        total_output_tokens = 0
        _token_totals = {"input": 0, "output": 0}

        async def _call_with_model(
            model_name: str,
            system_prompt: str,
            user_message: str,
            max_tokens: int = 8192,
            temperature: float = 0.2,
        ) -> str:
            """Issue one LLM call against a SPECIFIC model id (overrides the
            agent's default for this call only). Restores the previous model
            so other in-flight calls in this run aren't disturbed.
            """
            from dataclasses import replace as _dc_replace
            prev_model = self._model
            try:
                self._model = _dc_replace(prev_model, model=model_name)
                response_text, in_tok, out_tok = await self.call_llm(
                    system_prompt=system_prompt,
                    user_message=user_message,
                    max_tokens=max_tokens,
                    temperature=temperature,
                )
                _token_totals["input"] += in_tok
                _token_totals["output"] += out_tok
                return response_text
            finally:
                self._model = prev_model

        # ── Phase 1: Feature generation (Sonnet, single call) ──────────────
        smart_model = _settings.llm_model_smart or _settings.llm_model
        fast_model = _settings.llm_model

        reqs_summary = "\n".join(
            f"- [{r.get('id', '?')}] {r.get('statement', '')[:200]}"
            for r in approved_reqs
        )

        feature_system_prompt = (
            "# Permanent Domain Knowledge (always applies)\n\n"
            + domain_knowledge
            + "\n\n# Phase 1 — Feature Template Mapping (this call)\n"
            f"Generate exactly ONE Feature work item for the {len(approved_reqs)} approved\n"
            "requirements. The Feature is the SINGLE root of the work item tree. Apply\n"
            "the Feature Template (Initiative Summary, Project Objective,\n"
            "Scope, BRs, Deliverables, Impacted Systems, Risk, Owners, E2E Contacts).\n\n"
            f"ROM context: band={rom_band}, total_score={rom_score}, "
            f"impacted_slices={json.dumps(slices)}\n\n"
            "Output ONLY one JSON object with these fields:\n"
            "  work_item_type: \"Feature\"\n"
            "  title: \"QA Testing: <Feature Name>\"\n"
            "  description: full Feature Template mapping as markdown bullets\n"
            "  acceptance_criteria: [list of feature-level AC]\n"
            "  tshirt_size: \"S\" | \"M\" | \"L\" | \"XL\" (based on ROM band: small→S, medium→M, large→L, xlarge→XL)\n"
            "  story_points: null\n"
            "  tags: [\"qa-testing\", ...]\n"
            "  source_requirement_ids: [all req ids covered]\n"
            "Respond with the JSON object only — no markdown fences, no prose."
        )
        if user_instructions:
            feature_system_prompt += (
                f"\n\n## Reviewer Guidance\n{user_instructions}"
            )

        feature_user_message = (
            "Requirements covered by this Feature (use these IDs verbatim in "
            "source_requirement_ids):\n"
            f"{reqs_summary}"
        )

        # ── Phase 2 (Haiku, single call) — all requirements in one prompt so the
        # LLM sees the full picture and respects the global max_stories limit.
        # Batching previously caused each batch to independently generate up
        # to max_stories, multiplying the total well beyond the ROM band cap.

        def _build_story_prompt() -> str:
            return (
                "# Permanent Domain Knowledge (always applies)\n\n"
                + domain_knowledge
                + "\n\n# Phase 2 — Story/Task/TestCase Decomposition (this call)\n"
                "The Feature has been generated in Phase 1. Your job is to produce ONLY\n"
                "User Story + Task + Test Case work items for ALL the requirements provided\n"
                "below. DO NOT produce a Feature — only stories and their children.\n\n"
                "CRITICAL: Every User Story MUST have at least 2 Tasks and 1 Test Case.\n"
                "Tasks are action-oriented work units (e.g. 'Build test data matrix',\n"
                "'Configure environment', 'Implement validation logic'). Do NOT skip them.\n\n"
                "Apply the default QA story package: Prepare Test Plan, Setup\n"
                "System Test Env, Execute System Test Cycle 1, System Test Signoff,\n"
                "and PERF stories when triggered. Include the mandatory DoR and DoD\n"
                "stories. Use Given/When/Then for Acceptance Criteria.\n\n"
                "Each Story uses parent_ref = \"FEATURE_ROOT\" so the persistence\n"
                "layer can link them to the single Feature root produced in Phase 1.\n"
                "Each Task and Test Case MUST set parent_ref to the EXACT title of its\n"
                "parent User Story (verbatim, case-sensitive). This is how the hierarchy\n"
                "is built — if parent_ref doesn't match a Story title the item is lost.\n\n"
                f"ROM context: band={rom_band}, total_score={rom_score}\n"
                f"Hard limit: produce AT MOST {max_stories} User Stories TOTAL.\n"
                "Group related requirements into coherent stories rather than creating\n"
                "one story per requirement. Keep descriptions to 1-2 concise sentences.\n\n"
                "Each work item must have: work_item_type, title, description,\n"
                "acceptance_criteria (array), parent_ref, priority (1-4),\n"
                "story_points (for Stories only, null for Tasks/TestCases),\n"
                "effort_hours (for Tasks only, null for Stories/TestCases),\n"
                "tags (array), source_requirement_ids (array).\n"
                "Use the EXACT req ids supplied — do not invent identifiers.\n"
                "Respond with a JSON array of work items only."
            )

        story_system_prompt = _build_story_prompt()
        if user_instructions:
            story_system_prompt += (
                f"\n\n## Reviewer Guidance\n{user_instructions}"
            )

        reqs_json = json.dumps(approved_reqs, indent=2, default=str)
        story_user_message = (
            f"Decompose these {len(approved_reqs)} requirements into Stories/Tasks/"
            "TestCases (NO Feature — the Feature was made in Phase 1):\n\n"
            f"{reqs_json}\n\n"
            "Each item's source_requirement_ids must come VERBATIM from the\n"
            "`id` field above. Each Story's parent_ref must be \"FEATURE_ROOT\".\n"
            "Each Task/TestCase's parent_ref must be the EXACT title of its parent Story."
        )

        logger.info(
            "Planning hybrid: Phase 1 Feature (Sonnet=%s) + Phase 2 Stories "
            "(Haiku=%s) — max %d stories (ROM band=%s)",
            smart_model, fast_model, max_stories, rom_band,
        )

        # Fan out Phase 1 (Sonnet) + Phase 2 (Haiku) concurrently. The bridge
        # serializes within each model queue but the two models run in parallel.
        results = await asyncio.gather(
            _call_with_model(
                smart_model, feature_system_prompt, feature_user_message,
                max_tokens=4096, temperature=0.2,
            ),
            _call_with_model(
                fast_model, story_system_prompt, story_user_message,
                max_tokens=8192, temperature=0.2,
            ),
            return_exceptions=True,
        )
        total_input_tokens = _token_totals["input"]
        total_output_tokens = _token_totals["output"]

        # First result is the Phase 1 Feature; second is Phase 2 Stories.
        feature_result = results[0]
        story_result = results[1]

        # ── Parse Phase 1 Feature ──────────────────────────────────────────
        feature_local_id = "FEATURE_ROOT"
        if isinstance(feature_result, Exception):
            state.errors.append(f"Phase 1 (Feature) crashed: {feature_result}")
            logger.error("Phase 1 Feature crashed", exc_info=feature_result)
        else:
            try:
                feature_obj = self.parse_json_response(feature_result)
                # Accept either a single object or [obj]
                if isinstance(feature_obj, list) and feature_obj:
                    feature_obj = feature_obj[0]
                if isinstance(feature_obj, dict):
                    feature_obj["work_item_type"] = "Feature"
                    feature_obj["_local_id"] = feature_local_id
                    feature_obj["parent_id"] = None
                    feature_obj["parent_ref"] = None
                    all_work_items.append(feature_obj)
                    logger.info(
                        "Phase 1 Feature: %r", feature_obj.get("title", "")[:80]
                    )
                else:
                    state.errors.append(
                        f"Phase 1: expected Feature object, got {type(feature_obj).__name__}"
                    )
            except (json.JSONDecodeError, ValueError) as exc:
                snippet = (feature_result or "")[:500]
                state.errors.append(
                    f"Phase 1 JSON parse failed: {exc}; "
                    f"response starts with: {snippet!r}"
                )
                logger.error(
                    "Phase 1 JSON parse failed: %s\nResponse: %s", exc, snippet
                )

        # ── Parse Phase 2 stories and link them to the Feature ──────────────
        if isinstance(story_result, Exception):
            state.errors.append(f"Phase 2 (Stories) crashed: {story_result}")
            logger.error("Phase 2 Stories crashed", exc_info=story_result)
        else:
            try:
                wis = self.parse_json_response(story_result)
                if not isinstance(wis, list):
                    state.errors.append(
                        f"Phase 2: expected list, got {type(wis).__name__}"
                    )
                else:
                    # Rewrite "FEATURE_ROOT" parent_ref so _resolve_hierarchy
                    # later links each Story directly to the single Feature.
                    for wi in wis:
                        if (wi.get("parent_ref") or "").upper() in (
                            "FEATURE_ROOT", "FEATURE", ""
                        ) and (wi.get("work_item_type") or "") == "User Story":
                            wi["_phase2_root"] = True
                    all_work_items.extend(wis)
                    logger.info(
                        "Phase 2 Stories (Haiku): parsed %d items", len(wis)
                    )
            except (json.JSONDecodeError, ValueError) as exc:
                snippet = (story_result or "")[:500]
                state.errors.append(
                    f"Phase 2 JSON parse failed: {exc}; "
                    f"response starts with: {snippet!r}"
                )
                logger.error(
                    "Phase 2 JSON parse failed: %s\nResponse: %s", exc, snippet
                )

        # Enforce the ROM band story limit post-hoc. If the LLM still produced
        # more User Stories than max_stories, keep only the first max_stories
        # and their children.
        stories = [wi for wi in all_work_items if wi.get("work_item_type") == "User Story"]
        if len(stories) > max_stories:
            excess = len(stories) - max_stories
            # Keep the first max_stories stories; drop the rest + their children
            stories_to_drop = {id(s) for s in stories[max_stories:]}
            drop_titles = {s.get("title") for s in stories[max_stories:]}
            all_work_items = [
                wi for wi in all_work_items
                if id(wi) not in stories_to_drop
                and wi.get("parent_ref") not in drop_titles
            ]
            state.errors.append(
                f"Planning guardrail: trimmed {excess} excess User Stories "
                f"to stay within ROM band limit of {max_stories}."
            )
            logger.warning(
                "Trimmed %d excess stories to respect ROM band %s max of %d",
                excess, rom_band, max_stories,
            )

        # Resolve parent links: assign local ids, then map every Phase 2 root
        # Story (parent_ref FEATURE_ROOT) to the single Feature root.
        state.work_items = self._resolve_hierarchy(all_work_items)
        for wi in state.work_items:
            if wi.pop("_phase2_root", False):
                wi["parent_id"] = feature_local_id

        self._apply_effort_estimates(state.work_items)

        # Hard cap — trim deepest items (Tasks/Test Cases first) if the LLM
        # over-generated despite prompt constraints.
        if len(state.work_items) > _settings.max_work_items:
            type_priority = {"Feature": 0, "User Story": 1, "Task": 2, "Test Case": 3}
            state.work_items.sort(
                key=lambda wi: type_priority.get(wi.get("work_item_type", ""), 99)
            )
            trimmed = len(state.work_items) - _settings.max_work_items
            state.work_items = state.work_items[:_settings.max_work_items]
            state.errors.append(
                f"Planning guardrail: trimmed {trimmed} excess work items "
                f"to stay within the {_settings.max_work_items} cap."
            )

        state.progress = 0.71

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

    def _score_requirements(
        self,
        requirements: list[dict],
        sprint_start: str | date | None = None,
    ) -> tuple[float, str, list[dict], dict]:
        """Run deterministic ROM scoring on requirements.

        Classifies requirements into impacted slices based on keyword matching,
        then scores using the ROM engine weights and multipliers.

        ``sprint_start`` optionally overrides the date used for sprint
        resolution (threaded from session metadata).

        Returns (total_score, rom_band, scored_slices, sprint).
        """
        try:
            from backend.app.scoring.rom_engine import load_config, run as run_scoring

            config = load_config(_scoring_config_path())

            # Classify requirements into slices based on content keywords
            slices_input = self._classify_into_slices(requirements, config)

            if not slices_input:
                return (0.0, "small", [], {})

            result = run_scoring(slices_input, config, sprint_start=sprint_start)
            return (
                result.get("total_score", 0.0),
                result.get("rom_band", "small"),
                result.get("slices", []),
                result.get("sprint", {}),
            )
        except Exception:
            # If scoring fails, fall back to a default
            return (0.0, "medium", [], {})

    def _classify_into_slices(self, requirements: list[dict], config: dict) -> list[dict]:
        """Map requirements to ROM slices using keyword matching.

        Prefers the ``classification_to_slice`` mapping from rom_config.yaml
        (authoritative, team-maintainable). Falls back to a small built-in
        keyword map only if the config section is missing.
        """
        base_weights = config.get("base_weights", {})
        valid_slices = set(base_weights.keys())

        # Collect all requirement text for matching
        all_text = " ".join(
            (r.get("statement", "") + " " + (r.get("category", "") or "")).lower()
            for r in requirements
        )

        # Build slice -> matched? from config-driven classification mapping.
        matched_slice_names: set[str] = set()
        classification = config.get("classification_to_slice") or {}
        for entry in classification.values():
            phrases = entry.get("match_phrases", []) if isinstance(entry, dict) else []
            slices = entry.get("slices", []) if isinstance(entry, dict) else []
            if any(str(p).lower() in all_text for p in phrases):
                matched_slice_names.update(slices)

        # Fallback to built-in keyword map if config provided no classification.
        if not classification:
            for slice_name, keywords in self._FALLBACK_KEYWORD_MAP.items():
                if any(kw.lower() in all_text for kw in keywords):
                    matched_slice_names.add(slice_name)

        matched_slices: list[dict] = []
        for slice_name in matched_slice_names:
            if slice_name not in valid_slices:
                continue
            matched_slices.append({
                "slice": slice_name,
                "change_type": "multi_rule_or_multi_field",
                "dependencies": "one_dependency",
                "test_breadth": "functional_plus_targeted_regression",
                "data_env": "moderate_setup_or_conditioning",
            })

        # If no specific matches, default to a generic slice
        if not matched_slices:
            matched_slices.append({
                "slice": "EDC",
                "change_type": "single_rule_or_single_path",
                "dependencies": "none",
                "test_breadth": "functional_only",
                "data_env": "minimal_existing_setup",
            })

        return matched_slices

    # Used only when rom_config.yaml has no classification_to_slice section.
    _FALLBACK_KEYWORD_MAP = {
        "EDC": ["data capture", "shipment data", "validation", "inbound data", "schema"],
        "ODC": ["image", "workflow", "queue", "scan upload", "correction"],
        "IDC": ["key entry", "edit", "correction", "maintenance", "UI", "screen"],
        "MSG": ["messaging", "notification", "distribution", "feed", "outbound"],
        "REG": ["regulatory", "compliance", "filing", "manifest", "customs"],
        "SCAN_STS": ["scan", "status", "intercept", "hold", "release", "tracking"],
        "BRE_SEC": ["rule", "security", "screening", "validation rule", "business rule"],
        "RPT": ["report", "dashboard", "analytics", "visibility", "monitoring"],
        "INFRA": ["infrastructure", "deployment", "environment", "platform", "upgrade"],
        "PERF": ["performance", "throughput", "latency", "SLA", "load test", "scalability"],
    }

    def _resolve_hierarchy(self, work_items: list[dict]) -> list[dict]:
        """Resolve parent_ref strings into parent_id relationships.

        Preserves any pre-existing ``_local_id`` (e.g. the synthetic
        ``FEATURE_ROOT`` we set on the Phase 1 Feature). This lets Phase 2
        Story batches link to the Feature via a stable identifier without
        relying on title matching.
        """
        title_to_idx: dict[str, int] = {}
        for idx, item in enumerate(work_items):
            title_to_idx[item.get("title", "")] = idx
            # Honor a pre-assigned _local_id (Phase 1 Feature uses FEATURE_ROOT
            # so Phase 2 can reference it). Otherwise auto-number.
            if not item.get("_local_id"):
                item["_local_id"] = f"WI-{idx:04d}"
            if item.get("parent_id") is None:
                item["parent_id"] = None

        for item in work_items:
            parent_ref = item.get("parent_ref", "")
            if parent_ref and parent_ref in title_to_idx:
                parent_idx = title_to_idx[parent_ref]
                item["parent_id"] = work_items[parent_idx]["_local_id"]

        # Rescue orphan Tasks/TestCases whose parent_ref didn't match any title.
        # Attach them to the first User Story (better than leaving them as roots).
        first_story_id: str | None = None
        for item in work_items:
            if item.get("work_item_type") == "User Story":
                first_story_id = item["_local_id"]
                break

        if first_story_id:
            for item in work_items:
                wtype = item.get("work_item_type", "")
                if wtype in ("Task", "Test Case") and item.get("parent_id") is None:
                    # Try fuzzy match: find closest story title
                    ref = (item.get("parent_ref") or "").strip().lower()
                    best_id = first_story_id  # fallback
                    if ref and ref not in ("feature_root", "feature", ""):
                        for wi in work_items:
                            if wi.get("work_item_type") == "User Story":
                                title = (wi.get("title") or "").strip().lower()
                                if ref in title or title in ref:
                                    best_id = wi["_local_id"]
                                    break
                    item["parent_id"] = best_id
                    logger.debug(
                        "Rescued orphan %s '%s' → parent %s",
                        wtype, item.get('title', '?')[:40], best_id,
                    )

        return work_items

    def _apply_effort_estimates(self, work_items: list[dict]) -> None:
        """Enforce sizing rules and distribute hours from story points to tasks.

        Uses the ``story_point_hours`` planning constant from rom_config.yaml
        (default 6.5h/point). Mutates each work item in place.

        Rules:
        - Features: no story_points, no effort_hours (use tshirt_size)
        - User Stories: story_points only, no effort_hours
        - Tasks: effort_hours only (parent story pts × 6.5 ÷ number of tasks)
        - Test Cases: no sizing
        """
        try:
            from backend.app.scoring.rom_engine import load_config

            config = load_config(_scoring_config_path())
            sph = float(config.get("story_point_hours", 6.5))
        except Exception:
            sph = 6.5

        # Build a map of story → its child tasks for hour distribution
        # Items reference parents via _local_id or title
        story_map: dict[str, dict] = {}
        task_groups: dict[str, list[dict]] = {}  # story_key → [tasks]

        for item in work_items:
            wi_type = item.get("work_item_type", "")
            key = item.get("_local_id") or item.get("title", "")
            if "Story" in wi_type:
                story_map[key] = item

        for item in work_items:
            wi_type = item.get("work_item_type", "")
            if "Task" in wi_type:
                parent_key = item.get("parent_ref") or item.get("parent_id") or ""
                task_groups.setdefault(parent_key, []).append(item)

        # Apply rules
        for item in work_items:
            wi_type = item.get("work_item_type", "")

            if "Feature" in wi_type:
                item["story_points"] = None
                item["effort_hours"] = None
            elif "Story" in wi_type:
                item["effort_hours"] = None
            elif "Task" in wi_type:
                item["story_points"] = None
                # Hours will be set below per-group
            elif "Test" in wi_type:
                item["story_points"] = None
                item["effort_hours"] = None

        # Distribute story hours evenly across child tasks
        for story_key, story in story_map.items():
            pts = story.get("story_points")
            if not isinstance(pts, (int, float)) or pts <= 0:
                continue
            total_hrs = pts * sph
            tasks = task_groups.get(story_key, [])
            if not tasks:
                continue
            hrs_per_task = round(total_hrs / len(tasks), 1)
            for task in tasks:
                task["effort_hours"] = hrs_per_task

