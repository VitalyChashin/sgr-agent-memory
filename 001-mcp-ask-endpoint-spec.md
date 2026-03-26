# Spec: MCP Endpoint for SGR Agent — "ask" Tool

> **Feature:** 001-mcp-ask-endpoint  
> **Status:** Ready for Implementation  
> **Created:** 2026-03-25

---

## 1. Feature Description

Add an MCP (Model Context Protocol) endpoint to SGR Agent Core that exposes a single tool called `ask`. This tool wraps the existing `/v1/chat/completions` functionality but adds an extended payload structure — both on request and response — to support tracing and user context. For the initial implementation, the extended payload includes `traceId` and `userId` in the request, and `traceId` in the response. The payload schema is designed to be extensible for future complex JSON payloads without breaking changes.

The MCP endpoint allows external MCP clients (including other agents, orchestrators, and IDE integrations) to interact with SGR agents through the standard MCP protocol, while carrying additional metadata that the REST API alone does not provide.

---

## 2. User Stories

### US-1: MCP Client Sends a Research Query with Trace Context

**As** an MCP client (e.g., another agent or orchestrator),  
**I want** to call the SGR Agent's `ask` tool via MCP with a query, traceId, and userId,  
**So that** I receive a structured response with the agent's answer and the traceId echoed back for correlation.

