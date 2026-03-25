# Quickstart: Custom MCP Tool Name and Description

## Configure custom tool identity

Add `tool_name` and `tool_description` to your `config.yaml`:

```yaml
mcp_server:
  enabled: true
  port: 8011
  tool_name: "research"
  tool_description: "Perform deep technical research on a given topic using SGR agents."
```

## Start and verify

```bash
sgr --config-file config.yaml
```

Connect any MCP client to `http://localhost:8011/sse`. The tool will appear as `research` with the custom description.

## Configuration Reference

| Setting | Default | Description |
|---------|---------|-------------|
| `mcp_server.tool_name` | `"ask"` | Custom name for the exposed MCP tool |
| `mcp_server.tool_description` | (built-in default) | Custom description shown to MCP clients |
