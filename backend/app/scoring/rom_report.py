"""
ROM HTML Report Generator
Generates professional HTML reports from structured ROM data.
Replaces LLM-generated HTML to save tokens.

Usage:
  python rom_report.py --data rom_output.json --mode draft|final|rom_only --output output/Report.html

Input (rom_output.json): output from rom_engine.py plus additional context fields:
  {
    "feature_id": "12345",
    "feature_title": "QA Testing: Sample Feature",
    "source_mode": "requirements_document",
    "requirements_source": "Sample Requirements.docx",
    "impact_analysis": { "tags": [...], "summary": "..." },
    "assumptions": ["..."],
    "unknowns": ["..."],
    "artifact_plan": { "stories": [...] },
    ...rom_engine output fields...
  }
"""
import json
import sys
import re
import argparse
from datetime import datetime
from pathlib import Path
from html import escape

from report_telemetry import render_agent_footer


def _clean_ado_text(text: str) -> str:
    """Clean up ADO-stripped HTML text artifacts and reassemble fragmented lines."""
    # Decode common HTML entities
    text = text.replace("&amp;nbsp;", " ").replace("&nbsp;", " ")
    text = text.replace("&amp;quot;", '"').replace("&quot;", '"')
    text = text.replace("&amp;amp;", "&").replace("&amp;", "&")
    # Collapse excessive whitespace (but preserve single newlines)
    text = re.sub(r'[ \t]+', ' ', text)
    # Collapse 3+ newlines into 2
    text = re.sub(r'\n{3,}', '\n\n', text)

    # Reassemble fragmented ADO lines: ADO HTML stripping often puts each
    # styled span on its own line. Join short lines that don't end with sentence
    # terminators and aren't FR headings or bullet items.
    lines = text.split('\n')
    reassembled = []
    buffer = ""
    fr_heading_re = re.compile(r'^FR\d+(?:\.\d+[a-z]?)?\s*[–\-—]')
    bullet_re = re.compile(r'^[\•\-\–\►\*●○◦]\s')
    section_heading_re = re.compile(r'^[A-Z][A-Za-z &/]+:?\s*$')

    for line in lines:
        stripped = line.strip()
        if not stripped:
            # Blank line — flush buffer and keep paragraph break
            if buffer:
                reassembled.append(buffer)
                buffer = ""
            reassembled.append("")
            continue

        # Preserve FR headings, bullets, and section headings on their own lines
        if fr_heading_re.match(stripped) or bullet_re.match(stripped):
            if buffer:
                reassembled.append(buffer)
                buffer = ""
            reassembled.append(stripped)
            continue

        # Join short fragments into the buffer
        if buffer:
            # If buffer doesn't end with a sentence terminator, join
            if buffer[-1] not in '.!?:' or len(stripped) < 40:
                buffer += " " + stripped
            else:
                reassembled.append(buffer)
                buffer = stripped
        else:
            buffer = stripped

    if buffer:
        reassembled.append(buffer)

    return '\n'.join(reassembled).strip()


