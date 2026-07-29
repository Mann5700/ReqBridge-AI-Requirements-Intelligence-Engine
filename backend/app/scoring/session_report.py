"""Session report generator — produces rich HTML review documents.

Generates a ROM-Estimate-style HTML report with the following layout:

    1. Source Mode & Input
    2. Feature Context
    3. Impact Analysis (impacted-area chips + narrative)
    4. ROM Estimate (Total / Band / Points / Hours + per-slice scoring matrix)
    5. Reference Plan / Work Item Plan (monospace tree)
    6. Conflicts Detected (if any)
    7. Sprint Alignment
    8. Safety Review
    9. Assumptions & Unknowns (aggregated from per-requirement fields)
"""

from __future__ import annotations

from datetime import datetime
from html import escape
from typing import Any

from backend.app.scoring.report_telemetry import render_agent_footer


# ─── Slice descriptions — used in the Impact Assessment narrative ────────────
SLICE_DESCRIPTIONS: dict[str, str] = {
    "EDC": "Inbound data capture / validation / routing",
    "ODC": "Image-driven workflows and queue processing",
    "IDC": "Interactive key entry / correction / real-time validation",
    "MSG": "Messaging distribution and downstream feeds",
    "REG": "Regulatory / trade-compliance filing",
    "SCAN_STS": "Scan ingestion + status/visibility behaviors",
    "BRE_SEC": "Rule engine + security screening",
    "RPT": "Reporting correctness, extract validation",
    "INFRA": "Platform/deployment connectivity verification",
    "PERF": "Performance testing, peak readiness",
    "MOD": "Modernization / tier migration",
}


