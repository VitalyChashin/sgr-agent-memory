# Quickstart: MCP Ask Endpoint

## Enable the MCP Server

Add `mcp_server` section to your `config.yaml`:

```yaml
mcp_server:
  enabled: true
  host: "0.0.0.0"
  port: 8011
```

## Start the Server

```bash
sgr --config-file config.yaml
```

Both the REST API (default port 8010) and MCP server (port 8011) will start.

## Test with an MCP Client

Any MCP-compatible client can connect to `http://localhost:8011/sse` and call the `ask` tool:

```json
{
  "tool": "ask",
  "arguments": {
    "query": "What are the latest developments in quantum computing?",
    "traceId": "my-trace-123",
    "userId": "user-456"
  }
}
```

**Response**:

```json
{
  "response": "The agent's research answer...",
  "traceId": "my-trace-123"
}
```

## Configuration Reference

| Setting | Default | Description |
|---------|---------|-------------|
| `mcp_server.enabled` | `false` | Enable/disable the MCP server |
| `mcp_server.host` | `"0.0.0.0"` | Host to bind |
| `mcp_server.port` | `8011` | Port to listen on |
| `mcp_server.transport` | `"sse"` | Transport protocol |
| `mcp_server.default_agent` | `null` | Agent to use (null = first available) |