def _format_feature_description(desc: str) -> str:
    """Format ADO feature description into structured, readable HTML."""
    desc = _clean_ado_text(desc)

    # Split into sections by FR headings or major section headers
    fr_pattern = re.compile(r'(FR\d+(?:\.\d+[a-z]?)?)\s*[–\-—]\s*(.+?)(?=\n|$)')

    # Detect FRs
    frs = fr_pattern.findall(desc)

    if frs:
        # Has FR structure — show brief summary + FR matrix table
        html = "<div style='font-size:0.8em;'>"

        # Extract preamble (text before first FR) — show condensed summary only
        first_fr_match = fr_pattern.search(desc)
        if first_fr_match and first_fr_match.start() > 50:
            preamble = desc[:first_fr_match.start()].strip()
            # Truncate preamble to first ~500 chars for brevity
            if len(preamble) > 500:
                # Find a sentence boundary near 500
                cut = preamble.rfind('.', 0, 500)
                if cut > 200:
                    preamble = preamble[:cut + 1] + " [...]"
                else:
                    preamble = preamble[:500] + "..."
            preamble_html = _format_text_block(preamble)
            html += f"<div style='margin-bottom:10px;'>{preamble_html}</div>"

        # Format FRs as a matrix table
        html += "<h3 style='color:#333;margin-bottom:6px;font-size:1em;'><strong>Functional Requirements</strong></h3>"
        html += '<table style="border-collapse:collapse;width:100%;font-size:0.95em;">'
        html += '<thead><tr>'
        html += '<th style="background:#f0f0f0;padding:6px 10px;border:1px solid #ddd;text-align:left;width:70px;">ID</th>'
        html += '<th style="background:#f0f0f0;padding:6px 10px;border:1px solid #ddd;text-align:left;width:200px;">Requirement</th>'
        html += '<th style="background:#f0f0f0;padding:6px 10px;border:1px solid #ddd;text-align:left;">Summary</th>'
        html += '</tr></thead><tbody>'

        # Split desc by FR headings and get content for each
        fr_splits = fr_pattern.split(desc)
        i = 1
        row_num = 0
        while i + 2 < len(fr_splits):
            fr_id = fr_splits[i].strip()
            fr_title = fr_splits[i + 1].strip()
            fr_content = fr_splits[i + 2].strip() if i + 2 < len(fr_splits) else ""
            # Condense content to first meaningful sentence/line
            fr_summary = _condense_fr_content(fr_content)

            bg = "#fff" if row_num % 2 == 0 else "#f9f9f9"
            html += f'<tr style="background:{bg};">'
            html += f'<td style="padding:5px 10px;border:1px solid #ddd;font-weight:bold;color:#0078d4;vertical-align:top;">{escape(fr_id)}</td>'
            html += f'<td style="padding:5px 10px;border:1px solid #ddd;font-weight:bold;vertical-align:top;">{escape(fr_title)}</td>'
            html += f'<td style="padding:5px 10px;border:1px solid #ddd;color:#444;">{fr_summary}</td>'
            html += '</tr>'
            i += 3
            row_num += 1

        html += '</tbody></table>'
        html += "</div>"
        return html
    else:
        # No FR structure — format as paragraphs with smaller font
        return f"<div style='font-size:0.8em;'>{_format_text_block(desc)}</div>"


def _condense_fr_content(content: str) -> str:
    """Condense FR content to a brief summary suitable for a table cell.

    Guardrail: Reassembles fragmented ADO lines into coherent sentences before
    summarizing. Rejects summaries that are too short (< 20 chars) or contain
    only a system name (e.g. a bare module code) and forces re-assembly from subsequent lines.
    """
    content = content.strip()
    if not content:
        return "<em style='color:#999;'>—</em>"

    # --- Phase 1: Reassemble fragmented lines into coherent text ---
    raw_lines = [ln.strip() for ln in content.split('\n') if ln.strip()]
    # Remove noise lines
    raw_lines = [ln for ln in raw_lines if len(ln) > 2 and ln not in ("Note:", "Notes", "—")]

    if not raw_lines:
        return "<em style='color:#999;'>—</em>"

    # Detect bullet lines vs prose lines
    bullet_re = re.compile(r'^[\•\-\–\►\*●○◦]\s*')
    bullets = []
    prose_parts = []

    for ln in raw_lines:
        if bullet_re.match(ln):
            bullets.append(bullet_re.sub('', ln).strip())
        else:
            prose_parts.append(ln)

    # Reassemble prose: join short fragments that don't end with sentence terminators
    # ADO HTML stripping often breaks mid-sentence on span/div boundaries
    reassembled = ""
    for part in prose_parts:
        if not reassembled:
            reassembled = part
        elif len(reassembled.split()[-1]) < 3 or not reassembled[-1] in '.!?:':
            # Previous fragment didn't end a sentence — join with space
            reassembled += " " + part
        else:
            reassembled += " " + part

    # --- Phase 2: Extract meaningful summary ---
    # Guardrail: if reassembled text is just a system name or too short, it's not useful
    SYSTEM_NAMES = {"EDC", "ODC", "IDC", "MSG", "REG", "SCAN", "BRE", "RPT", "INFRA", "PERF"}
    summary_text = reassembled.strip()

    # If summary starts with just a system name followed by content, keep the whole thing
    # If it IS only a system name, that means reassembly failed — use raw concatenation
    first_word = summary_text.split()[0] if summary_text.split() else ""
    if first_word.upper().rstrip('.,;:') in SYSTEM_NAMES and len(summary_text) < 20:
        # Guardrail triggered: summary is just a system name — force broader assembly
        summary_text = " ".join(raw_lines[:6])

    # Truncate to first meaningful sentence (~150 chars)
    if len(summary_text) > 150:
        # Try to cut at a sentence or clause boundary
        cut = summary_text.find('.', 80)
        if cut > 0 and cut < 160:
            summary_text = summary_text[:cut + 1]
        else:
            cut = summary_text.find(',', 100)
            if cut > 0 and cut < 160:
                summary_text = summary_text[:cut + 1] + "..."
            else:
                summary_text = summary_text[:150] + "..."

    result = escape(summary_text)

    # Append bullet items inline if present
    if bullets:
        items_html = ", ".join(escape(b[:50]) for b in bullets[:5])
        if len(bullets) > 5:
            items_html += f" (+{len(bullets) - 5} more)"
        result += f"<br><span style='color:#555;font-style:italic;'>{items_html}</span>"

    # --- Phase 3: Final guardrail — reject trivially short results ---
    plain_text = re.sub(r'<[^>]+>', '', result).strip()
    if len(plain_text) < 15:
        # Last resort: concatenate all available text
        fallback = " ".join(raw_lines[:8])[:150]
        if len(fallback) > 140:
            fallback = fallback[:140] + "..."
        return escape(fallback)

    return result


