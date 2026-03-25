# MCP Tool Contract: `ask`

**Protocol**: MCP (Model Context Protocol)
**Transport**: SSE (Server-Sent Events)
**Default endpoint**: `http://{host}:{port}/sse` (default `0.0.0.0:8011`)

## Tool Definition

**Name**: `ask`
**Description**: Send a research query to an SGR Agent and receive a structured response.

### Input Schema

```json
{
  "type": "object",
  "properties": {
    "query": {
      "type": "string",
      "description": "The research question or task for the agent."
    },
    "traceId": {
      "type": "string",
      "description": "Trace identifier for request correlation.",
      "default": "trace-default-001"
    },
    "userId": {
      "type": "string",
      "description": "User identifier for the request.",
      "default": "user-default-001"
    }
  },
  "required": ["query"],
  "additionalProperties": true
}
```

### Output Format

On success, returns a JSON string:

```json
{
  "response": "The agent's text answer to the query.",
  "traceId": "echoed-trace-id"
}
```

### Error Cases

| Condition | Error Type | Message |
|-----------|-----------|---------|
| Empty or whitespace-only query | Tool error | "Query must not be empty" |
| Agent execution failure | Tool error | Original exception message |

## Configuration Contract

Added to `config.yaml` under `mcp_server:`:

```yaml
mcp_server:
  enabled: true          # default: false
  host: "0.0.0.0"       # default: "0.0.0.0"
  port: 8011             # default: 8011
  transport: "sse"       # default: "sse"
  default_agent: null    # default: null (uses first available agent)
```

## Behavioral Contract

1. The MCP server starts only when `mcp_server.enabled` is `true`
2. Each `ask` call creates a fresh agent instance (no session continuity)
3. The `traceId` from the request is always echoed in the response
4. Agent execution is non-streaming — the tool blocks until completion
5. The MCP server does not affect the REST API's operation or performance