def generate_session_report(
    session_id: str,
    requirements: list[dict],
    work_items: list[dict],
    conflicts: list[dict],
    metadata: dict | None = None,
) -> str:
    """Generate a complete ROM-style HTML review report for a session."""
    meta = metadata or {}
    gen_date = datetime.now().strftime("%Y-%m-%d %H:%M")
    doc_names: list[str] = meta.get("uploaded_documents", []) or []
    doc_metadatas: list[dict] = meta.get("document_metadatas", []) or []
    session_meta: dict = meta.get("session_metadata", {}) or {}

    # Pull pre-computed ROM data from session metadata (set by PlanningAgent).
    rom_band: str = (session_meta.get("rom_band") or "").lower()
    rom_score: float = float(session_meta.get("rom_score") or 0.0)
    rom_slices: list[dict] = session_meta.get("rom_slices") or []
    sprint_info: dict = session_meta.get("sprint") or {}

    total_reqs = len(requirements)
    total_conflicts = len(conflicts)
    total_work_items = len(work_items)
    total_points = sum(wi.get("story_points") or 0 for wi in work_items)
    sph = 6.5
    total_hours = round(total_points * sph, 1)

    # Detect feature title from the root Feature work item, falling back to
    # the source document filename.
    feature_title = "Feature TBD"
    for wi in work_items:
        if (wi.get("work_item_type") or "").lower() == "feature":
            feature_title = (wi.get("title") or feature_title).removeprefix("QA Testing: ")
            break
    if feature_title == "Feature TBD" and doc_names:
        feature_title = doc_names[0].replace(".txt", "").replace("_", " ")

    sections: list[str] = []

    # ── 1. Source Mode & Input ───────────────────────────────────────────
    sections.append(_section(
        "Source Mode & Input",
        _source_section(doc_names, doc_metadatas),
    ))

    # ── 2. Feature Context ───────────────────────────────────────────────
    sections.append(_section(
        "Feature Context",
        _feature_context_section(requirements, work_items),
    ))

    # ── 3. Classification & Impact Assessment ─────────────────────────────
    sections.append(_section(
        "Classification &amp; Impact Assessment",
        _impact_section(rom_slices),
    ))

    # ── 4. ROM Estimate ──────────────────────────────────────────────────
    sections.append(_section(
        "ROM Estimate",
        _rom_estimate_section(rom_score, rom_band, total_points, total_hours, rom_slices),
    ))

    # ── 5. Requirements Extracted (detailed table with confidence scores)
    sections.append(_section(
        f"Requirements Extracted ({total_reqs})",
        _requirements_summary(requirements, session_id),
    ))

    # ── 7. Conflicts Detected (only if any) ──────────────────────────────
    if conflicts:
        sections.append(_section(
            f"Conflicts Detected ({total_conflicts})",
            _conflicts_section(conflicts, requirements),
        ))

    # ── 8. Sprint Alignment ──────────────────────────────────────────────
    sections.append(_section(
        "Sprint Alignment",
        _sprint_section(sprint_info),
    ))

    # ── 9. Safety Review ─────────────────────────────────────────────────
    sections.append(_section("Safety Review", _safety_section()))

    # ── 10. Assumptions & Unknowns ───────────────────────────────────────
    sections.append(_section(
        "Assumptions & Unknowns",
        _assumptions_unknowns_section(requirements),
    ))

    header = _header(feature_title, gen_date, rom_band, total_work_items)
    body = "\n".join(sections)
    footer_data = {
        "total_requirements": total_reqs,
        "total_work_items": total_work_items,
        "total_conflicts": total_conflicts,
    }

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>ROM Estimate — {escape(feature_title)}</title>
<style>
  body {{ font-family: 'Segoe UI', sans-serif; margin: 0; padding: 0; background: #fafafa; color: #333; }}
  .container {{ max-width: 960px; margin: 24px auto; background: #fff; border-radius: 4px; box-shadow: 0 2px 8px rgba(0,0,0,0.08); overflow: hidden; }}
  .content {{ padding: 24px 32px; }}
  .footer {{ background: #f0f0f0; padding: 12px 32px; font-size: 0.8em; color: #666; text-align: center; }}
  h2 {{ color: #0078d4; border-bottom: 2px solid #0078d4; padding-bottom: 4px; font-size: 1.1em; margin: 28px 0 12px; }}
  table {{ border-collapse: collapse; width: 100%; font-size: 0.9em; margin: 8px 0 16px; }}
  th {{ background: #f0f0f0; padding: 8px 12px; border: 1px solid #ddd; text-align: left; font-size: 0.85em; }}
  td {{ padding: 8px 12px; border: 1px solid #ddd; }}
  tr:nth-child(even) td {{ background: #f9f9f9; }}
  .badge {{ display: inline-block; padding: 2px 8px; border-radius: 3px; font-size: 0.8em; font-weight: 600; }}
  .badge-high {{ background: #d1fae5; color: #059669; }}
  .badge-medium {{ background: #fef3c7; color: #d97706; }}
  .badge-low {{ background: #fee2e2; color: #dc2626; }}
  .area-chip {{ display: inline-block; background: #e1f0ff; color: #0078d4; padding: 3px 10px; border-radius: 4px; margin: 2px 4px 2px 0; font-size: 0.85em; font-weight: 600; }}
  .tree-view {{ font-family: 'Cascadia Code', Consolas, monospace; padding: 16px; background: #f5f5f5; border-radius: 4px; font-size: 0.85em; line-height: 1.7; }}
  /* ADO standard work-item-type colors */
  .tree-view .epic     {{ color: #FF7B00; font-weight: 700; }}
  .tree-view .feature  {{ color: #773B93; font-weight: 700; }}
  .tree-view .story    {{ color: #009CCC; font-weight: 600; }}
  .tree-view .task     {{ color: #B89500; }}
  .tree-view .testcase {{ color: #006B30; }}
  .tree-view .points   {{ color: #d97706; font-weight: 600; font-size: 0.9em; }}
  .rom-summary {{ font-size: 1em; }}
  .rom-summary strong {{ color: #0078d4; }}
  .feature-ctx details {{ transition: all 0.2s; }}
  .feature-ctx details[open] {{ background: #f9fbfd; border-radius: 3px; padding-right: 6px; }}
  .feature-ctx summary:hover {{ color: #005a9e; }}
</style>
</head>
<body>
<div class="container">
  {header}
  <div class="content">
    {body}
  </div>
  <div class="footer">
    CONFIDENTIAL — ReqBridge | Generated {gen_date}
  </div>
  {render_agent_footer("reqbridge_pipeline", footer_data)}
</div>
</body>
</html>"""


def _header(title: str, gen_date: str, rom_band: str, total_work_items: int) -> str:
    # Color-code the banner by ROM band (matches the orange ROM-only sample).
    band_colors = {
        "small": "#107c10",
        "medium": "#d48000",
        "large": "#d48000",
        "xlarge": "#d83b01",
    }
    color = band_colors.get(rom_band, "#0078d4")
    subtitle = (
        f"AI-Generated ROM Estimate &amp; Work Item Plan | "
        f"{total_work_items} work items | Generated: {gen_date}"
    )
    return f"""<div style="background:{color};color:#fff;padding:16px 24px;border-radius:4px 4px 0 0;">
  <h1 style="margin:0;font-size:1.5em;">ROM Estimate — {escape(title)}</h1>
  <p style="margin:4px 0 0;font-size:0.9em;opacity:0.9;">{subtitle}</p>
</div>"""


def _section(title: str, content: str) -> str:
    return f"""<div style="margin:20px 0;">
  <h2>{escape(title)}</h2>
  {content}
</div>"""


# ─── Section: Source Mode & Input ────────────────────────────────────────────
def _source_section(doc_names: list[str], doc_metadatas: list[dict]) -> str:
    if not doc_names:
        return "<p><strong>Mode:</strong> <em>No source supplied</em></p>"
    # Detect ADO-import mode by checking the first metadata blob.
    is_ado = any(
        (m or {}).get("source") == "ado_import" for m in doc_metadatas
    )
    mode = "ado_import" if is_ado else "uploaded_document"
    source_list = ", ".join(escape(d) for d in doc_names)
    return (
        f"<p><strong>Mode:</strong> {mode}<br>"
        f"<strong>Source:</strong> {source_list}</p>"
    )


# ─── Section: Feature Context ────────────────────────────────────────────────
def _feature_context_section(requirements: list[dict], work_items: list[dict]) -> str:
    """Render the Feature context with a business summary and FR table.

    Shows a collapsible feature description card at the top, followed by
    a compact Functional Requirements table listing each requirement with
    ID, name, and summary — matching the ROM Estimate reference layout.
    """
    # Prefer the Feature root description (Feature Template mapping).
    feature_card = ""
    for wi in work_items:
        if (wi.get("work_item_type") or "").lower() == "feature":
            desc = (wi.get("description") or "").strip()
            title = (wi.get("title") or "").strip()
            points = wi.get("story_points") or 0
            if desc:
                feature_card = _render_feature_card(title, desc, points)
            break

    if not feature_card and not requirements:
        return "<p><em>No feature context available.</em></p>"

    # Build Functional Requirements table from extracted requirements
    fr_table = ""
    if requirements:
        fr_rows = ""
        for i, req in enumerate(requirements):
            req_id = escape(req.get("requirement_id") or f"FR{i + 1}")
            category = escape(req.get("category") or "—")
            statement = escape((req.get("statement") or "")[:200])
            bg_color = "#fff" if i % 2 == 0 else "#f9f9f9"
            fr_rows += (
                f'<tr style="background:{bg_color};">'
                f'<td style="padding:5px 10px;border:1px solid #ddd;font-weight:bold;'
                f'color:#0078d4;vertical-align:top;">{req_id}</td>'
                f'<td style="padding:5px 10px;border:1px solid #ddd;font-weight:bold;'
                f'vertical-align:top;">{category}</td>'
                f'<td style="padding:5px 10px;border:1px solid #ddd;color:#444;">'
                f'{statement}</td></tr>'
            )
        fr_table = f"""
<h3 style="color:#333;margin-bottom:6px;font-size:1em;"><strong>Functional Requirements</strong></h3>
<table style="border-collapse:collapse;width:100%;font-size:0.95em;">
<thead><tr>
<th style="background:#f0f0f0;padding:6px 10px;border:1px solid #ddd;text-align:left;width:70px;">ID</th>
<th style="background:#f0f0f0;padding:6px 10px;border:1px solid #ddd;text-align:left;width:200px;">Requirement</th>
<th style="background:#f0f0f0;padding:6px 10px;border:1px solid #ddd;text-align:left;">Summary</th>
</tr></thead>
<tbody>{fr_rows}</tbody>
</table>"""

    return f'<div style="font-size:0.8em;">{feature_card}{fr_table}</div>'


def _render_feature_card(title: str, desc: str, points: int) -> str:
    """Parse Feature Template markdown into a compact collapsible card."""
    # Known Feature Template section headers
    _SECTION_HEADERS = [
        "Initiative Summary",
        "Project Objective",
        "Problem Statement",
        "In Scope",
        "Out of Scope",
        "Business Requirements",
        "Deliverables",
        "Impacted Systems",
        "Risk Level",
        "Production Impact",
        "Owners",
        "E2E Contacts",
        "Dependencies",
    ]

    # Parse description into sections
    sections: list[tuple[str, str]] = []
    current_header = ""
    current_lines: list[str] = []

    for line in desc.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        # Check if line is a section header:
        # 1. Markdown ## Header style
        # 2. Bold **Header:** style
        # 3. Plain "Header:" style matching known names
        matched_header = None
        remainder = ""

        # Strip markdown heading prefix
        clean = stripped.lstrip("#").strip()
        # Strip bold markers
        if clean.startswith("**") and "**" in clean[2:]:
            clean = clean.replace("**", "").strip()
        # Strip leading bullet
        clean_no_bullet = clean.lstrip("-*• ").strip()

        for h in _SECTION_HEADERS:
            if clean_no_bullet.lower().startswith(h.lower()):
                matched_header = h
                remainder = clean_no_bullet[len(h):].lstrip(":&").strip()
                break

        # Also match "## Something" that isn't a known header (use as-is)
        if not matched_header and stripped.startswith("#"):
            matched_header = clean.rstrip(":")
            remainder = ""

        if matched_header:
            if current_header or current_lines:
                sections.append((current_header, "\n".join(current_lines)))
            current_header = matched_header
            current_lines = [remainder] if remainder else []
        else:
            current_lines.append(stripped)

    if current_header or current_lines:
        sections.append((current_header, "\n".join(current_lines)))

    # Build HTML — title badge + compact collapsible sections
    html_parts = [
        '<div class="feature-ctx">',
        f'<div style="display:flex;align-items:center;gap:12px;margin-bottom:10px;">',
        f'  <span style="font-weight:600;font-size:0.95em;">{escape(title)}</span>',
    ]
    if points:
        html_parts.append(
            f'  <span class="badge badge-medium" style="font-size:0.75em;">{points} pts</span>'
        )
    html_parts.append('</div>')

    # If we parsed labeled sections, render them as collapsible blocks
    if any(h for h, _ in sections):
        for header, content in sections:
            if not header and not content.strip():
                continue
            label = header or "Details"
            body = _bullets_to_compact_html(content)
            if not body.strip():
                continue
            html_parts.append(
                f'<details style="margin:2px 0;border-left:3px solid #0078d4;padding-left:10px;">'
                f'<summary style="cursor:pointer;font-weight:600;font-size:0.85em;color:#0078d4;'
                f'padding:3px 0;user-select:none;">{escape(label)}</summary>'
                f'<div style="font-size:0.83em;color:#444;padding:4px 0 6px;">{body}</div>'
                f'</details>'
            )
    else:
        # No recognized headers — render as a single compact block
        html_parts.append(
            f'<div style="font-size:0.85em;color:#444;line-height:1.5;">'
            f'{_bullets_to_compact_html(desc)}</div>'
        )

    html_parts.append('</div>')
    return "\n".join(html_parts)


def _bullets_to_compact_html(text: str) -> str:
    """Convert bullet-point text to compact HTML list items."""
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    if not lines:
        return ""

    def _clean(s: str) -> str:
        """Strip markdown bold markers and leading bullet chars."""
        return s.lstrip("-*•1234567890. ").replace("**", "")

    # If most lines are bullet-prefixed, render as a tight list
    bullets = [_clean(l) for l in lines if l and l[0] in "-*•"]
    if len(bullets) >= len(lines) * 0.5 and bullets:
        items = "".join(f"<li>{escape(b)}</li>" for b in bullets if b)
        return f'<ul style="margin:2px 0;padding-left:16px;line-height:1.4;">{items}</ul>'
    # Otherwise join as short paragraph (strip bold markers)
    return "<br>".join(escape(_clean(l)) for l in lines)


# ─── Section: Classification & Impact Assessment ─────────────────────────────
def _impact_section(rom_slices: list[dict]) -> str:
    if not rom_slices:
        return "<p><em>No slices classified.</em></p>"
    # Chip row
    chips = " ".join(
        f'<span class="area-chip">{escape(s.get("slice", "?"))}</span>'
        for s in rom_slices
    )

    # Detailed table with matched keywords + impact narrative per slice
    rows = ""
    for s in rom_slices:
        slice_name = escape(s.get("slice", "?"))
        keywords = escape(s.get("matched_keywords", "") or "")
        if not keywords:
            # Fallback: generate plausible keywords from slice name
            keywords = ", ".join(
                k for k in (s.get("keywords") or [])
            ) if s.get("keywords") else ""
            keywords = escape(keywords)
        impact_desc = escape(
            s.get("impact_narrative", "")
            or SLICE_DESCRIPTIONS.get(s.get("slice", ""), "Domain area")
        )
        impacted_by = s.get("impacted_by", "") or ""
        impact_html = f"<strong>{slice_name}</strong>: {impact_desc}"
        if impacted_by:
            impact_html += (
                f"<br><em style='color:#0078d4;'>Impacted by: {escape(impacted_by)}</em>"
            )
        rows += (
            f"<tr><td style='font-weight:bold;vertical-align:top;'>{slice_name}</td>"
            f"<td style='vertical-align:top;'><code>{keywords}</code></td>"
            f"<td>{impact_html}</td></tr>"
        )

    table = f"""<p style='color:#555;margin-bottom:12px;font-size:0.9em;'>
Areas assessed against classification definitions. Shows <em>how</em> this feature specifically impacts each area.</p>
<table style="font-size:0.85em;">
<thead><tr>
<th style="width:90px;">Slice</th>
<th style="width:140px;">Matched Keywords</th>
<th>How This Feature Impacts This Area</th>
</tr></thead>
<tbody>{rows}</tbody>
</table>"""
    return f"<p>{chips}</p>{table}"


# ─── Section: ROM Estimate ───────────────────────────────────────────────────
def _rom_estimate_section(
    rom_score: float,
    rom_band: str,
    total_points: int,
    total_hours: float,
    rom_slices: list[dict],
) -> str:
    """Render the scoring matrix table identical to the ROM Estimate sample."""
    band_label = (rom_band or "—").upper()
    summary = (
        f'<p class="rom-summary"><strong>Total Score:</strong> {rom_score:.3f} | '
        f'<strong>Band:</strong> {band_label} | '
        f'<strong>Points:</strong> {total_points} | '
        f'<strong>Hours:</strong> {total_hours}</p>'
    )

    if not rom_slices:
        return summary + "<p><em>No per-slice scoring available.</em></p>"

    rows = ""
    for s in rom_slices:
        rows += (
            "<tr>"
            f"<td>{escape(s.get('slice', '?'))}</td>"
            f"<td>{s.get('base_weight', '—')}</td>"
            f"<td>{s.get('change_type_multiplier', '—')}</td>"
            f"<td>{s.get('dependency_multiplier', '—')}</td>"
            f"<td>{s.get('test_breadth_multiplier', '—')}</td>"
            f"<td>{s.get('data_env_multiplier', '—')}</td>"
            f"<td><strong>{s.get('score', '—')}</strong></td>"
            "</tr>"
        )
    rows += (
        "<tr><td><strong>TOTAL</strong></td><td></td><td></td><td></td><td></td><td></td>"
        f"<td><strong>{rom_score:.3f} ({band_label})</strong></td></tr>"
    )

    table = f"""<table>
<thead><tr>
<th>Slice</th><th>Base</th><th>Change Type</th><th>Deps</th>
<th>TestBreadth</th><th>DataEnv</th><th>Score</th>
</tr></thead>
<tbody>{rows}</tbody>
</table>"""
    return summary + table


# ─── Section: Sprint Alignment ───────────────────────────────────────────────
def _sprint_section(sprint_info: dict) -> str:
    if not sprint_info:
        return "<p><em>No sprint resolved.</em></p>"
    name = escape(str(sprint_info.get("name") or sprint_info.get("sprint") or "—"))
    path = escape(str(sprint_info.get("path") or sprint_info.get("iteration_path") or "—"))
    return f"<p><strong>Sprint:</strong> {name} | <strong>Path:</strong> {path}</p>"


# ─── Section: Safety Review ──────────────────────────────────────────────────
def _safety_section() -> str:
    return (
        "<ul>"
        "<li>Create-only mode enforced (no existing artifacts modified)</li>"
        "<li>Feature Template used as creation driver (Master R10)</li>"
        "<li>Slice Discipline anti-inflation rules applied</li>"
        "<li>Hard caps on requirements and work items enforced</li>"
        "<li>No secrets exposed</li>"
        "</ul>"
    )


# ─── Section: Assumptions & Unknowns ─────────────────────────────────────────
def _assumptions_unknowns_section(requirements: list[dict]) -> str:
    """Aggregate assumptions + constraints across all requirements,
    deduplicating exact matches.
    """
    assumptions: list[str] = []
    unknowns: list[str] = []
    seen_a, seen_u = set(), set()
    for r in requirements:
        for a in (r.get("assumptions") or []):
            key = (a or "").strip().lower()
            if key and key not in seen_a:
                seen_a.add(key)
                assumptions.append(a)
        # Treat constraints as known limitations, low-confidence reqs as unknowns
        for c in (r.get("constraints") or []):
            key = (c or "").strip().lower()
            if key and key not in seen_u:
                seen_u.add(key)
                unknowns.append(c)
        if (r.get("confidence_score") or 0) < 0.6:
            stmt = (r.get("statement") or "").strip()
            key = stmt.lower()
            if stmt and key not in seen_u:
                seen_u.add(key)
                unknowns.append(f"Low-confidence requirement: {stmt}")

    if not assumptions and not unknowns:
        return "<p><em>No assumptions or unknowns recorded.</em></p>"

    html = ""
    if assumptions:
        html += "<h3>Assumptions</h3><ul>"
        html += "".join(f"<li>{escape(a)}</li>" for a in assumptions[:15])
        html += "</ul>"
    if unknowns:
        html += "<h3>Unknowns / Constraints</h3><ul>"
        html += "".join(f"<li>{escape(u)}</li>" for u in unknowns[:15])
        html += "</ul>"
    return html


def _confidence_badge(score: float) -> str:
    if score >= 0.9:
        return f'<span class="badge badge-high">{score:.0%}</span>'
    elif score >= 0.7:
        return f'<span class="badge badge-medium">{score:.0%}</span>'
    return f'<span class="badge badge-low">{score:.0%}</span>'


def _requirements_summary(requirements: list[dict], session_id: str) -> str:
    """Render a compact requirements table with confidence scores."""
    if not requirements:
        return "<p><em>No requirements extracted.</em></p>"

    rows = ""
    for i, req in enumerate(requirements):
        statement = escape(req.get("statement", "")[:200])
        category = escape(req.get("category", "") or "—")
        confidence = req.get("confidence_score", 0)
        rows += f"""<tr>
  <td>{i + 1}</td>
  <td>{statement}</td>
  <td>{category}</td>
  <td style="text-align:center;">{_confidence_badge(confidence)}</td>
</tr>"""

    return f"""<table>
<thead><tr><th>#</th><th>Requirement</th><th>Category</th><th>AI Confidence</th></tr></thead>
<tbody>{rows}</tbody>
</table>"""


def _conflicts_section(conflicts: list[dict], requirements: list[dict]) -> str:
    """Render conflicts table. Replaces raw UUIDs in descriptions with the
    human-readable row numbers (Req #1, Req #2, …) that match the
    Requirements Extracted table — so reviewers can map a conflict back to
    the rows above without decoding UUIDs.
    """
    import re

    # Build UUID → row-number map (full id + first-8-char prefix).
    id_to_num: dict[str, int] = {}
    for i, req in enumerate(requirements):
        rid = req.get("id")
        if rid:
            num = i + 1
            id_to_num[str(rid)] = num
            id_to_num[str(rid)[:8]] = num

    def _label(rid: str | None) -> str:
        if not rid:
            return "—"
        n = id_to_num.get(str(rid)) or id_to_num.get(str(rid)[:8])
        return f"Req #{n}" if n else f"Req {str(rid)[:8]}"

    # Pattern for UUID-like tokens (8+ hex chars, optionally hyphenated).
    uuid_pattern = re.compile(r"\b[0-9a-f]{8}(?:-[0-9a-f]{4}){0,4}\b", re.IGNORECASE)

    def _humanize(text: str) -> str:
        def repl(m: re.Match[str]) -> str:
            token = m.group(0)
            n = id_to_num.get(token) or id_to_num.get(token[:8])
            return f"Req #{n}" if n else token
        return uuid_pattern.sub(repl, text)

    rows = ""
    for c in conflicts:
        req_a = _label(c.get("requirement_a_id"))
        req_b = _label(c.get("requirement_b_id"))
        desc = escape(_humanize(c.get("description", ""))[:300])
        severity = escape(c.get("severity", "medium"))
        rows += (
            f"<tr><td style='white-space:nowrap;'>{req_a} ↔ {req_b}</td>"
            f"<td>{desc}</td>"
            f"<td><span class='badge badge-{severity}'>{severity}</span></td></tr>"
        )

    return f"""<table>
<thead><tr><th style="width:120px;">Between</th><th>Conflict</th><th>Severity</th></tr></thead>
<tbody>{rows}</tbody>
</table>"""


def _work_items_tree(work_items: list[dict]) -> str:
    """Render work items as a hierarchical tree, like the ROM Estimate sample.

    Output matches the monospace tree format:
      Feature TBD: QA Testing: ...
        ├── User Story: ... [5 pts | 32.5 hrs]
            └── Task: ...
            └── Task: ...
    """
    if not work_items:
        return "<p><em>No work items generated.</em></p>"

    sph = 6.5  # standard hours per point

    # Build parent→children map
    by_id = {wi.get("id"): wi for wi in work_items if wi.get("id")}
    children_map: dict[str | None, list[dict]] = {}
    for wi in work_items:
        parent = wi.get("parent_id")
        children_map.setdefault(parent, []).append(wi)

    # Find roots (items with no parent or parent not in this set)
    roots = [wi for wi in work_items if not wi.get("parent_id") or wi.get("parent_id") not in by_id]

    # Sort roots: Feature first, then stories, etc.
    type_order = {"Feature": 0, "User Story": 1, "Task": 2, "Test Case": 3}
    roots.sort(key=lambda w: type_order.get(w.get("work_item_type", ""), 99))

    def _type_class(wtype: str) -> str:
        if "Epic" in wtype:
            return "epic"
        elif "Feature" in wtype:
            return "feature"
        elif "Story" in wtype:
            return "story"
        elif "Task" in wtype:
            return "task"
        elif "Test" in wtype:
            return "testcase"
        return ""

    def _render_node(wi: dict, depth: int, is_last: bool = False) -> str:
        wtype = wi.get("work_item_type", "Unknown")
        title = escape(wi.get("title", "Untitled"))
        pts = wi.get("story_points")
        if pts:
            hrs = round(pts * sph, 1)
            pts_str = f' <span class="points">[{pts} pts | {hrs} hrs]</span>'
        else:
            pts_str = ""
        cls = _type_class(wtype)

        if depth == 0:
            # Root (Feature) — bold, no connector
            line = f'<strong class="{cls}">{wtype}: {title}</strong>{pts_str}'
        else:
            indent = "&nbsp;&nbsp;" * (depth * 2)
            connector = "└──" if is_last else "├──"
            line = f'{indent}{connector} <span class="{cls}">{wtype}: {title}</span>{pts_str}'

        result = f"<br>{line}"
        # Render children
        kids = children_map.get(wi.get("id"), [])
        kids.sort(key=lambda w: type_order.get(w.get("work_item_type", ""), 99))
        for i, child in enumerate(kids):
            result += _render_node(child, depth + 1, is_last=(i == len(kids) - 1))
        return result

    html = '<div class="tree-view">'
    for root in roots:
        html += _render_node(root, 0)
    html += "\n</div>"

    # ── Detailed Work Item Cards ──
    # Render expanded cards for User Stories with descriptions + acceptance criteria
    stories = [wi for wi in work_items if (wi.get("work_item_type") or "").lower() == "user story"]
    if stories:
        html += _work_item_detail_cards(stories, children_map, sph)

    return html


def _work_item_detail_cards(
    stories: list[dict],
    children_map: dict[str | None, list[dict]],
    sph: float,
) -> str:
    """Render detailed cards for each User Story showing description, AC, and child tasks."""
    cards = '<div style="margin-top:24px;">'

    for story in stories:
        title = escape(story.get("title", "Untitled"))
        pts = story.get("story_points") or 0
        hrs = round(pts * sph, 1)
        desc = story.get("description") or ""

        cards += f"""
<div style="background:#fff;border:1px solid #e1dfdd;border-radius:8px;padding:1.5rem;margin:1rem 0;box-shadow:0 1px 4px rgba(0,0,0,.08);">
<h3 style="color:#009CCC;margin:0 0 8px;">User Story: {title}</h3>
<p style="font-size:0.9em;"><strong>Story Points:</strong> {pts} | <strong>Hours:</strong> {hrs}</p>
"""
        # Render description (may contain acceptance criteria)
        if desc:
            cards += _render_story_description(desc)

        # Render child tasks
        tasks = children_map.get(story.get("id"), [])
        if tasks:
            cards += '<div style="margin-top:12px;">'
            cards += '<table style="font-size:0.85em;"><thead><tr>'
            cards += '<th>Task</th><th style="width:100px;">Hours</th>'
            cards += '</tr></thead><tbody>'
            for task in tasks:
                t_title = escape(task.get("title", ""))
                t_pts = task.get("story_points") or 0
                t_hrs = round(t_pts * sph, 1) if t_pts else "—"
                cards += f"<tr><td>{t_title}</td><td>{t_hrs}</td></tr>"
            cards += '</tbody></table></div>'

        cards += '</div>'

    cards += '</div>'
    return cards


def _render_story_description(desc: str) -> str:
    """Parse a story description into formatted HTML with acceptance criteria."""
    lines = desc.strip().splitlines()
    ac_lines: list[str] = []
    narrative_lines: list[str] = []
    in_ac = False

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        lower = stripped.lower()
        if "acceptance criteria" in lower or lower.startswith("ac:"):
            in_ac = True
            continue
        if in_ac:
            ac_lines.append(stripped.lstrip("-*• "))
        else:
            narrative_lines.append(stripped)

    html = ""
    if narrative_lines:
        html += '<div style="font-size:0.88em;color:#444;margin:6px 0;line-height:1.5;">'
        html += "<br>".join(escape(l) for l in narrative_lines)
        html += '</div>'
    if ac_lines:
        html += '<div style="margin-top:8px;"><strong style="font-size:0.85em;">Acceptance Criteria:</strong>'
        html += '<ul style="margin:4px 0;padding-left:18px;font-size:0.85em;">'
        html += "".join(f"<li>{escape(a)}</li>" for a in ac_lines)
        html += '</ul></div>'
    elif not narrative_lines:
        html += f'<div style="font-size:0.85em;color:#666;">{escape(desc[:300])}</div>'
    return html