def _format_text_block(text: str) -> str:
    """Format a block of text into readable HTML paragraphs and lists."""
    text = text.strip()
    if not text:
        return ""

    # Split into lines
    lines = text.split('\n')
    html_parts = []
    current_list = []

    for line in lines:
        stripped = line.strip()
        if not stripped:
            # Flush any open list
            if current_list:
                html_parts.append("<ul style='margin:3px 0;padding-left:20px;line-height:1.4;'>" +
                                  "".join(f"<li>{escape(item)}</li>" for item in current_list) + "</ul>")
                current_list = []
            continue

        # Detect section headings (title case, ends with colon or is all-caps-like)
        if re.match(r'^[A-Z][A-Za-z &/,]+(?:Requirements|Criteria|Scope|Objective|Assumptions|Summary|Feature|Notes?)\s*$', stripped):
            if current_list:
                html_parts.append("<ul style='margin:3px 0;padding-left:20px;line-height:1.4;'>" +
                                  "".join(f"<li>{escape(item)}</li>" for item in current_list) + "</ul>")
                current_list = []
            html_parts.append(f"<p style='margin:8px 0 3px;line-height:1.4;'><strong>{escape(stripped)}</strong></p>")
        # Detect bullet-like items
        elif re.match(r'^[\•\-\–\►\*]\s+', stripped) or (len(stripped) < 80 and line.startswith('    ')):
            item = re.sub(r'^[\•\-\–\►\*]\s+', '', stripped)
            current_list.append(item)
        else:
            # Flush any open list
            if current_list:
                html_parts.append("<ul style='margin:3px 0;padding-left:20px;line-height:1.4;'>" +
                                  "".join(f"<li>{escape(item)}</li>" for item in current_list) + "</ul>")
                current_list = []
            # Regular paragraph
            html_parts.append(f"<p style='margin:4px 0;line-height:1.4;'>{escape(stripped)}</p>")

    # Flush final list
    if current_list:
        html_parts.append("<ul style='margin:4px 0;padding-left:20px;line-height:1.5;'>" +
                          "".join(f"<li>{escape(item)}</li>" for item in current_list) + "</ul>")

    return "\n".join(html_parts)


def _format_acceptance_criteria(ac: str) -> str:
    """Format acceptance criteria into a readable list."""
    ac = _clean_ado_text(ac)
    items = re.split(r'\n+', ac)
    clean_items = []
    for item in items:
        item = item.strip().lstrip("•-–► ")
        if item and len(item) > 3:
            clean_items.append(item)

    if not clean_items:
        return ""

    html = "<h3 style='margin-top:16px;color:#333;border-top:1px solid #eee;padding-top:12px;'>Acceptance Criteria</h3>"
    html += "<ul style='line-height:1.6;padding-left:20px;'>"
    for item in clean_items:
        html += f"<li>{escape(item)}</li>"
    html += "</ul>"
    return html


def build_header(title: str, mode: str, gen_date: str) -> str:
    """Build the header banner."""
    if mode == "draft":
        banner_color = "#0078d4"
        label = "DRAFT &mdash; Pending Review"
    elif mode == "rom_only":
        banner_color = "#d48000"
        label = "ROM ESTIMATE ONLY &mdash; No ADO Artifacts Will Be Created"
    else:
        banner_color = "#0078d4"
        label = "FINAL &mdash; Artifacts Created"

    return f"""<div style="background:{banner_color};color:#fff;padding:16px 24px;border-radius:4px 4px 0 0;">
  <h1 style="margin:0;font-size:1.5em;">{escape(title)}</h1>
  <p style="margin:4px 0 0;font-size:0.9em;opacity:0.9;">{label} | Generated: {gen_date}</p>
</div>"""


