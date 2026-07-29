"""Integration Agent — pushes work items to Azure DevOps via the ADO MCP server."""

from __future__ import annotations

import base64
import json
import logging
import time
from urllib.parse import quote

import httpx
from sqlalchemy import select

from backend.app.agents.base_agent import AgentState, BaseAgent
from backend.app.core.config import settings
from backend.app.core.database import async_session_factory
from backend.app.core.time import utcnow
from backend.app.models import ADOPushLog

logger = logging.getLogger(__name__)


class IntegrationAgent(BaseAgent):
    """Pushes finalized work items to Azure DevOps via the REST API.

    Talks directly to the Azure DevOps REST API (``_apis/wit/workitems``) using
    a Personal Access Token (HTTP Basic auth). **One httpx.AsyncClient is reused
    for the whole batch** so the connection pool is shared across every create
    call instead of reconnecting per item.

    Operations:
    - Create Epics, Features, User Stories, Tasks, Test Cases (in that order)
    - Set parent-child links between work items
    - Skip items already pushed (dedup via ado_push_log)
    - Full audit logging to the ado_push_log table
    """

    agent_name = "integration"

    def __init__(
        self,
        ado_org_url: str | None = None,
        ado_pat: str | None = None,
        ado_project: str | None = None,
    ) -> None:
        super().__init__()
        # Per-instance credential overrides — supplied by the API when the
        # frontend sends per-session ADO config. Empty values fall back to .env.
        self._ado_org_url = (ado_org_url or settings.ado_org_url).strip()
        self._ado_pat = (ado_pat or settings.ado_pat).strip()
        self._ado_project = (ado_project or settings.ado_project).strip()
        # Populated per-run in execute() from ROM config + resolved sprint.
        self._ado_path_map: dict = {}
        # Planning constant (hours per story point); refreshed from config per run.
        self._story_point_hours: float = 6.5

    async def execute(self, state: AgentState) -> AgentState:
        """Push all work items to Azure DevOps via the REST API (PAT auth)."""
        started_at = utcnow()

        # Resolve ADO area/iteration paths. Epics & Features are PI/release-level
        # containers that span multiple sprints, so they get the PI-level path —
        # NOT the selected sprint. Only Stories/Tasks/Test Cases are bound to the
        # chosen sprint iteration.
        self._ado_path_map = self._resolve_ado_paths(state)

        work_items = state.work_items
        if not work_items:
            state.progress = 0.9
            await self.log_run(
                session_id=state.session_id,
                status="completed",
                confidence=1.0,
                started_at=started_at,
            )
            return state

        # Dedup: any work item that already has a successful push log entry
        # for this session should not be created again. We seed the
        # local-id → ado-id map from those log rows so children can still be
        # parented correctly without re-creating the parent.
        already_pushed = await self._load_existing_pushes(state.session_id)
        skipped_count = 0
        pushed_count = 0
        failed_count = 0

        # Process work items in hierarchical order: Epics first, then Features…
        # Single O(n) pass: bucket items by type, then concatenate in order.
        # Anything with an unrecognised type is pushed last so we never silently
        # drop work items because of a typo in the LLM output.
        type_order = ["Epic", "Feature", "User Story", "Task", "Test Case"]
        known_types = set(type_order)
        buckets: dict[str, list[dict]] = {t: [] for t in type_order}
        unknown: list[dict] = []
        for wi in work_items:
            wi_type = wi.get("work_item_type")
            if wi_type in known_types:
                buckets[wi_type].append(wi)
            else:
                unknown.append(wi)

        ordered_items: list[dict] = []
        for wi_type in type_order:
            ordered_items.extend(buckets[wi_type])
        ordered_items.extend(unknown)

        local_id_to_ado_id: dict[str, int] = dict(already_pushed)

        # Guard: without an org URL + PAT we cannot reach Azure DevOps. Log
        # every item as a failure rather than raising — partial progress and a
        # clear audit trail are still useful for the operator.
        if not self._ado_org_url or not self._ado_pat:
            for item in ordered_items:
                await self._log_push(
                    session_id=state.session_id,
                    work_item_id=item.get("_local_id"),
                    tool_name="ado_rest_create_work_item",
                    payload=self._build_payload(item),
                    response=None,
                    ado_work_item_id=None,
                    success=False,
                    error_message="ADO credentials not configured (ADO_ORG_URL / ADO_PAT)",
                    latency_ms=0,
                )
            state.metadata["ado_push_summary"] = {
                "pushed": 0,
                "failed": len(ordered_items),
                "skipped": 0,
                "total": len(work_items),
            }
            return state

        # Azure DevOps PAT auth is HTTP Basic with an empty username.
        auth_header = "Basic " + base64.b64encode(f":{self._ado_pat}".encode()).decode()
        org_base = self._ado_org_url.rstrip("/")

        # One AsyncClient is reused for the whole batch. If the client cannot be
        # constructed we log every remaining item as a failure rather than raising.
        try:
            async with httpx.AsyncClient(
                timeout=settings.llm_request_timeout,
                headers={"Authorization": auth_header},
            ) as client:
                # Load the project's real area/iteration trees so we can drop
                # any configured path that doesn't exist (ADO rejects unknown
                # paths with TF401347 and fails the whole work item).
                self._valid_area_paths, self._valid_iteration_paths = (
                    await self._load_valid_paths(client, org_base, self._ado_project)
                )

                # PARALLELIZE within each hierarchical TIER: items inside the
                # same tier (e.g. all Stories) are independent of each other,
                # but every tier needs its parent tier's ADO IDs resolved
                # first to set parent_id correctly. So we await all of
                # tier-N concurrently, then move on to tier-N+1.
                import asyncio as _asyncio
                from collections import defaultdict
                tier_items: dict[str, list[dict]] = defaultdict(list)
                for item in ordered_items:
                    tier_items[item.get("work_item_type") or ""].append(item)

                tier_order = ["Epic", "Feature", "User Story", "Task", "Test Case"]
                # Anything not in the standard tiers gets pushed at the end,
                # one tier per unknown type so type-internal parallelism is
                # preserved without crossing tiers.
                for unknown_type in (
                    set(tier_items) - set(tier_order) - {""}
                ):
                    tier_order.append(unknown_type)
                if "" in tier_items:
                    tier_order.append("")

                async def _push_one(item: dict) -> None:
                    nonlocal pushed_count, skipped_count, failed_count
                    local_id = item.get("_local_id") or item.get("id") or ""
                    if local_id and local_id in already_pushed:
                        item["ado_work_item_id"] = already_pushed[local_id]
                        item["pushed_to_ado"] = True
                        skipped_count += 1
                        return

                    start_time = time.time()
                    payload = self._build_payload(item)
                    parent_local = item.get("parent_id") or item.get("parent_local_id")
                    if parent_local and parent_local in local_id_to_ado_id:
                        payload["parent_id"] = local_id_to_ado_id[parent_local]

                    try:
                        ado_id, response_json = await self._create_work_item_rest(
                            client, org_base, payload
                        )
                        latency_ms = int((time.time() - start_time) * 1000)

                        if ado_id:
                            local_id_to_ado_id[local_id] = ado_id
                            item["ado_work_item_id"] = ado_id
                            item["pushed_to_ado"] = True
                            pushed_count += 1
                            await self._log_push(
                                session_id=state.session_id,
                                work_item_id=local_id or None,
                                tool_name="ado_rest_create_work_item",
                                payload=payload,
                                response={"id": ado_id},
                                ado_work_item_id=ado_id,
                                success=True,
                                latency_ms=latency_ms,
                            )
                        else:
                            failed_count += 1
                            await self._log_push(
                                session_id=state.session_id,
                                work_item_id=local_id or None,
                                tool_name="ado_rest_create_work_item",
                                payload=payload,
                                response=response_json,
                                ado_work_item_id=None,
                                success=False,
                                error_message="ADO REST returned no work item id",
                                latency_ms=latency_ms,
                            )
                    except Exception as e:
                        latency_ms = int((time.time() - start_time) * 1000)
                        failed_count += 1
                        logger.exception(
                            "ADO push failed for work item %s in session %s",
                            local_id, state.session_id,
                        )
                        await self._log_push(
                            session_id=state.session_id,
                            work_item_id=local_id or None,
                            tool_name="ado_rest_create_work_item",
                            payload=payload,
                            response=None,
                            ado_work_item_id=None,
                            success=False,
                            error_message=str(e)[:500],
                            latency_ms=latency_ms,
                        )

                # Drive each tier concurrently. Cap fan-out at 10 to be
                # gentle on the ADO REST quota even on huge plans.
                semaphore = _asyncio.Semaphore(10)

                async def _push_one_bounded(item: dict) -> None:
                    async with semaphore:
                        await _push_one(item)

                for tier in tier_order:
                    items_in_tier = tier_items.get(tier, [])
                    if not items_in_tier:
                        continue
                    logger.info(
                        "ADO push tier '%s': %d items in parallel",
                        tier or "unknown", len(items_in_tier),
                    )
                    await _asyncio.gather(
                        *(_push_one_bounded(it) for it in items_in_tier),
                        return_exceptions=False,
                    )
        except Exception as e:
            # Client construction failed before the loop started.
            logger.exception("ADO REST client failed to start for session %s", state.session_id)
            await self._log_push(
                session_id=state.session_id,
                work_item_id=None,
                tool_name="ado_rest_create_work_item",
                payload={"_phase": "client_init"},
                response=None,
                ado_work_item_id=None,
                success=False,
                error_message=f"ADO REST client unavailable: {e}",
                latency_ms=0,
            )
            failed_count += max(len(ordered_items) - pushed_count - skipped_count, 0)

        state.work_items = work_items
        state.progress = 0.9
        state.metadata["ado_push_summary"] = {
            "pushed": pushed_count,
            "failed": failed_count,
            "skipped": skipped_count,
            "total": len(work_items),
        }

        await self.log_run(
            session_id=state.session_id,
            status="completed" if failed_count == 0 else "completed_with_errors",
            confidence=pushed_count / max(len(work_items) - skipped_count, 1),
            input_hash=self.compute_hash(work_items),
            token_input=0,
            token_output=0,
            started_at=started_at,
        )

        return state

    @staticmethod
    async def _load_existing_pushes(session_id: str) -> dict[str, int]:
        """Return ``{local_work_item_id: ado_id}`` for every successful push.

        Used to skip work items that have already made it across so reruns
        don't create duplicates in ADO.
        """
        out: dict[str, int] = {}
        async with async_session_factory() as db:
            rows = (
                await db.execute(
                    select(
                        ADOPushLog.work_item_id,
                        ADOPushLog.ado_work_item_id,
                    ).where(
                        ADOPushLog.session_id == session_id,
                        ADOPushLog.success.is_(True),
                        ADOPushLog.ado_work_item_id.is_not(None),
                    )
                )
            ).all()
        for local_id, ado_id in rows:
            if local_id and ado_id:
                out[local_id] = int(ado_id)
        return out

    def _resolve_ado_paths(self, state: AgentState) -> dict:
        """Resolve area/iteration paths per work-item level from ROM config + state.

        Returns a mapping keyed by a coarse level:
            - "container"  → Epic / Feature  (PI-level, spans sprints)
            - "sprint"     → User Story / Task / Test Case (bound to the sprint)

        The container iteration path comes from ``ado_paths.feature_iteration_path``
        (a release/PI-level node). The sprint iteration path is the one the
        PlanningAgent resolved into ``state.metadata["sprint"]`` (falling back to
        the config default if planning didn't run or scoring failed).
        """
        try:
            from backend.app.agents.planning_agent import _scoring_config_path
            from backend.app.scoring.rom_engine import load_config

            config = load_config(_scoring_config_path())
            ado_paths = config.get("ado_paths", {}) or {}
            # Cache the planning constant so _build_payload can derive task hours.
            self._story_point_hours = float(config.get("story_point_hours", 6.5))
        except Exception:
            ado_paths = {}

        feature_iteration = ado_paths.get("feature_iteration_path")
        feature_area = ado_paths.get("feature_area_path")
        story_task_area = ado_paths.get("story_task_area_path")

        # Sprint iteration path resolved by the PlanningAgent (may be absent).
        sprint = state.metadata.get("sprint") or {}
        sprint_iteration = sprint.get("iteration_path")

        return {
            "container": {
                "iteration_path": feature_iteration,
                "area_path": feature_area,
            },
            "sprint": {
                # Stories/Tasks live in the selected sprint; fall back to the
                # feature-level node only if no sprint could be resolved.
                "iteration_path": sprint_iteration or feature_iteration,
                "area_path": story_task_area,
            },
        }

    @staticmethod
    async def _load_valid_paths(
        client: httpx.AsyncClient, org_base: str, project: str
    ) -> tuple[set[str], set[str]]:
        """Return the set of valid area paths and iteration paths for a project.

        Work-item field values use the form ``Project\\Node\\Child`` (no
        ``Area``/``Iteration`` segment). Azure DevOps rejects any path that
        isn't a real classification node, so we pre-load both trees and later
        drop configured paths that aren't present.
        """

        def collect(node: dict, parent: str, out: set[str]) -> None:
            name = node.get("name", "")
            full = name if not parent else f"{parent}\\{name}"
            out.add(full)
            for child in node.get("children", []) or []:
                collect(child, full, out)

        areas: set[str] = set()
        iterations: set[str] = set()
        try:
            base = f"{org_base}/{quote(project)}/_apis/wit/classificationnodes"
            ar = await client.get(f"{base}/areas?$depth=10&api-version=7.1")
            if ar.status_code < 400:
                collect(ar.json(), "", areas)
            ir = await client.get(f"{base}/iterations?$depth=10&api-version=7.1")
            if ir.status_code < 400:
                collect(ir.json(), "", iterations)
        except Exception:
            logger.warning("Could not pre-load ADO classification nodes; "
                           "paths will be sent unvalidated")
        return areas, iterations

    @staticmethod
    async def _create_work_item_rest(
        client: httpx.AsyncClient, org_base: str, payload: dict
    ) -> tuple[int | None, dict | None]:
        """Create a single work item via the ADO REST API.

        Returns ``(ado_id, response_json)``. ``ado_id`` is ``None`` when the
        response did not contain an id.
        """
        work_item_type = payload.get("work_item_type", "User Story")
        project = payload.get("project", "")
        # POST .../{project}/_apis/wit/workitems/${type}?api-version=7.1
        url = (
            f"{org_base}/{quote(project)}/_apis/wit/workitems/"
            f"${quote(work_item_type)}?api-version=7.1"
        )

        document = IntegrationAgent._build_patch_document(payload, org_base)
        response = await client.post(
            url,
            content=json.dumps(document),
            headers={"Content-Type": "application/json-patch+json"},
        )
        if response.status_code >= 400:
            # Surface the Azure DevOps error detail (message/typeName) instead of
            # a bare status code so failures are actually diagnosable.
            detail = response.text
            try:
                body = response.json()
                detail = body.get("message") or body.get("value", {}).get("Message") or detail
            except Exception:
                pass
            raise RuntimeError(
                f"ADO {response.status_code} creating '{work_item_type}': {detail}"
            )
        data = response.json()
        wi_id = data.get("id")
        return (int(wi_id) if wi_id else None, data)

    @staticmethod
    def _build_patch_document(payload: dict, org_base: str) -> list[dict]:
        """Translate the neutral payload dict into an ADO JSON-Patch document."""
        ops: list[dict] = []

        def add(field: str, value) -> None:
            ops.append({"op": "add", "path": f"/fields/{field}", "value": value})

        add("System.Title", payload.get("title", "Untitled"))
        if payload.get("assigned_to"):
            add("System.AssignedTo", payload["assigned_to"])
        if payload.get("description"):
            add("System.Description", payload["description"])
        if payload.get("acceptance_criteria"):
            add("Microsoft.VSTS.Common.AcceptanceCriteria", payload["acceptance_criteria"])
        if payload.get("priority"):
            add("Microsoft.VSTS.Common.Priority", payload["priority"])
        if payload.get("area_path"):
            add("System.AreaPath", payload["area_path"])
        if payload.get("iteration_path"):
            add("System.IterationPath", payload["iteration_path"])
        if payload.get("tags"):
            add("System.Tags", payload["tags"])

        # Estimation fields (already mapped to the right key in _build_payload).
        if payload.get("story_points") is not None:
            add("Microsoft.VSTS.Scheduling.StoryPoints", payload["story_points"])
        if payload.get("effort") is not None:
            add("Microsoft.VSTS.Scheduling.Effort", payload["effort"])
        if payload.get("original_estimate") is not None:
            add("Microsoft.VSTS.Scheduling.OriginalEstimate", payload["original_estimate"])
        if payload.get("remaining_work") is not None:
            add("Microsoft.VSTS.Scheduling.RemainingWork", payload["remaining_work"])

        # Extra raw fields (e.g. required Lean-Agile custom fields).
        for field, value in (payload.get("extra_fields") or {}).items():
            add(field, value)

        # Parent link (Hierarchy-Reverse points from child → parent).
        parent_id = payload.get("parent_id")
        if parent_id:
            ops.append(
                {
                    "op": "add",
                    "path": "/relations/-",
                    "value": {
                        "rel": "System.LinkTypes.Hierarchy-Reverse",
                        "url": f"{org_base}/_apis/wit/workItems/{parent_id}",
                    },
                }
            )
        return ops

    def _build_payload(self, item: dict) -> dict:
        """Build the ADO MCP tool payload from a work item dict."""
        work_item_type = item.get("work_item_type", "User Story")
        payload: dict = {
            "work_item_type": work_item_type,
            "title": item.get("title", "Untitled"),
            "project": self._ado_project,
        }
        # Prefer the assignee carried on the work item (inherited from its
        # source requirement); otherwise fall back to a configured default.
        # An empty value leaves the work item unassigned in Azure DevOps.
        item_assignee = (item.get("assigned_to") or "").strip()
        default_assignee = settings.ado_default_assignee.strip()
        if item_assignee:
            payload["assigned_to"] = item_assignee
        elif default_assignee:
            payload["assigned_to"] = default_assignee

        # Assign area/iteration paths by level. Epics & Features are PI-level
        # containers (span sprints); Stories/Tasks/Test Cases get the sprint.
        # Any path that isn't a real classification node in the project is
        # dropped so ADO doesn't reject the whole item (TF401347).
        valid_areas = getattr(self, "_valid_area_paths", None)
        valid_iterations = getattr(self, "_valid_iteration_paths", None)
        path_map = getattr(self, "_ado_path_map", None)
        if path_map:
            level = "container" if work_item_type in ("Epic", "Feature") else "sprint"
            paths = path_map.get(level, {})
            iteration_path = paths.get("iteration_path")
            area_path = paths.get("area_path")
            if iteration_path and (not valid_iterations or iteration_path in valid_iterations):
                payload["iteration_path"] = iteration_path
            elif iteration_path:
                logger.warning("Dropping unknown ADO iteration path: %s", iteration_path)
            if area_path and (not valid_areas or area_path in valid_areas):
                payload["area_path"] = area_path
            elif area_path:
                logger.warning("Dropping unknown ADO area path: %s", area_path)

        # Required Lean-Agile custom fields that have no server-side default.
        # Without these, ADO rejects the create (Required rule violation).
        extra_fields: dict = {}
        if work_item_type == "Epic":
            extra_fields["Custom.ORG_LA_WorkCategory"] = settings.ado_epic_work_category
        if work_item_type == "User Story":
            extra_fields["Custom.ORG_LA_PeakWork"] = False
        if extra_fields:
            payload["extra_fields"] = extra_fields

        if item.get("description"):
            payload["description"] = item["description"]
        if item.get("acceptance_criteria"):
            ac = item["acceptance_criteria"]
            if isinstance(ac, list):
                payload["acceptance_criteria"] = "\n".join(f"- {a}" for a in ac)
            else:
                payload["acceptance_criteria"] = str(ac)
        if item.get("priority"):
            payload["priority"] = item["priority"]

        # Estimation fields, mapped to the right ADO field per work-item type:
        #   - User Story          → story_points
        #   - Epic / Feature      → effort (rolled-up sizing)
        #   - Task / Test Case    → original_estimate + remaining_work (hours)
        story_points = item.get("story_points")
        effort_hours = item.get("effort_hours")
        if effort_hours in (None, "", 0) and isinstance(story_points, (int, float)) and story_points > 0:
            effort_hours = round(float(story_points) * self._story_point_hours, 1)

        if work_item_type == "User Story":
            if isinstance(story_points, (int, float)) and story_points > 0:
                payload["story_points"] = story_points
        elif work_item_type in ("Epic", "Feature"):
            if isinstance(effort_hours, (int, float)) and effort_hours > 0:
                payload["effort"] = effort_hours
        elif work_item_type in ("Task", "Test Case"):
            if isinstance(effort_hours, (int, float)) and effort_hours > 0:
                payload["original_estimate"] = effort_hours
                payload["remaining_work"] = effort_hours

        if item.get("tags"):
            tags = item["tags"]
            payload["tags"] = ", ".join(tags) if isinstance(tags, list) else str(tags)
        return payload

    async def _log_push(
        self,
        session_id: str,
        work_item_id: str | None,
        tool_name: str,
        payload: dict,
        response: dict | None,
        ado_work_item_id: int | None,
        success: bool,
        error_message: str | None = None,
        latency_ms: int | None = None,
    ) -> None:
        """Log every ADO MCP server call to the ado_push_log table for audit."""
        log_entry = ADOPushLog(
            session_id=session_id,
            work_item_id=work_item_id,
            tool_name=tool_name,
            payload=payload,
            response=response,
            ado_work_item_id=ado_work_item_id,
            success=success,
            error_message=error_message,
            latency_ms=latency_ms,
        )
        async with async_session_factory() as db_session:
            db_session.add(log_entry)
            await db_session.commit()
