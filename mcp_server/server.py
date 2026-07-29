"""ReqBridge MCP Server (stdio) — exposes the FastAPI backend as MCP tools/resources/prompts."""

import asyncio
import json
import os
from typing import Optional

import httpx
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import (
    GetPromptResult,
    Prompt,
    PromptArgument,
    PromptMessage,
    Resource,
    TextContent,
    Tool,
)
from pydantic import BaseModel, Field

# Configuration
API_URL = os.environ.get("REQBRIDGE_API_URL", "http://localhost:8000")

# Initialize MCP server
server = Server("reqbridge")


# ─── Input Schemas (Pydantic validation before API calls) ─────────────────────

class CreateSessionInput(BaseModel):
    name: str = Field(..., min_length=1)
    description: Optional[str] = None


class UploadDocumentInput(BaseModel):
    session_id: str
    file_path: str
    file_type: str = Field(..., pattern="^(pdf|docx|xlsx|image|text|email)$")


class RunPipelineInput(BaseModel):
    session_id: str


class GetPipelineStatusInput(BaseModel):
    session_id: str


class GetRequirementsInput(BaseModel):
    session_id: str
    filter_confidence_below: Optional[float] = None


class GetConflictsInput(BaseModel):
    session_id: str


class ApproveRequirementsInput(BaseModel):
    session_id: str
    requirement_ids: list[str]


class GetWorkItemsInput(BaseModel):
    session_id: str


class PushToADOInput(BaseModel):
    session_id: str
    ado_project: Optional[str] = None
    ado_org: Optional[str] = None


class GetTraceabilityInput(BaseModel):
    session_id: str
    requirement_id: Optional[str] = None


class GetSessionReportInput(BaseModel):
    session_id: str


class CorrectRequirementInput(BaseModel):
    session_id: str
    requirement_id: str
    corrected_text: str


# ─── HTTP Client Helper ───────────────────────────────────────────────────────

async def api_request(method: str, path: str, **kwargs) -> dict:
    """Make an HTTP request to the FastAPI backend."""
    async with httpx.AsyncClient(base_url=API_URL, timeout=60.0) as client:
        response = await getattr(client, method)(path, **kwargs)
        if response.status_code >= 400:
            return {"error": response.text, "status_code": response.status_code}
        return response.json()


# ─── MCP Tools ────────────────────────────────────────────────────────────────