def build_section(title: str, content: str) -> str:
    return f"""<div style="margin:20px 0;">
  <h2 style="color:#0078d4;border-bottom:2px solid #0078d4;padding-bottom:4px;">{escape(title)}</h2>
  {content}
</div>"""


def build_table(headers: list, rows: list) -> str:
    """Build an HTML table with alternating row shading."""
    th = "".join(f'<th style="background:#f0f0f0;padding:8px 12px;border:1px solid #ddd;text-align:left;">{escape(str(h))}</th>' for h in headers)
    body = ""
    for i, row in enumerate(rows):
        bg = "#fff" if i % 2 == 0 else "#f9f9f9"
        cells = "".join(f'<td style="padding:8px 12px;border:1px solid #ddd;">{escape(str(c))}</td>' for c in row)
        body += f'<tr style="background:{bg};">{cells}</tr>'
    return f'<table style="border-collapse:collapse;width:100%;font-size:0.9em;"><thead><tr>{th}</tr></thead><tbody>{body}</tbody></table>'


def build_tree(data: dict) -> str:
    """Build artifact hierarchy tree."""
    lines = []
    feature_id = data.get("feature_id", "TBD")
    feature_title = data.get("feature_title", "Feature")
    lines.append(f'<div style="font-family:monospace;padding:12px;background:#f5f5f5;border-radius:4px;">')
    lines.append(f'<strong>Feature {escape(str(feature_id))}: {escape(feature_title)}</strong>')

    stories = data.get("artifact_plan", {}).get("stories", data.get("story_package", []))
    for story in stories:
        if isinstance(story, dict):
            title = story.get("title", "")
            pts = story.get("story_points", "")
            lines.append(f'<br>&nbsp;&nbsp;├── User Story: {escape(title)} [{pts} pts]')
            for task in story.get("tasks", []):
                task_title = task if isinstance(task, str) else task.get("title", "")
                lines.append(f'<br>&nbsp;&nbsp;&nbsp;&nbsp;└── Task: {escape(str(task_title))}')
        else:
            lines.append(f'<br>&nbsp;&nbsp;├── User Story: {escape(str(story))}')

    lines.append('</div>')
    return "\n".join(lines)


def _validate_fr_mapping(desc: str) -> list:
    """Guardrail: Validate that FR extraction produces meaningful summaries.

    Returns a list of warning strings for any FRs that failed quality checks.
    Checks:
      - Each FR summary must be >= 20 chars (not just a system name)
      - Each FR must have identifiable content (not empty/noise)
      - Overall: at least 50% of FRs must have substantive summaries
    """
    SYSTEM_NAMES = {"EDC", "ODC", "IDC", "MSG", "REG", "SCAN", "BRE", "RPT", "INFRA", "PERF"}
    warnings = []

    desc = _clean_ado_text(desc)
    fr_pattern = re.compile(r'(FR\d+(?:\.\d+[a-z]?)?)\s*[–\-—]\s*(.+?)(?=\n|$)')
    frs = fr_pattern.findall(desc)

    if not frs:
        return warnings  # No FRs to validate

    fr_splits = fr_pattern.split(desc)
    total_frs = 0
    poor_frs = 0
    i = 1
    while i + 2 < len(fr_splits):
        fr_id = fr_splits[i].strip()
        fr_content = fr_splits[i + 2].strip() if i + 2 < len(fr_splits) else ""
        total_frs += 1

        # Check content quality
        summary = _condense_fr_content(fr_content)
        plain = re.sub(r'<[^>]+>', '', summary).strip()

        if len(plain) < 20:
            poor_frs += 1
            warnings.append(f"  WARN: {fr_id} summary too short ({len(plain)} chars): '{plain}'")
        elif plain.upper().strip('.,;: ') in SYSTEM_NAMES:
            poor_frs += 1
            warnings.append(f"  WARN: {fr_id} summary is only a system name: '{plain}'")

        i += 3

    if total_frs > 0 and poor_frs / total_frs > 0.5:
        warnings.insert(0, f"  GUARDRAIL ALERT: {poor_frs}/{total_frs} FRs have inadequate summaries — review ADO text quality")

    return warnings


