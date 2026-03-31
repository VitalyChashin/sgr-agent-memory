# Research: Memory Middleware for MCP Endpoint

**Feature Branch**: `192-mcp-memory-middleware`
**Date**: 2026-03-31

## R1: Integration Point within MCP ask Handler

**Decision**: Integrate memory directly inside the `ask()` tool handler in `mcp_server/server.py`, before `AgentFactory.create()` (preprocess) and after `agent.execute()` (postprocess/store). No new middleware layer or abstraction needed.

**Rationale**: The MCP `ask` handler is a single async function. The existing `MemoryMiddleware.preprocess()` and `MemoryMiddleware.postprocess()` methods can be called inline. This is simpler than the REST endpoint pattern because MCP execution is synchronous (await, not fire-and-forget).

**Alternatives considered**:
- **MCP-specific middleware class**: Over-engineering for a single call site. The `MemoryMiddleware` already encapsulates all logic. Rejected.
- **MCPPayloadProcessor**: These operate on outgoing MCP tool call payloads, not on the MCP server's incoming tool handler. Wrong abstraction layer. Rejected.

## R2: sessionId Source

**Decision**: Add `sessionId` as a direct optional parameter on the `ask` tool function signature (default: empty string). Additionally, extract it from the `McpFieldMapping` configuration if a mapping is configured. Direct parameter takes precedence.

**Rationale**: MCP clients control tool parameters directly. Adding `sessionId` to the tool signature makes it discoverable via the MCP tool schema. The `McpFieldMapping` fallback provides configuration-level flexibility for environments where clients can't modify tool parameters.

**Alternatives considered**:
- **Only via McpFieldMapping**: Would require config changes to enable memory; tool schema wouldn't advertise the capability. Rejected.
- **Only via direct parameter**: Would ignore the existing field mapping infrastructure. Rejected — supporting both is trivial.

## R3: Response Storage Pattern

**Decision**: Store the assistant response synchronously (inline) after `agent.execute()` completes, before returning the MCP response. No fire-and-forget needed.

**Rationale**: The MCP `ask` handler already awaits the full agent execution. The response is available immediately. Storing it inline adds minimal latency (one HTTP call to the memory service, within the configured timeout) and guarantees storage before the client receives the response (SC-003).

**Alternatives considered**:
- **Fire-and-forget (like REST)**: Unnecessary complexity for synchronous execution. The REST endpoint uses it because SSE streaming returns to the client before execution completes. MCP doesn't have this constraint. Rejected.

## R4: Memory Middleware Instance Access

**Decision**: Import `get_memory_middleware()` from `sgr_agent_core.server.endpoints` to access the already-initialized middleware instance. The MCP server starts after the FastAPI lifespan has initialized the middleware.

**Rationale**: The middleware is initialized once during server lifespan in `app.py` and stored as a module global in `endpoints.py`. The MCP server starts within the same lifespan, after middleware initialization. No separate initialization needed.

**Alternatives considered**:
- **Initialize a separate MemoryMiddleware for MCP**: Would create duplicate HTTP clients and connections. Wasteful. Rejected.
- **Pass middleware via config/closure**: Would require changing `create_mcp_server()` signature. Unnecessary when the module global is available. Rejected.
