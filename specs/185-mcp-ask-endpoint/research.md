# Research: MCP Ask Endpoint

**Date**: 2026-03-25
**Feature**: 185-mcp-ask-endpoint

## R1: FastMCP Server Integration Pattern

**Decision**: Use `fastmcp.FastMCP` to create a standalone MCP server instance with SSE transport, co-hosted in the same process as the FastAPI server via `asyncio`.

**Rationale**: The project already depends on `fastmcp >= 2.12.4` for MCP client functionality (`MCP2ToolConverter`). FastMCP provides both client and server capabilities. Using the same library avoids new dependencies and aligns with constitution principle P5 (MCP as the Extension Protocol).

**Alternatives considered**:
- Separate MCP server process: Rejected — adds deployment complexity, violates assumption of same-process co-hosting.
- Custom MCP protocol implementation: Rejected — unnecessary when `fastmcp` provides a complete server implementation.

## R2: Co-Hosting FastAPI and FastMCP

**Decision**: Start the MCP server as an `asyncio.Task` within the FastAPI lifespan context manager. The FastMCP SSE transport runs its own ASGI app on a separate port.

**Rationale**: The existing server entry point (`sgr_agent_core/server/__main__.py`) uses `uvicorn.run()` which blocks. The FastAPI app already has a lifespan context manager (`server/app.py:17-34`). The MCP server can be started as a background task during lifespan startup and cancelled during shutdown. FastMCP's `run_sse_async()` method supports this pattern.

**Alternatives considered**:
- Mount MCP as sub-application on same port: Rejected — SSE transport requires its own ASGI handler; mixing would complicate routing.
- Separate CLI command for MCP server: Rejected — violates US-2 requirement of co-starting with REST API.

## R3: Agent Creation for MCP Ask Tool

**Decision**: Use `AgentFactory.create(agent_def, task_messages)` directly, the same path used by the REST API's `POST /v1/chat/completions` endpoint.

**Rationale**: The REST endpoint (`server/endpoints.py:179-220`) already demonstrates the pattern: resolve agent definition by name from `GlobalConfig().agents`, create agent via factory, execute, and return result. The MCP ask tool follows the identical flow but returns a structured JSON response instead of streaming SSE.

**Alternatives considered**:
- Direct BaseAgent instantiation: Rejected — bypasses tool resolution, MCP tool conversion, and config merging that `AgentFactory.create()` handles.

## R4: Configuration Integration

**Decision**: Add a new `MCPServerConfig` Pydantic model and integrate it into `GlobalConfig` as a new field `mcp_server: MCPServerConfig`. This is separate from the existing `mcp: MCPConfig` field (which configures MCP *client* connections).

**Rationale**: The existing `mcp` field in `AgentConfig` configures MCP client connections to external MCP servers. The new `mcp_server` field configures SGR's own MCP server endpoint. These are conceptually distinct: one is "which MCP servers do I connect to" vs. "how do I serve MCP clients." Constitution principle P4 (Configuration Cascades) requires new features to integrate into the existing config hierarchy.

**Alternatives considered**:
- Nest under existing `mcp:` section: Rejected — confuses client and server configuration; the existing `MCPConfig` from `fastmcp` has a fixed schema.
- Separate config file: Rejected — violates P4 configuration cascade principle.

## R5: Agent Selection for Ask Tool

**Decision**: Use `MCPServerConfig.default_agent` to specify the agent name. If `None`, use `next(iter(GlobalConfig().agents))` to get the first defined agent.

**Rationale**: The REST API uses the `model` field in the request to select an agent. For MCP, the agent is server-configured rather than client-selected (the MCP client calls `ask`, not a specific agent). Using the first available agent is a reasonable default that mirrors how single-agent deployments typically work.

**Alternatives considered**:
- Client-selectable agent via extra parameter: Deferred — could be added later via the extensible payload without breaking changes.

## R6: Error Handling Pattern

**Decision**: Catch all exceptions during agent execution and raise `ValueError` with the original exception message. FastMCP converts raised exceptions to MCP tool error responses automatically.

**Rationale**: The existing REST API creates tasks and streams results, handling errors via SSE events. The MCP tool is synchronous (from the tool perspective) — it awaits the full result. FastMCP's `@mcp.tool()` decorator handles exception-to-error conversion.

**Alternatives considered**:
- Return error in response payload: Rejected — MCP protocol has a standard error mechanism; using it is more idiomatic.
