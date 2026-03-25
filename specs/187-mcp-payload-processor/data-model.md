# Data Model: MCP Payload Processor

**Date**: 2026-03-25

## New Entities

### MCPPayloadProcessor (Abstract)

Abstract base class for payload processors.

| Attribute | Type | Description |
|-----------|------|-------------|
| processor_config | dict[str, Any] | Arbitrary parameters from YAML config |

| Method | Signature | Description |
|--------|-----------|-------------|
| pre_call | async (payload, context, config, **kwargs) -> dict | Transform payload before MCP call |
| post_call | async (result, payload, context, config, **kwargs) -> str | Transform result after MCP call (optional, default: pass-through) |

### MCPPayloadProcessorChain

Ordered collection of processors.

| Attribute | Type | Description |
|-----------|------|-------------|
| processors | list[MCPPayloadProcessor] | Ordered list of processor instances |

| Method | Signature | Description |
|--------|-----------|-------------|
| run_pre_call | async (payload, context, config, **kwargs) -> dict | Execute pre_call on each processor in order |
| run_post_call | async (result, payload, context, config, **kwargs) -> str | Execute post_call in reverse order |

### PayloadProcessorDefinition (Config Model)

YAML configuration entry for a single processor.

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| class | string | yes | — | Processor class name or import string |
| config | dict[str, Any] | no | {} | Arbitrary processor-specific parameters |
| managed_fields | list[string] | no | [] | Fields hidden from LLM schema |

### ProcessorRegistry

Auto-registration registry for processor classes, following `ToolRegistry` pattern.

## Modified Entities

### AgentContext (extended)

| Field | Type | Default | Status |
|-------|------|---------|--------|
| request_metadata | dict[str, Any] | {} | **NEW** |

All existing fields unchanged.

### MCPBaseTool (extended)

| ClassVar | Type | Default | Status |
|----------|------|---------|--------|
| _client | Client / None | None | Existing |
| _processor_chain | MCPPayloadProcessorChain / None | None | **NEW** |
| _managed_fields | list[str] | [] | **NEW** |

## Built-in Processors

### TraceContextProcessor

Injects `traceId` into the payload from `context.request_metadata["traceId"]` or a configured default.

| Config Key | Type | Default | Description |
|------------|------|---------|-------------|
| default_trace_id | string | auto-generated | Fallback trace ID |

### AuthContextProcessor

Injects `userId` into the payload from `context.request_metadata["userId"]` or a configured default.

| Config Key | Type | Default | Description |
|------------|------|---------|-------------|
| default_user_id | string | "user-default-001" | Fallback user ID |

## Relationships

```
config.yaml
└── mcp.mcpServers.<name>.payload_processors: list[PayloadProcessorDefinition]
        │
        ▼ (resolved at startup by MCP2ToolConverter)
    MCPPayloadProcessorChain
        │
        ▼ (attached as ClassVar)
    MCPBaseTool._processor_chain
        │
        ▼ (invoked during __call__)
    pre_call(payload, context, config) → enriched payload → client.call_tool()
                                                                    │
                                                              post_call(result) → final result

AgentFactory.create(request_metadata={...})
    └── agent._context.request_metadata = {...}
            │
            └── read by processors via context.request_metadata.get("key")
```