**Acceptance Criteria:**
- The MCP server exposes exactly one tool: `ask`
- The `ask` tool accepts `query` (string, required), `traceId` (string, optional), and `userId` (string, optional)
- The tool internally invokes the same logic as `POST /v1/chat/completions` with `stream: false`
- The response is a JSON object containing `response` (string — the agent's text answer) and `traceId` (string — echoed from request or a default)
- If `traceId` is not provided, a hardcoded default value `"trace-default-001"` is used
- If `userId` is not provided, a hardcoded default value `"user-default-001"` is used

### US-2: MCP Server Starts Alongside the REST API

**As** a system operator,  
**I want** the MCP server to be configurable and start alongside (or instead of) the existing FastAPI server,  
**So that** I don't need separate deployment infrastructure for MCP access.

**Acceptance Criteria:**
- MCP server configuration lives in `config.yaml` under a new `mcp_server:` section
- The MCP server can be enabled/disabled via config (`mcp_server.enabled: true/false`)
- The MCP server transport is configurable (SSE by default, with host/port settings)
- The server starts automatically when the `sgr` CLI launches (if enabled)

### US-3: Extensible Payload Schema

**As** a developer extending the platform,  
**I want** the request and response payload schemas to be Pydantic models,  
**So that** I can add new fields in future without breaking existing clients.

**Acceptance Criteria:**
- Request payload is a Pydantic model (`AskRequest` or similar) with `query`, `traceId`, `userId`, and room for future fields
- Response payload is a Pydantic model (`AskResponse` or similar) with `response`, `traceId`, and room for future fields
- Both models use `model_config = ConfigDict(extra="allow")` or equivalent to permit unknown fields without validation errors

---

## 3. Functional Requirements

| ID | Requirement |
|----|-------------|
| FR-1 | The system exposes an MCP server using `fastmcp` that registers one tool: `ask` |
| FR-2 | The `ask` tool accepts parameters: `query` (str, required), `traceId` (str, optional, default `"trace-default-001"`), `userId` (str, optional, default `"user-default-001"`) |
| FR-3 | The `ask` tool creates an SGR agent instance (using `AgentFactory` or equivalent), executes the query via the agent's `execute()` method, and returns the result |
| FR-4 | The agent type used by the `ask` tool is configurable (default: the first agent defined in `agents.yaml`, or `sgr-tools-agent`) |
| FR-5 | The response is a JSON object: `{"response": "<agent output text>", "traceId": "<echoed or default>"}` |
| FR-6 | The MCP server configuration is added to `config.yaml` under `mcp_server:` with fields: `enabled`, `host`, `port`, `transport` |
| FR-7 | The MCP server is started by the existing `sgr` CLI entry point when `mcp_server.enabled` is `true` |
| FR-8 | Errors during agent execution are caught and returned as MCP tool errors (not server crashes) |

## 4. Non-Functional Requirements

| ID | Requirement |
|----|-------------|
| NFR-1 | The MCP endpoint must not degrade the performance of the existing REST API |
| NFR-2 | The MCP server must use async I/O consistently (no blocking calls) |
| NFR-3 | Request/response models must be Pydantic v2 models |

## 5. Edge Cases

| Case | Expected Behavior |
|------|-------------------|
| Empty `query` string | Return MCP tool error: "Query must not be empty" |
| Missing `traceId` | Use default `"trace-default-001"` |
| Missing `userId` | Use default `"user-default-001"` |
| Agent execution fails (LLM error, timeout) | Return MCP tool error with the exception message; do not crash the server |
| MCP server disabled in config | `sgr` CLI starts only the REST API; no MCP listener |
| Very long query | Pass through to agent; agent's own `max_iterations` and `max_tokens` limits apply |

## 6. Assumptions & Constraints

- The `fastmcp` library (already a dependency, ≥ 2.12.4) is used for the MCP server implementation
- The initial implementation is non-streaming (the `ask` tool returns a complete response). Streaming MCP support is out of scope for this feature
- The `traceId` and `userId` defaults are hardcoded constants for now — dynamic population is a future task
- The MCP server runs in the same process as the FastAPI server (using `asyncio` task or similar co-hosting)

---

# Plan: MCP Endpoint Implementation

## Constitution Compliance Check

| Principle | Status | Notes |
|-----------|--------|-------|
| P1: Two-Phase Architecture | ✅ | Uses existing agent execution; no new agent type |
| P2: Tool-Centric | ✅ | MCP `ask` is a thin wrapper, not a new agent capability |
| P3: OpenAI Compat | ✅ | Existing REST API untouched |
| P4: Config Cascades | ✅ | New `mcp_server:` section in GlobalConfig |
| P5: MCP Extension | ✅ | This IS the MCP extension |
| P7: Pydantic | ✅ | Request/response as Pydantic models |
| P8: Async-First | ✅ | All MCP handlers async |
| P9: Tests | ✅ | Tests included in tasks |
| P12: Stateful Agents | ✅ | Each `ask` call creates a fresh agent (stateless from MCP client perspective) |

## Architecture

```
MCP Client
    │
    ▼
┌─────────────────────────┐
│  fastmcp MCP Server     │  (SSE transport, same process as FastAPI)
│  Tool: "ask"            │
│  ├─ AskRequest (Pydantic)│
│  └─ AskResponse(Pydantic)│
└─────────┬───────────────┘
          │ creates agent via AgentFactory
          ▼
┌─────────────────────────┐
│  SGR Agent (existing)   │
│  execute() → result     │
└─────────────────────────┘
```

## Component Design

### New Files

| File | Purpose |
|------|---------|
| `sgr_agent_core/mcp_server/__init__.py` | Package init |
| `sgr_agent_core/mcp_server/server.py` | MCP server setup, tool registration |
| `sgr_agent_core/mcp_server/models.py` | `AskRequest`, `AskResponse` Pydantic models |
| `sgr_agent_core/mcp_server/config.py` | `MCPServerConfig` Pydantic model |
| `tests/test_mcp_server.py` | Tests for MCP server and ask tool |

### Modified Files

| File | Change |
|------|--------|
| `sgr_agent_core/server/server.py` (or main entry) | Add MCP server co-start logic |
| `config.yaml.example` | Add `mcp_server:` section |
| `sgr_agent_core/__init__.py` | Export `MCPServerConfig` if needed |

### Data Models

```python
# sgr_agent_core/mcp_server/models.py

from pydantic import BaseModel, ConfigDict


class AskRequest(BaseModel):
    """Request payload for the MCP ask tool."""
    model_config = ConfigDict(extra="allow")

    query: str
    traceId: str = "trace-default-001"
    userId: str = "user-default-001"


class AskResponse(BaseModel):
    """Response payload from the MCP ask tool."""
    model_config = ConfigDict(extra="allow")

    response: str
    traceId: str = "trace-default-001"
```

### MCP Server Config

```python
# sgr_agent_core/mcp_server/config.py

from pydantic import BaseModel


class MCPServerConfig(BaseModel):
    """Configuration for the MCP server."""
    enabled: bool = False
    host: str = "0.0.0.0"
    port: int = 8011
    transport: str = "sse"
    default_agent: str | None = None  # Agent definition name; None = use first available
```

### MCP Server Implementation (Sketch)

```python
# sgr_agent_core/mcp_server/server.py

from fastmcp import FastMCP
from sgr_agent_core.mcp_server.models import AskRequest, AskResponse

mcp = FastMCP("sgr-agent-mcp")


@mcp.tool()
async def ask(query: str, traceId: str = "trace-default-001", userId: str = "user-default-001") -> str:
    """
    Send a research query to an SGR Agent and receive a structured response.

    Args:
        query: The research question or task for the agent.
        traceId: Trace identifier for request correlation (default: trace-default-001).
        userId: User identifier for the request (default: user-default-001).

    Returns:
        JSON string with response text and traceId.
    """
    import json
    from sgr_agent_core import AgentFactory, GlobalConfig

    if not query.strip():
        raise ValueError("Query must not be empty")

    # Access global config (set during server startup)
    config = _get_global_config()
    agent_def_name = config.mcp_server.default_agent or next(iter(config.agents))
    agent_def = config.agents[agent_def_name]

    agent = await AgentFactory.create(
        agent_def=agent_def,
        task_messages=[{"role": "user", "content": query}],
    )

    result = await agent.execute()

    response = AskResponse(
        response=str(result),
        traceId=traceId,
    )
    return json.dumps(response.model_dump())
```

### Config YAML Addition

```yaml
# config.yaml — new section
mcp_server:
  enabled: true
  host: "0.0.0.0"
  port: 8011
  transport: "sse"
  default_agent: null  # Uses first agent from agents.yaml
```

---

# Tasks

> Tasks are ordered by dependency. Tasks marked `[P]` can be executed in parallel with others at the same dependency level.

## US-3: Extensible Payload Schema

### Task 1: Create Pydantic request/response models

**Description:** Create `sgr_agent_core/mcp_server/models.py` with `AskRequest` and `AskResponse` Pydantic models as specified in the data models section above.

**Files:**
- Create `sgr_agent_core/mcp_server/__init__.py`
- Create `sgr_agent_core/mcp_server/models.py`

**Dependencies:** None

**Acceptance Criteria:**
- `AskRequest` has fields: `query` (str, required), `traceId` (str, default `"trace-default-001"`), `userId` (str, default `"user-default-001"`)
- `AskResponse` has fields: `response` (str, required), `traceId` (str, default `"trace-default-001"`)
- Both models use `ConfigDict(extra="allow")`
- Models can be instantiated and serialized: `AskRequest(query="test").model_dump()` works
- Unknown extra fields are preserved: `AskRequest(query="test", custom_field="x").model_dump()` includes `custom_field`

---

### Task 2: Create MCP server config model `[P]`

**Description:** Create `sgr_agent_core/mcp_server/config.py` with `MCPServerConfig` Pydantic model.

**Files:**
- Create `sgr_agent_core/mcp_server/config.py`

**Dependencies:** Task 1 (package must exist)

**Acceptance Criteria:**
- `MCPServerConfig` has fields: `enabled` (bool, default `False`), `host` (str, default `"0.0.0.0"`), `port` (int, default `8011`), `transport` (str, default `"sse"`), `default_agent` (str | None, default `None`)
- Model validates correctly with partial input: `MCPServerConfig(enabled=True)` works

---

### Task 3: Integrate MCPServerConfig into GlobalConfig

**Description:** Add `mcp_server: MCPServerConfig` field to `GlobalConfig` (or the appropriate parent config class) so that the `mcp_server:` YAML section is parsed automatically.

**Files:**
- Modify the file containing `GlobalConfig` (likely `sgr_agent_core/config.py` or similar — verify actual location)
- Modify `config.yaml.example` to include the `mcp_server:` section with commented defaults

**Dependencies:** Task 2

**Acceptance Criteria:**
- `GlobalConfig.from_yaml("config.yaml")` correctly parses the `mcp_server:` section
- Missing `mcp_server:` section in YAML results in defaults (`enabled=False`)
- `config.yaml.example` includes the new section with documentation comments

---

## US-1: MCP Client Sends a Research Query

### Task 4: Implement MCP server with `ask` tool

**Description:** Create `sgr_agent_core/mcp_server/server.py` that sets up a `FastMCP` server instance and registers the `ask` tool. The tool should:

1. Validate that `query` is non-empty
2. Look up the configured agent definition (from `MCPServerConfig.default_agent` or first available)
3. Create an agent via `AgentFactory.create()`
4. Execute the agent and collect the result
5. Return an `AskResponse` serialized as JSON string

The module should expose a function `create_mcp_server(config: GlobalConfig) -> FastMCP` that sets up the server with access to the global config.

**Files:**
- Create `sgr_agent_core/mcp_server/server.py`

**Dependencies:** Task 1, Task 2, Task 3

**Acceptance Criteria:**
- `create_mcp_server()` returns a configured `FastMCP` instance
- The server lists exactly one tool: `ask`
- The `ask` tool's parameter schema matches `AskRequest` fields (query, traceId, userId with defaults)
- Empty query raises a `ValueError` with message "Query must not be empty"
- Successful execution returns a JSON string parseable as `AskResponse`
- `traceId` from request is echoed in response
- Agent execution errors are caught and re-raised as `ValueError` (or appropriate MCP error) with the original exception message

---

## US-2: MCP Server Co-Starts with REST API

### Task 5: Add MCP server startup to the `sgr` CLI entry point

**Description:** Modify the server startup logic so that when `mcp_server.enabled` is `True`, the MCP server starts as an async task alongside the FastAPI/Uvicorn server. When `mcp_server.enabled` is `False` (default), behavior is unchanged.

**Files:**
- Modify the server entry point (likely `sgr_agent_core/server/server.py` or `sgr_agent_core/cli/` — verify actual location)

**Dependencies:** Task 4

**Acceptance Criteria:**
- With `mcp_server.enabled: false` — server starts identically to current behavior
- With `mcp_server.enabled: true` — both FastAPI and MCP server run concurrently in the same process
- MCP server listens on the configured `host:port` (default `0.0.0.0:8011`)
- Shutdown of the main process cleanly stops both servers
- Server startup logs indicate MCP server status (enabled/disabled, host, port)

---

## Testing

### Task 6: Unit tests for models and config

**Description:** Write pytest tests for `AskRequest`, `AskResponse`, and `MCPServerConfig`.

**Files:**
- Create `tests/test_mcp_models.py`

**Dependencies:** Task 1, Task 2

**Acceptance Criteria:**
- Test default values for all models
- Test serialization/deserialization round-trip
- Test `extra="allow"` — unknown fields preserved
- Test validation errors (e.g., missing required `query`)
- All tests pass with `pytest tests/test_mcp_models.py`

---

### Task 7: Integration test for the `ask` tool `[P]`

**Description:** Write a pytest integration test that creates an MCP server, calls the `ask` tool programmatically, and verifies the response structure. Use a mock or minimal agent that returns a fixed string to avoid LLM dependency.

**Files:**
- Create `tests/test_mcp_server.py`

**Dependencies:** Task 4, Task 6

**Acceptance Criteria:**
- Test that the MCP server has exactly one tool registered ("ask")
- Test successful call returns valid `AskResponse` JSON with `response` and `traceId`
- Test that `traceId` is echoed from request
- Test that empty query returns an error
- Test that default `traceId` and `userId` are applied when omitted
- Tests use mocked agent execution (no real LLM calls)
- All tests pass with `pytest tests/test_mcp_server.py`

---

## Summary

| Task | Description | Dependencies | Parallelizable |
|------|-------------|--------------|----------------|
| 1 | Pydantic models (`AskRequest`, `AskResponse`) | — | — |
| 2 | MCP server config model | Task 1 | [P] with Task 1 |
| 3 | Integrate config into GlobalConfig | Task 2 | — |
| 4 | MCP server + `ask` tool implementation | Tasks 1, 2, 3 | — |
| 5 | Co-start MCP server with REST API | Task 4 | — |
| 6 | Unit tests for models/config | Tasks 1, 2 | [P] with Tasks 3, 4 |
| 7 | Integration test for `ask` tool | Tasks 4, 6 | [P] with Task 5 |