def generate_report(data: dict, mode: str) -> str:
    """Generate complete HTML report."""
    gen_date = datetime.now().strftime("%Y-%m-%d %H:%M")
    feature_id = data.get("feature_id", "TBD")
    feature_title = data.get("feature_title", "ROM Estimate")

    title = f"ROM Estimate — Feature {feature_id}"

    sections = []

    # Source Mode
    source_mode = data.get("source_mode", "unknown")
    req_source = data.get("requirements_source", "N/A")
    sections.append(build_section("Source Mode & Input", f"<p><strong>Mode:</strong> {escape(source_mode)}<br><strong>Source:</strong> {escape(str(req_source))}</p>"))

    # Feature Context — format description into readable structured content
    if data.get("feature_description"):
        desc = data["feature_description"]

        # Guardrail: validate FR mapping quality before rendering
        fr_warnings = _validate_fr_mapping(desc)
        if fr_warnings:
            for w in fr_warnings:
                print(w, file=sys.stderr)

        desc_html = _format_feature_description(desc)

        # Add acceptance criteria if present
        if data.get("acceptance_criteria"):
            ac_html = _format_acceptance_criteria(data["acceptance_criteria"])
            desc_html += ac_html

        sections.append(build_section("Feature Context", desc_html))

    # Classification & Impact Assessment
    evidence = data.get("classification_evidence", {})
    if evidence:
        evidence_html = "<p style='color:#555;margin-bottom:12px;font-size:0.9em;'>Areas assessed against classification definitions. Shows <em>how</em> this feature specifically impacts each area.</p>"
        evidence_html += '<table style="border-collapse:collapse;width:100%;font-size:0.85em;">'
        evidence_html += '<thead><tr>'
        evidence_html += '<th style="background:#f0f0f0;padding:8px 12px;border:1px solid #ddd;text-align:left;width:90px;">Slice</th>'
        evidence_html += '<th style="background:#f0f0f0;padding:8px 12px;border:1px solid #ddd;text-align:left;width:140px;">Matched Keywords</th>'
        evidence_html += '<th style="background:#f0f0f0;padding:8px 12px;border:1px solid #ddd;text-align:left;">How This Feature Impacts This Area</th>'
        evidence_html += '</tr></thead><tbody>'
        for i, (slice_name, reasons) in enumerate(evidence.items()):
            bg = "#fff" if i % 2 == 0 else "#f9f9f9"
            # Parse reasons to extract matched keywords and explanations
            keywords_all = []
            explanations = []
            for reason in reasons:
                # Format: [CLASS_KEY] Matched: kw1, kw2 | Relevant FRs: FR1, FR2 (definition: ...)
                # or:     [CLASS_KEY] Matched: kw1, kw2 (definition: ...)
                import re as _re
                match = _re.match(r'\[(.+?)\]\s*Matched:\s*(.+?)(?:\s*\|\s*Relevant FRs:\s*(.+?))?\s*\(definition:\s*(.+?)\)', reason)
                if match:
                    class_key = match.group(1)
                    keywords = match.group(2).strip().rstrip(" |")
                    relevant_frs = match.group(3)
                    definition = match.group(4).strip()
                    keywords_all.append(keywords)
                    fr_html = ""
                    if relevant_frs:
                        fr_html = f"<br><em style='color:#0078d4;'>Impacted by: {escape(relevant_frs.strip())}</em>"
                    explanations.append(f"<strong>{escape(class_key)}</strong>: {escape(definition)}{fr_html}")
                else:
                    explanations.append(escape(reason))
            keywords_html = ", ".join(f"<code>{escape(k)}</code>" for k in keywords_all) if keywords_all else "—"
            explain_html = "<br>".join(explanations)
            evidence_html += f'<tr style="background:{bg};">'
            evidence_html += f'<td style="padding:8px 12px;border:1px solid #ddd;font-weight:bold;vertical-align:top;">{escape(slice_name)}</td>'
            evidence_html += f'<td style="padding:8px 12px;border:1px solid #ddd;vertical-align:top;">{keywords_html}</td>'
            evidence_html += f'<td style="padding:8px 12px;border:1px solid #ddd;">{explain_html}</td>'
            evidence_html += '</tr>'
        evidence_html += '</tbody></table>'
        sections.append(build_section("Classification &amp; Impact Assessment", evidence_html))

    # Impact Analysis
    impact = data.get("impact_analysis", {})
    if impact:
        tags = impact.get("tags", [])
        summary = impact.get("summary", "")
        tag_html = ", ".join(f'<span style="background:#e1f0ff;padding:2px 8px;border-radius:3px;margin:2px;">{escape(t)}</span>' for t in tags)
        sections.append(build_section("Impact Analysis", f"<p>{tag_html}</p><p>{escape(summary)}</p>"))

    # ROM Scoring
    slices = data.get("slices", [])
    if slices:
        headers = ["Slice", "Base", "Change Type", "Deps", "TestBreadth", "DataEnv", "Score"]
        rows = []
        for s in slices:
            rows.append([
                s.get("slice", ""),
                s.get("base_weight", ""),
                f'{s.get("change_type_multiplier", "")}',
                f'{s.get("dependency_multiplier", "")}',
                f'{s.get("test_breadth_multiplier", "")}',
                f'{s.get("data_env_multiplier", "")}',
                f'{s.get("score", "")}',
            ])
        total = data.get("total_score", 0)
        band = data.get("rom_band", "")
        rows.append(["TOTAL", "", "", "", "", "", f"{total} ({band.upper()})"])
        table = build_table(headers, rows)
        summary_html = f"<p><strong>Total Score:</strong> {total} | <strong>Band:</strong> {band.upper()} | <strong>Points:</strong> {data.get('story_points_total', '')} | <strong>Hours:</strong> {data.get('hours_total', '')}</p>"
        sections.append(build_section("ROM Estimate", summary_html + table))

    # Artifact Plan / Hierarchy
    plan_label = "Reference Plan &mdash; Not Created" if mode == "rom_only" else "ADO Artifact Plan"
    tree_html = build_tree(data)
    sections.append(build_section(plan_label, tree_html))

    # Sprint
    sprint = data.get("sprint", {})
    if sprint:
        sections.append(build_section("Sprint Alignment", f"<p><strong>Sprint:</strong> {escape(sprint.get('name', ''))} | <strong>Path:</strong> {escape(sprint.get('iteration_path', ''))}</p>"))

    # Safety Review
    sections.append(build_section("Safety Review", "<ul><li>Create-only mode enforced</li><li>No existing artifacts modified</li><li>No secrets exposed</li></ul>"))

    # Assumptions / Unknowns
    assumptions = data.get("assumptions", [])
    unknowns = data.get("unknowns", [])
    au_html = ""
    if assumptions:
        au_html += "<h3>Assumptions</h3><ul>" + "".join(f"<li>{escape(a)}</li>" for a in assumptions) + "</ul>"
    if unknowns:
        au_html += "<h3>Unknowns</h3><ul>" + "".join(f"<li>{escape(u)}</li>" for u in unknowns) + "</ul>"
    if au_html:
        sections.append(build_section("Assumptions & Unknowns", au_html))

    # Assemble
    body = "\n".join(sections)
    header = build_header(title, mode, gen_date)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{escape(title)}</title>
