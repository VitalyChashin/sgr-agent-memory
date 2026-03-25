# Implementation Plan: MCP Ask Endpoint

**Branch**: `185-mcp-ask-endpoint` | **Date**: 2026-03-25 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `/specs/185-mcp-ask-endpoint/spec.md`

## Summary

Add an MCP server endpoint to SGR Agent Core that exposes an `ask` tool, enabling MCP clients to query SGR agents with trace/user context. The server co-hosts with the existing FastAPI REST API in the same process, using `fastmcp` for the MCP protocol with SSE transport. Request/response payloads are extensible Pydantic models.

## Technical Context

**Language/Version**: Python 3.12 (target), 3.11 minimum
**Primary Dependencies**: `fastmcp` >= 2.12.4 (already installed), `pydantic` >= 2.0, `fastapi` >= 0.116.1, `uvicorn` >= 0.35.0
**Storage**: N/A (stateless per-request)
**Testing**: pytest with `asyncio_mode = "auto"`, mocked `AsyncOpenAI` client
**Target Platform**: Linux/Windows server
**Project Type**: Library/web-service (agentic framework with REST API)
**Performance Goals**: MCP response time equivalent to REST API call (~same agent execution time)
**Constraints**: Non-streaming, same-process co-hosting, async-only
**Scale/Scope**: Single MCP tool (`ask`), single new config section, ~5 new files

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Notes |
|-----------|--------|-------|
| P1: Two-Phase Agent Architecture | PASS | Uses existing agent execution via `AgentFactory.create()` → `agent.execute()`. No new agent type. |
| P2: Tool-Centric Capability Model | PASS | MCP `ask` is a thin wrapper exposing existing agent capabilities to MCP clients. Not a new agent tool. |
| P3: OpenAI API Compatibility | PASS | Existing REST API completely untouched. MCP endpoint is additive. |
| P4: Configuration Cascades | PASS | New `mcp_server: MCPServerConfig` field added to `GlobalConfig`. Follows existing config hierarchy. |
| P5: MCP as Extension Protocol | PASS | This IS the MCP extension — exposing SGR agents as an MCP server. |
| P6: Python >= 3.11, Target 3.12 | PASS | All new code uses Python 3.12 features (type hints, `str \| None`). |
| P7: Pydantic for All Data Contracts | PASS | `AskRequest`, `AskResponse`, `MCPServerConfig` are all Pydantic models. |
| P8: Async-First | PASS | MCP tool handler and server startup are fully async. |
| P9: Test Coverage | PASS | Unit tests for models/config, integration test for ask tool with mocked agent. |
| P10: Ruff for Linting | PASS | All new files formatted with Ruff (`line-length = 120`). |
| P11: SSE Streaming | N/A | Initial MCP implementation is non-streaming by design. Agent execution is awaited. |
| P12: Stateful Agents, Stateless API | PASS | Each `ask` call creates a fresh agent instance. No session state in MCP server. |
| P13: Backward-Compatible Versioning | PASS | New endpoint is purely additive. No existing API changes. |
| P14: Spec-Driven Development | PASS | Following full Spec Kit workflow. |
| P15: Registry Auto-Discovery | N/A | No new agent or tool types registered. Uses existing registries for agent lookup. |

**Gate result**: ALL PASS — no violations to justify.

## Project Structure

### Documentation (this feature)

```text
specs/185-mcp-ask-endpoint/
├── plan.md              # This file
├── spec.md              # Feature specification
├── research.md          # Phase 0: Research findings
├── data-model.md        # Phase 1: Data model
├── quickstart.md        # Phase 1: Usage guide
├── contracts/
│   └── mcp-ask-tool.md  # Phase 1: MCP tool contract
└── tasks.md             # Phase 2: Implementation tasks (via /speckit.tasks)
```

### Source Code (repository root)

```text
sgr_agent_core/
├── mcp_server/              # NEW package
│   ├── __init__.py          # Package init, exports
│   ├── models.py            # AskRequest, AskResponse Pydantic models
│   ├── config.py            # MCPServerConfig Pydantic model
│   └── server.py            # FastMCP server setup, ask tool registration
├── agent_config.py          # MODIFIED — add mcp_server field to GlobalConfig
├── server/
│   ├── __main__.py          # MODIFIED — co-start MCP server when enabled
│   └── app.py               # MODIFIED — MCP server lifecycle in lifespan

config.yaml.example          # MODIFIED — add mcp_server section

tests/
├── test_mcp_models.py       # NEW — unit tests for models and config
└── test_mcp_server.py       # NEW — integration test for ask tool
```

**Structure Decision**: New `sgr_agent_core/mcp_server/` package follows the existing pattern of feature-grouped modules (e.g., `sgr_agent_core/server/`, `sgr_agent_core/services/`). Tests go in the existing `tests/` directory matching the project convention.

## Complexity Tracking

No constitution violations to justify. All gates pass.

## Design Decisions

### D1: Package Location

New code lives in `sgr_agent_core/mcp_server/` as a self-contained package. This mirrors the existing `sgr_agent_core/server/` package for the REST API and keeps MCP server concerns separate from MCP client code (`sgr_agent_core/services/mcp_service.py`).

### D2: Config Field Naming

The new field is `mcp_server` (not nested under `mcp`). The existing `mcp: MCPConfig` field configures MCP *client* connections. The new field configures the MCP *server*. Clear naming prevents confusion.

### D3: Server Co-Hosting

The MCP server starts as an asyncio background task in the FastAPI lifespan context. This approach:
- Requires minimal changes to the entry point
- Shares the event loop with FastAPI
- Supports clean shutdown via lifespan exit

### D4: Agent Resolution

The `ask` tool resolves the agent at call time (not server startup). This means:
- Agent definitions can be loaded/modified after server start
- The first available agent is used if `default_agent` is not configured
- Agent creation uses the same `AgentFactory.create()` path as the REST API

### D5: Response Format

The `ask` tool returns a JSON string (not a dict) because FastMCP tool return values are serialized as text content. The JSON string contains an `AskResponse` model dump with `response` and `traceId` fields.
