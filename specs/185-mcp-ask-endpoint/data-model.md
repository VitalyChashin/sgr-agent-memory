# Data Model: MCP Ask Endpoint

**Date**: 2026-03-25
**Feature**: 185-mcp-ask-endpoint

## Entities

### AskRequest

Represents the input payload for the MCP `ask` tool.

| Field   | Type   | Required | Default               | Description                                |
|---------|--------|----------|-----------------------|--------------------------------------------|
| query   | string | yes      | —                     | The research question or task for the agent |
| traceId | string | no       | `"trace-default-001"` | Trace identifier for request correlation   |
| userId  | string | no       | `"user-default-001"`  | User identifier for the request            |

**Extensibility**: Accepts and preserves additional unknown fields without validation errors.

**Validation rules**:
- `query` must not be empty or whitespace-only

### AskResponse

Represents the output payload from the MCP `ask` tool.

| Field    | Type   | Required | Default               | Description                              |
|----------|--------|----------|-----------------------|------------------------------------------|
| response | string | yes      | —                     | The agent's text answer                  |
| traceId  | string | no       | `"trace-default-001"` | Echoed trace identifier from the request |

**Extensibility**: Accepts and preserves additional unknown fields without validation errors.

### MCPServerConfig

Represents operational configuration for the MCP server.

| Field         | Type         | Required | Default     | Description                                        |
|---------------|--------------|----------|-------------|----------------------------------------------------|
| enabled       | boolean      | no       | `false`     | Whether the MCP server starts with the system      |
| host          | string       | no       | `"0.0.0.0"` | Network host to bind the MCP server                |
| port          | integer      | no       | `8011`      | Network port for the MCP server                    |
| transport     | string       | no       | `"sse"`     | Transport protocol (SSE)                           |
| default_agent | string/null  | no       | `null`      | Agent definition name; null = use first available  |

## Relationships

```
GlobalConfig
├── mcp: MCPConfig           (existing — MCP client connections)
├── mcp_server: MCPServerConfig  (NEW — MCP server configuration)
└── agents: dict[str, AgentDefinition]
                 │
                 └── Referenced by MCPServerConfig.default_agent (by name)

AskRequest → AgentFactory.create() → BaseAgent.execute() → AskResponse
```

## State Transitions

The MCP ask tool has no persistent state. Each call follows:

```
Idle → Received Request → Validate Query → Create Agent → Execute Agent → Return Response → Idle
                              │                                │
                              ▼                                ▼
                         Error (empty query)            Error (agent failure)
```