<style>
  body {{ font-family: 'Segoe UI', sans-serif; margin: 0; padding: 0; background: #fafafa; }}
  .container {{ max-width: 960px; margin: 24px auto; background: #fff; border-radius: 4px; box-shadow: 0 2px 8px rgba(0,0,0,0.08); overflow: hidden; }}
  .content {{ padding: 24px 32px; }}
  .footer {{ background: #f0f0f0; padding: 12px 32px; font-size: 0.8em; color: #666; text-align: center; }}
</style>
</head>
<body>
<div class="container">
  {header}
  <div class="content">
    {body}
  </div>
  <div class="footer">
    ReqBridge — ROM Estimate | {gen_date}
  </div>
    {render_agent_footer("rom_report", data)}
</div>
</body>
</html>"""


def main():
    parser = argparse.ArgumentParser(description="ROM HTML Report Generator")
    parser.add_argument("--data", required=True, help="Path to ROM data JSON (output from rom_engine.py + context)")
    parser.add_argument("--mode", choices=["draft", "final", "rom_only"], default="draft", help="Report mode")
    parser.add_argument("--output", required=True, help="Output HTML file path")
    args = parser.parse_args()

    with open(args.data, "r", encoding="utf-8") as f:
        data = json.load(f)

    html = generate_report(data, args.mode)

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)

    print(json.dumps({"status": "ok", "path": str(out_path), "mode": args.mode}))


if __name__ == "__main__":
    main()
