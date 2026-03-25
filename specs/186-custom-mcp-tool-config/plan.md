# Implementation Plan: Custom MCP Tool Name and Description

**Branch**: `186-custom-mcp-tool-config` | **Date**: 2026-03-25 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `/specs/186-custom-mcp-tool-config/spec.md`

## Summary

Add `tool_name` and `tool_description` fields to `MCPServerConfig` so operators can customize the MCP tool's identity. The `create_mcp_server()` function reads these fields and passes them to the fastmcp `@mcp.tool()` decorator. Defaults preserve backward compatibility ("ask" name, existing description).

## Technical Context

**Language/Version**: Python 3.12 (target), 3.11 minimum
**Primary Dependencies**: `fastmcp` >= 2.12.4, `pydantic` >= 2.0
**Storage**: N/A
**Testing**: pytest with `asyncio_mode = "auto"`
**Target Platform**: Linux/Windows server
**Project Type**: Library/web-service
**Performance Goals**: N/A (config change only)
**Constraints**: Backward compatible with existing configs
**Scale/Scope**: 2 new fields on existing model, 1 modified function, ~10 lines of production code

## Constitution Check

| Principle | Status | Notes |
|-----------|--------|-------|
| P4: Configuration Cascades | PASS | New fields added to existing `MCPServerConfig` within `GlobalConfig` |
| P7: Pydantic for All Data Contracts | PASS | Fields are Pydantic model fields with defaults |
| P9: Test Coverage | PASS | Tests for new fields and custom tool registration |
| P10: Ruff | PASS | All code formatted |
| P13: Backward-Compatible | PASS | Both fields have defaults; omission preserves current behavior |

All other principles: N/A (no new agents, tools, endpoints, or streaming).

## Project Structure

### Source Code (repository root)

```text
sgr_agent_core/
├── mcp_server/
│   ├── config.py          # MODIFIED — add tool_name, tool_description fields
│   └── server.py          # MODIFIED — use config fields in @mcp.tool() registration

config.yaml.example        # MODIFIED — add tool_name, tool_description comments

tests/
├── test_mcp_models.py     # MODIFIED — add tests for new config fields
└── test_mcp_server.py     # MODIFIED — add tests for custom tool name/description
```

## Complexity Tracking

No violations. This is a minimal config extension.

## Design Decisions

### D1: FastMCP tool name override

The `@mcp.tool()` decorator accepts a `name` parameter: `@mcp.tool(name="custom")`. The tool function's Python name is irrelevant to MCP clients — only the registered name matters. For the description, fastmcp uses the function's docstring by default but also accepts a `description` parameter on `@mcp.tool()`.

### D2: Empty string handling

Empty `tool_name` or `tool_description` falls back to defaults. This is handled by a simple `or` expression: `config.mcp_server.tool_name or "ask"`.