@server.list_tools()
async def list_tools() -> list[Tool]:
    """Expose all ReqBridge capabilities as MCP tools."""
    return [
        Tool(
            name="create_session",
            description="Create a new requirements ingestion session",
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Session name"},
                    "description": {"type": "string", "description": "Session description"},
                },
                "required": ["name"],
            },
        ),
        Tool(
            name="upload_document",
            description="Upload/ingest a document into a session's pipeline",
            inputSchema={
                "type": "object",
                "properties": {
                    "session_id": {"type": "string"},
                    "file_path": {"type": "string", "description": "Path to the document file"},
                    "file_type": {"type": "string", "enum": ["pdf", "docx", "xlsx", "image", "text", "email"]},
                },
                "required": ["session_id", "file_path", "file_type"],
            },
        ),
        Tool(
            name="run_pipeline",
            description="Trigger the full AI agent pipeline for a session",
            inputSchema={
                "type": "object",
                "properties": {"session_id": {"type": "string"}},
                "required": ["session_id"],
            },
        ),
        Tool(
            name="get_pipeline_status",
            description="Get current pipeline stage, progress %, and errors",
            inputSchema={
                "type": "object",
                "properties": {"session_id": {"type": "string"}},
                "required": ["session_id"],
            },
        ),
        Tool(
            name="get_requirements",
            description="Get all extracted requirements as structured JSON",
            inputSchema={
                "type": "object",
                "properties": {
                    "session_id": {"type": "string"},
                    "filter_confidence_below": {"type": "number", "description": "Minimum confidence threshold"},
                },
                "required": ["session_id"],
            },
        ),
        Tool(
            name="get_conflicts",
            description="Get all detected conflicts with AI explanations",
            inputSchema={
                "type": "object",
                "properties": {"session_id": {"type": "string"}},
                "required": ["session_id"],
            },
        ),
        Tool(
            name="approve_requirements",
            description="Mark requirements as human-approved for work item generation",
            inputSchema={
                "type": "object",
                "properties": {
                    "session_id": {"type": "string"},
                    "requirement_ids": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["session_id", "requirement_ids"],
            },
        ),
        Tool(
            name="get_work_items",
            description="Get the full Epic→Feature→Story→Task→TestCase hierarchy",
            inputSchema={
                "type": "object",
                "properties": {"session_id": {"type": "string"}},
                "required": ["session_id"],
            },
        ),
        Tool(
            name="push_to_azure_devops",
            description="Push approved work items to Azure DevOps via ADO MCP server",
            inputSchema={
                "type": "object",
                "properties": {
                    "session_id": {"type": "string"},
                    "ado_project": {"type": "string"},
                    "ado_org": {"type": "string"},
                },
                "required": ["session_id"],
            },
        ),
        Tool(
            name="get_traceability",
            description="Get traceability graph as JSON nodes and edges",
            inputSchema={
                "type": "object",
                "properties": {
                    "session_id": {"type": "string"},
                    "requirement_id": {"type": "string", "description": "Optional: filter to specific requirement"},
                },
                "required": ["session_id"],
            },
        ),
        Tool(
            name="get_session_report",
            description="Get a markdown summary report of the entire session",
            inputSchema={
                "type": "object",
                "properties": {"session_id": {"type": "string"}},
                "required": ["session_id"],
            },
        ),
        Tool(
            name="list_sessions",
            description="List all sessions with status and metadata",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="correct_requirement",
            description="Log a human correction to a requirement, triggers feedback agent",
            inputSchema={
                "type": "object",
                "properties": {
                    "session_id": {"type": "string"},
                    "requirement_id": {"type": "string"},
                    "corrected_text": {"type": "string"},
                },
                "required": ["session_id", "requirement_id", "corrected_text"],
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    """Route MCP tool calls to the FastAPI backend."""
    try:
        if name == "create_session":
            validated = CreateSessionInput(**arguments)
            result = await api_request("post", "/sessions/", json=validated.model_dump(exclude_none=True))

        elif name == "upload_document":
            validated = UploadDocumentInput(**arguments)
            # Stream the file contents through a context manager so the file
            # descriptor is released even if the upstream request fails.
            with open(validated.file_path, "rb") as f:
                result = await api_request(
                    "post",
                    f"/sessions/{validated.session_id}/upload",
                    data={"file_type": validated.file_type},
                    files={"file": f},
                )

        elif name == "run_pipeline":
            validated = RunPipelineInput(**arguments)
            result = await api_request("post", f"/sessions/{validated.session_id}/run")

        elif name == "get_pipeline_status":
            validated = GetPipelineStatusInput(**arguments)
            result = await api_request("get", f"/sessions/{validated.session_id}/status")

        elif name == "get_requirements":
            validated = GetRequirementsInput(**arguments)
            params = {}
            if validated.filter_confidence_below is not None:
                params["filter_confidence_below"] = validated.filter_confidence_below
            result = await api_request(
                "get", f"/sessions/{validated.session_id}/requirements", params=params
            )

        elif name == "get_conflicts":
            validated = GetConflictsInput(**arguments)
            result = await api_request("get", f"/sessions/{validated.session_id}/conflicts")

        elif name == "approve_requirements":
            validated = ApproveRequirementsInput(**arguments)
            result = await api_request(
                "post",
                f"/sessions/{validated.session_id}/requirements/approve",
                json={"requirement_ids": validated.requirement_ids},
            )

        elif name == "get_work_items":
            validated = GetWorkItemsInput(**arguments)
            result = await api_request("get", f"/sessions/{validated.session_id}/workitems")

        elif name == "push_to_azure_devops":
            validated = PushToADOInput(**arguments)
            result = await api_request(
                "post",
                f"/sessions/{validated.session_id}/push",
                json=validated.model_dump(exclude_none=True),
            )

        elif name == "get_traceability":
            validated = GetTraceabilityInput(**arguments)
            params = {}
            if validated.requirement_id:
                params["requirement_id"] = validated.requirement_id
            result = await api_request(
                "get", f"/sessions/{validated.session_id}/graph", params=params
            )

        elif name == "get_session_report":
            validated = GetSessionReportInput(**arguments)
            result = await api_request("get", f"/sessions/{validated.session_id}/report")

        elif name == "list_sessions":
            result = await api_request("get", "/sessions/")

        elif name == "correct_requirement":
            validated = CorrectRequirementInput(**arguments)
            result = await api_request(
                "put",
                f"/sessions/{validated.session_id}/requirements/{validated.requirement_id}",
                json={"corrected_text": validated.corrected_text},
            )

        else:
            return [TextContent(type="text", text=f"Unknown tool: {name}")]

        return [TextContent(type="text", text=json.dumps(result, indent=2, default=str))]

    except Exception as e:
        return [TextContent(type="text", text=f"Error: {str(e)}")]


# ─── MCP Resources ────────────────────────────────────────────────────────────

@server.list_resources()
async def list_resources() -> list[Resource]:
    """Expose ReqBridge data as MCP resources."""
    return [
        Resource(
            uri="reqbridge://sessions",
            name="All Sessions",
            description="List of all ReqBridge sessions",
            mimeType="application/json",
        ),
    ]


@server.read_resource()
async def read_resource(uri: str) -> str:
    """Read a ReqBridge resource by URI."""
    if uri == "reqbridge://sessions":
        result = await api_request("get", "/sessions/")
        return json.dumps(result, indent=2, default=str)

    # Parse dynamic URIs: reqbridge://sessions/{id}/requirements
    parts = uri.replace("reqbridge://", "").split("/")
    if len(parts) >= 3 and parts[0] == "sessions":
        session_id = parts[1]
        resource_type = parts[2]

        endpoint_map = {
            "requirements": f"/sessions/{session_id}/requirements",
            "workitems": f"/sessions/{session_id}/workitems",
            "graph": f"/sessions/{session_id}/graph",
        }

        endpoint = endpoint_map.get(resource_type)
        if endpoint:
            result = await api_request("get", endpoint)
            return json.dumps(result, indent=2, default=str)

    return json.dumps({"error": f"Unknown resource: {uri}"})


# ─── MCP Prompts ──────────────────────────────────────────────────────────────

@server.list_prompts()
async def list_prompts() -> list[Prompt]:
    """Expose prompt templates for AI clients."""
    return [
        Prompt(
            name="summarize_session",
            description="Generate a summary of a ReqBridge session",
            arguments=[
                PromptArgument(name="session_id", description="The session ID to summarize", required=True),
            ],
        ),
        Prompt(
            name="review_conflicts",
            description="Guide the user through reviewing detected conflicts",
            arguments=[
                PromptArgument(name="session_id", description="The session ID with conflicts", required=True),
            ],
        ),
        Prompt(
            name="draft_acceptance_criteria",
            description="Draft acceptance criteria for a user story",
            arguments=[
                PromptArgument(name="story", description="The user story text", required=True),
            ],
        ),
    ]


@server.get_prompt()
async def get_prompt(name: str, arguments: dict | None = None) -> GetPromptResult:
    """Return prompt template content."""
    arguments = arguments or {}

    if name == "summarize_session":
        session_id = arguments.get("session_id", "")
        return GetPromptResult(
            description="Summarize a ReqBridge session",
            messages=[
                PromptMessage(
                    role="user",
                    content=TextContent(
                        type="text",
                        text=f"""Please summarize the ReqBridge session {session_id}.

Use the get_session_report tool to fetch the session data, then provide:
1. Overview of documents ingested
2. Key requirements extracted (top 5 by confidence)
3. Any conflicts detected
4. Work items generated
5. ADO push status
6. Recommendations for next steps""",
                    ),
                )
            ],
        )

    elif name == "review_conflicts":
        session_id = arguments.get("session_id", "")
        return GetPromptResult(
            description="Review conflicts in a session",
            messages=[
                PromptMessage(
                    role="user",
                    content=TextContent(
                        type="text",
                        text=f"""Review the conflicts detected in session {session_id}.

Use the get_conflicts tool to fetch all conflicts, then for each conflict:
1. Explain the contradiction in plain language
2. Assess severity and impact
3. Suggest a resolution
4. Ask me to approve/reject the suggested resolution

After reviewing all conflicts, help me approve the resolved requirements.""",
                    ),
                )
            ],
        )

    elif name == "draft_acceptance_criteria":
        story = arguments.get("story", "")
        return GetPromptResult(
            description="Draft acceptance criteria",
            messages=[
                PromptMessage(
                    role="user",
                    content=TextContent(
                        type="text",
                        text=f"""Draft acceptance criteria for this user story:

{story}

Write 3-5 acceptance criteria in Given/When/Then format.
Each criterion should be:
- Testable and unambiguous
- Cover positive path, negative path, and one edge case
- Specific enough for a QA engineer to write test scripts from""",
                    ),
                )
            ],
        )

    return GetPromptResult(description="Unknown prompt", messages=[])


# ─── Server Entry Point ───────────────────────────────────────────────────────

async def main():
    """Run the ReqBridge MCP server via stdio transport."""
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
