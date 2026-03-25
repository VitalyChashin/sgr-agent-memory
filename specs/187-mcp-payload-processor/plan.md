# Implementation Plan: MCP Payload Processor

**Branch**: `187-mcp-payload-processor` | **Date**: 2026-03-25 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `/specs/187-mcp-payload-processor/spec.md`

## Summary

Introduce an MCPPayloadProcessor middleware layer between the LLM-generated MCP tool payload and the actual MCP wire call. Processors form an ordered chain that transforms payloads before sending (pre_call) and responses after receiving (post_call). They are configured per MCP server in YAML and can inject request-scoped metadata (trace IDs, user identity) without LLM involvement. The pattern follows existing registry auto-discovery and config cascade conventions.

## Technical Context

**Language/Version**: Python 3.12 (target), 3.11 minimum
**Primary Dependencies**: `fastmcp` >= 2.12.4, `pydantic` >= 2.0, `jambo` (JSON schema → Pydantic)
**Storage**: N/A
**Testing**: pytest with `asyncio_mode = "auto"`
**Target Platform**: Linux/Windows server
**Project Type**: Library/web-service (agentic framework)
**Performance Goals**: Processor chain overhead < 1ms per call (in-process dict transforms)
**Constraints**: Backward compatible; no changes when no processors configured
**Scale/Scope**: ~6 new files, ~4 modified files, 2 built-in processors

## Constitution Check

| Principle | Status | Notes |
|-----------|--------|-------|
| P1: Two-Phase Agent Architecture | PASS | No change to agent phases. Processors run within the existing action phase. |
| P2: Tool-Centric Capability | PASS | MCPBaseTool extended with optional processor hook. No new tool type. |
| P3: OpenAI API Compat | PASS | REST API untouched. New `request_metadata` param is optional. |
| P4: Configuration Cascades | PASS | Processor config lives under `mcp.mcpServers.<name>.payload_processors`. Agent-level overrides supported. |
| P5: MCP as Extension Protocol | PASS | Enhances MCP integration, not an alternative. |
| P7: Pydantic for Data Contracts | PASS | `MCPPayloadProcessor` ABC, `PayloadProcessorDefinition` Pydantic model, `AgentContext` extended with Pydantic field. |
| P8: Async-First | PASS | `pre_call` and `post_call` are async methods. |
| P9: Test Coverage | PASS | Unit tests for chain, integration tests for end-to-end payload injection. |
| P10: Ruff | PASS | All files formatted. |
| P13: Backward Compatible | PASS | No processors configured = identical behavior. |
| P15: Registry Auto-Discovery | PASS | `ProcessorRegistry` with `__init_subclass__` auto-registration, same pattern as `ToolRegistry`. |

## Project Structure

### Source Code (repository root)

```text
sgr_agent_core/
├── mcp_payload_processor.py       # NEW — ABC, chain, ProcessorRegistry
├── processors/                    # NEW package
│   ├── __init__.py                # Exports built-in processors
│   ├── trace_context.py           # TraceContextProcessor
│   └── auth_context.py            # AuthContextProcessor
├── base_tool.py                   # MODIFIED — MCPBaseTool invokes processor chain
├── models.py                      # MODIFIED — AgentContext gains request_metadata field
├── agent_factory.py               # MODIFIED — accept and propagate request_metadata
├── services/
│   └── mcp_service.py             # MODIFIED — build processor chains, attach to tool classes
├── mcp_server/
│   └── server.py                  # MODIFIED — propagate traceId/userId to request_metadata
├── server/
│   └── endpoints.py               # MODIFIED — extract request metadata from headers

config.yaml.example                # MODIFIED — add payload_processors example

tests/
├── test_payload_processor.py      # NEW — unit tests for ABC, chain, registry
├── test_builtin_processors.py     # NEW — tests for trace/auth processors
└── test_mcp_server.py             # MODIFIED — integration test for metadata propagation
```

## Design Decisions

### D1: Processor as ABC, not Pydantic model

The processor is an abstract class with `pre_call`/`post_call` methods, not a Pydantic model. This keeps the interface simple and avoids schema complexity. Processor config is a plain dict passed via constructor.

### D2: Chain attached as ClassVar on MCPBaseTool

`_processor_chain: ClassVar[MCPPayloadProcessorChain | None] = None` follows the existing `_client: ClassVar[Client | None]` pattern. Built once during `build_tools_from_mcp()`, shared across all calls.

### D3: request_metadata as dict on AgentContext

Using `request_metadata: dict[str, Any] = Field(default_factory=dict)` rather than typed fields keeps the core model stable while allowing any metadata key. Processors access it via `context.request_metadata.get("traceId")`.

### D4: ProcessorRegistry follows ToolRegistry pattern

Auto-registration via `__init_subclass__`. Built-in processors register by import in `processors/__init__.py`. Custom processors via import strings in YAML config.

### D5: Managed fields via schema override

Processors declare `managed_fields` in config. During `_prepare_tools()`, MCPBaseTool overrides its JSON schema to exclude those fields. The Pydantic model retains them for processor population.

### D6: MCPConfig extension for payload_processors

`fastmcp.MCPConfig` uses `extra="allow"` so adding `payload_processors` to individual server entries in YAML is accepted without modifying the external library's model. The processor definitions are extracted by our code during tool building.

## Complexity Tracking

No constitution violations. All gates pass.
