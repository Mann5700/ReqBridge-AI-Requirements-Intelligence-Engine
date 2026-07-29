"""Azure DevOps work-item *read* client.

Used to pull existing work items into a session as ingestion context (so the
pipeline can extract requirements from, or reconcile against, items that
already live in ADO). This is read-only — pushing work items is handled
separately by the IntegrationAgent.
"""

from __future__ import annotations

import html
import re

import httpx

# Max ids per ADO batch GET. The REST API caps this at 200.
_MAX_BATCH = 200


def parse_work_item_refs(raw: str) -> list[int]:
    """Extract work-item ids from free-form user input.

    Accepts any mix of:
      - bare ids: ``1234``
      - comma / space / newline separated lists: ``12, 34 56``
      - ``AB#1234`` tokens (Azure Boards mention syntax)
      - full ADO URLs: ``https://dev.azure.com/org/proj/_workitems/edit/1234``
        or ``.../_workitems/edit/1234/`` or query forms ``?workitem=1234`` /
        ``?id=1234``

    Returns a de-duplicated list of positive integer ids, preserving the order
    they first appear.
    """
    if not raw:
        return []

    ids: list[int] = []
    seen: set[int] = set()

    # For each whitespace/comma separated token, the id is the final run of
    # digits (URLs like ``/_workitems/edit/1234`` and ``AB#1234`` both reduce
    # to the trailing number; plain lists fall out naturally).
    for token in re.split(r"[\s,;]+", raw.strip()):
        if not token:
            continue
        nums = re.findall(r"\d+", token)
        if not nums:
            continue
        candidate = int(nums[-1])
        if candidate > 0 and candidate not in seen:
            seen.add(candidate)
            ids.append(candidate)

    return ids


def _strip_html(value: str | None) -> str:
    """Convert ADO rich-text (HTML) fields to readable plain text."""
    if not value:
        return ""
    # Turn block boundaries into newlines so lists/paragraphs stay legible.
    text = re.sub(r"<\s*(br|/p|/div|/li)\s*/?>", "\n", value, flags=re.IGNORECASE)
    text = re.sub(r"<\s*li\s*>", "- ", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)  # drop remaining tags
    text = html.unescape(text)
    # Collapse excess blank lines / trailing whitespace.
    lines = [ln.rstrip() for ln in text.splitlines()]
    out: list[str] = []
    for ln in lines:
        if ln or (out and out[-1]):
            out.append(ln)
    return "\n".join(out).strip()


async def fetch_work_items(
    org_url: str,
    pat: str,
    ids: list[int],
    project: str | None = None,
) -> list[dict]:
    """Fetch work items from Azure DevOps by id.

    Returns a list of normalized dicts (id, type, title, state, description,
    acceptance_criteria, assigned_to, priority, tags, area_path,
    iteration_path) ordered to match ``ids``. Ids that don't exist or aren't
    accessible are silently skipped.

    Raises ``httpx.HTTPStatusError`` on auth/transport failures so the caller
    can map them to a friendly response.
    """
    if not ids:
        return []

    org_url = org_url.rstrip("/")
    base = f"{org_url}/{project}" if project else org_url

    # Build batch URLs first, then fan out concurrently. ADO's REST API has
    # no per-org rate-limit penalty at this scale and each batch is bounded
    # by _MAX_BATCH ids, so running them in parallel cuts wall-clock when
    # the user pastes hundreds of ids.
    import asyncio as _asyncio
    batch_ranges = list(range(0, len(ids), _MAX_BATCH))
    by_id: dict[int, dict] = {}

    async with httpx.AsyncClient(timeout=20.0, auth=("", pat)) as client:
        async def _fetch_batch(start: int) -> list[dict]:
            batch = ids[start : start + _MAX_BATCH]
            id_csv = ",".join(str(i) for i in batch)
            url = (
                f"{base}/_apis/wit/workitems"
                f"?ids={id_csv}&$expand=all&errorPolicy=omit&api-version=7.1"
            )
            res = await client.get(url)
            res.raise_for_status()
            return (res.json() or {}).get("value", []) or []

        batch_results = await _asyncio.gather(
            *(_fetch_batch(s) for s in batch_ranges)
        )
        for items in batch_results:
            for item in items:
                normalized = _normalize_work_item(item, org_url, project)
                by_id[normalized["id"]] = normalized

    # Preserve the user's requested order; drop misses.
    return [by_id[i] for i in ids if i in by_id]


def _normalize_work_item(item: dict, org_url: str, project: str | None) -> dict:
    """Flatten a raw ADO work item into the fields we care about."""
    fields = item.get("fields", {}) or {}
    assigned = fields.get("System.AssignedTo")
    assigned_name = ""
    if isinstance(assigned, dict):
        assigned_name = assigned.get("displayName") or assigned.get("uniqueName") or ""
    elif isinstance(assigned, str):
        assigned_name = assigned

    wid = int(item.get("id"))
    url = item.get("url") or ""
    # Prefer a human-clickable edit URL when we can build one.
    if project:
        url = f"{org_url}/{project}/_workitems/edit/{wid}"

    return {
        "id": wid,
        "work_item_type": fields.get("System.WorkItemType", "") or "",
        "title": fields.get("System.Title", "") or "",
        "state": fields.get("System.State", "") or "",
        "description": _strip_html(fields.get("System.Description")),
        "acceptance_criteria": _strip_html(
            fields.get("Microsoft.VSTS.Common.AcceptanceCriteria")
        ),
        "assigned_to": assigned_name,
        "priority": fields.get("Microsoft.VSTS.Common.Priority"),
        "tags": fields.get("System.Tags", "") or "",
        "area_path": fields.get("System.AreaPath", "") or "",
        "iteration_path": fields.get("System.IterationPath", "") or "",
        "url": url,
    }


def render_work_items_as_text(items: list[dict]) -> str:
    """Render fetched work items into a readable document for ingestion.

    The output reads like a requirements document so the extraction agent can
    treat it the same as any uploaded file.
    """
    blocks: list[str] = [
        "AZURE DEVOPS WORK ITEMS (imported as pipeline context)",
        "=" * 60,
        "",
    ]
    for it in items:
        header = f"[{it['work_item_type'] or 'Work Item'} #{it['id']}] {it['title']}".strip()
        blocks.append(header)
        meta_bits = []
        if it.get("state"):
            meta_bits.append(f"State: {it['state']}")
        if it.get("assigned_to"):
            meta_bits.append(f"Assigned To: {it['assigned_to']}")
        if it.get("priority") is not None:
            meta_bits.append(f"Priority: {it['priority']}")
        if it.get("tags"):
            meta_bits.append(f"Tags: {it['tags']}")
        if meta_bits:
            blocks.append(" | ".join(meta_bits))
        if it.get("description"):
            blocks.append("")
            blocks.append("Description:")
            blocks.append(it["description"])
        if it.get("acceptance_criteria"):
            blocks.append("")
            blocks.append("Acceptance Criteria:")
            blocks.append(it["acceptance_criteria"])
        if it.get("url"):
            blocks.append(f"Source: {it['url']}")
        blocks.append("")
        blocks.append("-" * 60)
        blocks.append("")

    return "\n".join(blocks).strip() + "\n"

