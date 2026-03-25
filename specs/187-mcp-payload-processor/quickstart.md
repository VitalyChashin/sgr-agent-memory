# Quickstart: MCP Payload Processor

## Configure processors on an MCP server connection

```yaml
# config.yaml
mcp:
  mcpServers:
    agent_b:
      url: "http://localhost:8011/sse"
      payload_processors:
        - class: "TraceContextProcessor"
          managed_fields: ["traceId"]
          config:
            default_trace_id: "trace-system-001"
        - class: "AuthContextProcessor"
          managed_fields: ["userId"]
          config:
            default_user_id: "user-system-001"
```

## How it works

1. Agent A calls an MCP tool on `agent_b`
2. The LLM fills `query` (and default `traceId`/`userId`)
3. **TraceContextProcessor** overwrites `traceId` with the value from request metadata (or the configured default)
4. **AuthContextProcessor** overwrites `userId` from request metadata (or default)
5. The enriched payload is sent to Agent B via MCP
6. `traceId` and `userId` fields are hidden from the LLM schema (declared as `managed_fields`)

## Writing a custom processor

```python
from sgr_agent_core.mcp_payload_processor import MCPPayloadProcessor

class BillingTagProcessor(MCPPayloadProcessor):
    async def pre_call(self, payload, context, config, **kwargs):
        payload["billingTag"] = self.processor_config.get("tag", "default")
        return payload
```

Configure in YAML:
```yaml
payload_processors:
  - class: "myproject.processors.BillingTagProcessor"
    config:
      tag: "team-alpha"
```
